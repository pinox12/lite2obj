"""Aplicación de escritorio de lite2obj.

Todo el flujo (abrir un esquemático, ajustar el render, previsualizarlo en 3D y exportarlo
a OBJ) ocurre en una única ventana Qt. El renderizado 3D se hace en Python con el OpenGL
nativo de Qt (:class:`QOpenGLWidget`). No se buscan archivos en el disco: los esquemáticos
se abren con el diálogo del sistema, los resource packs se añaden a mano y las texturas
salen del archivo ``.l2oassets``.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QLibraryInfo,
    QObject,
    QRunnable,
    QSettings,
    Qt,
    QThreadPool,
    QTimer,
    QTranslator,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPalette, QSurfaceFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import assetpack, assets, associations, i18n, litematic, paths
from ..assets import ResourceManager
from ..export import ExportOptions, ExportResult, export_obj
from ..i18n import _
from ..mesh import Mesh, MeshBuilder
from ..models import ModelResolver
from ..pipeline import RenderOptions, build_resources
from .viewport import Viewport

__all__ = ["MainWindow", "main", "run"]

#: Traductores de Qt instalados (la lista evita que el recolector los libere).
_QT_TRANSLATORS: list[QTranslator] = []


def _litematic_filter() -> str:
    return _("Litematica (*.litematic);;Todos los archivos (*)")


def _png_filter() -> str:
    return _("PNG (*.png);;Todos los archivos (*)")


def _asset_pack_filter() -> str:
    return _("Assets de lite2obj (*.l2oassets);;Todos los archivos (*)")


def _jar_filter() -> str:
    return _("Minecraft client (*.jar);;Todos los archivos (*)")


def _pack_filter() -> str:
    return _("Resource pack (*.zip);;Todos los archivos (*)")


_DEMO_SCHEMATIC_NAME = "all_blocks_and_entities.litematic"


def _app_icon_path() -> Path | None:
    """PNG del icono, dentro del bundle o en el repositorio durante el desarrollo."""
    bundle = getattr(sys, "_MEIPASS", None)
    candidates: list[Path] = []
    if bundle:
        candidates.append(Path(bundle) / "lite2obj.png")
    candidates.append(Path(__file__).resolve().parents[3] / "packaging" / "lite2obj.png")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _app_icon() -> QIcon:
    path = _app_icon_path()
    return QIcon(str(path)) if path is not None else QIcon()


def _is_resource_pack(path: Path) -> bool:
    """Un pack es un ``.zip`` o una carpeta con una subcarpeta ``assets``."""
    if path.is_file():
        return path.suffix.lower() == ".zip"
    return path.is_dir() and (path / "assets").is_dir()


def _resource_packs_in(path: Path) -> list[Path]:
    """Packs que hay en ``path``: el propio pack o sus hijos inmediatos.

    Permite elegir una carpeta que ya es un resource pack o una carpeta que contiene
    varios (por ejemplo, la carpeta ``resourcepacks`` de un perfil de Modrinth/Prism).
    No recorre subcarpetas más allá del primer nivel.
    """
    if _is_resource_pack(path):
        return [path]
    if not path.is_dir():
        return []
    try:
        entries = list(path.iterdir())
    except OSError:
        return []
    return sorted(
        (entry for entry in entries if _is_resource_pack(entry)),
        key=lambda entry: entry.name.lower(),
    )


def _asset_status(
    pack: Path | None,
    missing: Sequence[str] = (),
    missing_count: int = 0,
) -> tuple[str, str, str]:
    """Estado, detalle y color del archivo de assets base de Minecraft.

    Devuelve ``(texto de estado, detalle, color)``. El detalle incluye la versión de
    Minecraft con la que se generó el archivo. Si el último render encontró bloques que no
    están en los assets (``missing``), el check pasa a ser un aviso de actualización.
    """
    if pack is None:
        return (
            _("✗  Assets base de Minecraft no configurados"),
            _("Genera el archivo .l2oassets desde un client jar."),
            "#e8b45a",
        )
    if not assetpack.is_asset_pack(pack):
        return (
            _("✗  El archivo de assets no es válido"),
            _("{name} · vuelve a generarlo desde un client jar.").format(name=pack.name),
            "#e8b45a",
        )
    manifest = assetpack.read_manifest(pack) or {}
    version = manifest.get("minecraft_version") or _("desconocida")
    detail = _("Generados desde Minecraft {version} · {name}").format(
        version=version, name=pack.name
    )
    if missing:
        shown = ", ".join(missing[:5])
        if len(missing) > 5:
            shown += " " + _("y {count} más").format(count=len(missing) - 5)
        count = missing_count or len(missing)
        return (
            _("⚠  Hay bloques que no están en los assets"),
            _(
                "{detail}\n{count:,} bloque(s) omitidos: {shown}. "
                "Actualiza el .l2oassets con un client jar más reciente."
            ).format(detail=detail, count=count, shown=shown),
            "#e8b45a",
        )
    return (_("✓  Assets base de Minecraft configurados"), detail, "#58c27d")


def _demo_schematic_path() -> Path:
    """Ruta del esquemático de prueba incluido en el repositorio o en el bundle."""
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        # PyInstaller deja los ``datas`` en la raíz de ``_MEIPASS``.
        candidates.append(Path(bundle) / _DEMO_SCHEMATIC_NAME)
    candidates.extend(
        (
            Path(__file__).resolve().parents[3] / _DEMO_SCHEMATIC_NAME,
            Path.cwd() / _DEMO_SCHEMATIC_NAME,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


class _Signals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


class RenderTask(QRunnable):
    """Construye la malla en segundo plano para no bloquear la interfaz."""

    def __init__(
        self,
        path: Path,
        options: RenderOptions,
        resources=None,
    ) -> None:
        super().__init__()
        self.path = path
        self.options = options
        self.resources = resources
        self.signals = _Signals()

    def run(self) -> None:  # pragma: no cover - se prueba desde la GUI
        try:
            document = litematic.load(self.path)
            resources = self.resources or build_resources(self.options)
            mesh_options = self.options.mesh_options()
            mesh_options.progress = lambda done, total: self.signals.progress.emit(done, total)
            resolver = ModelResolver(resources, fallback_mode=self.options.fallback_mode)
            mesh = MeshBuilder(resolver, options=mesh_options).build(document)
            self.signals.finished.emit(mesh)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class ExportTask(QRunnable):
    """Escribe OBJ + MTL + atlas sin bloquear la interfaz."""

    def __init__(self, mesh: Mesh, directory: Path, options: ExportOptions) -> None:
        super().__init__()
        self.mesh = mesh
        self.directory = directory
        self.options = options
        self.signals = _Signals()

    def run(self) -> None:  # pragma: no cover - se prueba desde la GUI
        try:
            result = export_obj(self.mesh, self.directory, self.options)
            self.signals.finished.emit(result)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class AssetPackTask(QRunnable):
    """Genera el archivo .l2oassets sin bloquear la interfaz."""

    def __init__(self, jar: Path, output: Path) -> None:
        super().__init__()
        self.jar = jar
        self.output = output
        self.signals = _Signals()

    def run(self) -> None:  # pragma: no cover - se prueba desde la GUI
        try:
            info = assetpack.build_asset_pack(self.jar, self.output, force=True)
            self.signals.finished.emit(info)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    """Ventana principal: panel de opciones a la izquierda y viewport a la derecha."""

    def __init__(self, initial: Path | None = None) -> None:
        super().__init__()
        i18n.configure()
        self.settings = QSettings("lite2obj", "lite2obj")
        self.setWindowTitle("lite2obj")
        self.resize(1360, 860)
        self.setMinimumSize(980, 640)
        self.setAcceptDrops(True)

        self.path: Path | None = None
        self.doc: litematic.Litematic | None = None
        self.mesh: Mesh | None = None
        self._mesh_key: tuple | None = None
        self._resources: ResourceManager | None = None
        self._resources_key: tuple[str, ...] | None = None
        self._dirty = False
        self._busy = False
        self._export_after_render = False
        self._task: Any = None
        self._task_key: tuple | None = None
        self._task_packs: tuple[str, ...] | None = None
        self._render_started = 0.0
        self._packs_loading = False
        self._layers_guard = False
        self._pool = QThreadPool.globalInstance()

        self._build_ui()
        self._load_packs()
        self._update_assets_label()

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter_state = self.settings.value("splitter")
        if splitter_state is not None:
            self.splitter.restoreState(splitter_state)

        self.viewport.failed.connect(self._on_gl_error)
        if initial is not None:
            self.open_path(Path(initial))
        elif self._asset_pack_path() is None:
            QTimer.singleShot(0, self._show_missing_assets)

    # -- construcción de la interfaz --------------------------------------- #

    def _build_ui(self) -> None:
        self.viewport = Viewport(self)
        self.viewport.set_background(QColor("#171a20"))

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self._build_panel())
        self.splitter.addWidget(self.viewport)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([390, 970])
        self.setCentralWidget(self.splitter)

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_status()
        self._connect_signals()

    def _build_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumWidth(340)
        scroll.setMaximumWidth(460)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Esquemático
        schematic_group = QGroupBox(_("Esquemático"))
        schematic_layout = QVBoxLayout(schematic_group)
        hint = QLabel(_("Abre un .litematic o arrástralo sobre la ventana."))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        schematic_layout.addWidget(hint)
        open_row = QHBoxLayout()
        self.open_button = QPushButton(_("Abrir…"))
        self.reload_button = QPushButton(_("Recargar"))
        self.reload_button.setToolTip(_("Vuelve a leer el esquemático actual"))
        open_row.addWidget(self.open_button)
        open_row.addWidget(self.reload_button)
        schematic_layout.addLayout(open_row)
        self.info_label = QLabel(_("Ningún esquemático abierto."))
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        schematic_layout.addWidget(self.info_label)
        layout.addWidget(schematic_group)

        # Assets
        assets_group = QGroupBox(_("Assets de Minecraft"))
        assets_layout = QVBoxLayout(assets_group)
        self.assets_status = QLabel()
        self.assets_status.setWordWrap(True)
        assets_layout.addWidget(self.assets_status)
        self.assets_label = QLabel()
        self.assets_label.setWordWrap(True)
        self.assets_label.setObjectName("hint")
        assets_layout.addWidget(self.assets_label)
        self.assets_button = QPushButton(_("Generar archivo de assets…"))
        self.assets_button.setToolTip(_("Elige el client jar de Minecraft y genera el .l2oassets"))
        assets_layout.addWidget(self.assets_button)
        layout.addWidget(assets_group)

        # Resource packs (sólo los que añada el usuario; no se buscan en el disco)
        packs_group = QGroupBox("Resource packs")
        packs_layout = QVBoxLayout(packs_group)
        packs_hint = QLabel(
            _(
                "Opcional. Arriba = mayor prioridad. Los assets que falten se toman del "
                "archivo vanilla. Puedes añadir varios .zip a la vez o una carpeta con varios "
                "packs."
            )
        )
        packs_hint.setWordWrap(True)
        packs_hint.setObjectName("hint")
        packs_layout.addWidget(packs_hint)
        self.pack_list = QListWidget()
        self.pack_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pack_list.setFixedHeight(140)
        self.pack_list.setToolTip(_("Marca los packs que quieres aplicar al render"))
        packs_layout.addWidget(self.pack_list)
        add_row = QHBoxLayout()
        self.pack_add_file = QPushButton(_("Añadir .zip…"))
        self.pack_add_file.setToolTip(_("Añade uno o varios resource packs comprimidos"))
        self.pack_add_dir = QPushButton(_("Añadir carpeta…"))
        self.pack_add_dir.setToolTip(_("Añade un pack o todos los que contenga una carpeta"))
        add_row.addWidget(self.pack_add_file)
        add_row.addWidget(self.pack_add_dir)
        packs_layout.addLayout(add_row)
        move_row = QHBoxLayout()
        self.pack_up = QPushButton(_("Subir"))
        self.pack_down = QPushButton(_("Bajar"))
        self.pack_remove = QPushButton(_("Quitar"))
        move_row.addWidget(self.pack_up)
        move_row.addWidget(self.pack_down)
        move_row.addWidget(self.pack_remove)
        packs_layout.addLayout(move_row)
        layout.addWidget(packs_group)

        self.dirty_label = QLabel(_("Hay cambios sin aplicar. Pulsa Renderizar (F5)."))
        self.dirty_label.setObjectName("warning")
        self.dirty_label.setWordWrap(True)
        self.dirty_label.hide()
        layout.addWidget(self.dirty_label)

        self.render_button = QPushButton(_("Renderizar"))
        self.render_button.setToolTip(_("Vuelve a generar la malla con las opciones actuales (F5)"))
        layout.addWidget(self.render_button)

        # Vista
        view_group = QGroupBox(_("Vista y render"))
        view_layout = QVBoxLayout(view_group)
        self.cull_check = QCheckBox(_("Descartar caras ocultas"))
        self.cull_check.setChecked(True)
        self.cull_check.setToolTip(_("Activa el culling por cullface del modelo"))
        self.tint_check = QCheckBox(_("Teñir biomas (plains)"))
        self.tint_check.setChecked(True)
        self.mobs_check = QCheckBox(_("Dibujar mobs"))
        self.mobs_check.setChecked(False)
        self.mobs_check.setToolTip(_("Criaturas como cerdos, aldeanos o monstruos (desactivado)"))
        self.droped_items_check = QCheckBox(_("Dibujar items caídos"))
        self.droped_items_check.setChecked(False)
        self.droped_items_check.setToolTip(_("Items caídos en el suelo (desactivado)"))
        self.smooth_check = QCheckBox(_("Suavizar texturas"))
        self.smooth_check.setToolTip(_("Filtrado lineal con mipmaps en el visor"))
        self.grid_check = QCheckBox(_("Mostrar rejilla"))
        self.grid_check.setChecked(True)
        self.axes_check = QCheckBox(_("Mostrar ejes"))
        self.axes_check.setChecked(True)
        self.wire_check = QCheckBox("Wireframe")
        for widget in (
            self.cull_check,
            self.tint_check,
            self.mobs_check,
            self.droped_items_check,
            self.smooth_check,
            self.grid_check,
            self.axes_check,
            self.wire_check,
        ):
            view_layout.addWidget(widget)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel(_("Resolución del atlas")))
        self.atlas_scale_combo = QComboBox()
        for value in (1, 2, 4):
            self.atlas_scale_combo.addItem(f"×{value}", value)
        self.atlas_scale_combo.setToolTip(_("Multiplica el tamaño de las texturas del atlas"))
        scale_row.addWidget(self.atlas_scale_combo, 1)
        view_layout.addLayout(scale_row)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel(_("Capas Y")))
        self.layer_min_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_max_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_min_slider.setToolTip(_("Primera capa visible"))
        self.layer_max_slider.setToolTip(_("Última capa visible"))
        layer_row.addWidget(self.layer_min_slider)
        layer_row.addWidget(self.layer_max_slider)
        view_layout.addLayout(layer_row)
        self.layer_label = QLabel("—")
        self.layer_label.setObjectName("hint")
        view_layout.addWidget(self.layer_label)

        colors_row = QHBoxLayout()
        self.bg_button = QPushButton(_("Color de fondo"))
        colors_row.addWidget(self.bg_button)
        colors_row.addStretch(1)
        view_layout.addLayout(colors_row)

        layout.addWidget(view_group)

        # Exportación
        export_group = QGroupBox(_("Exportar a Blender"))
        export_layout = QFormLayout(export_group)
        self.out_dir = QLineEdit()
        self.out_dir.setText(str(self.app_dir() / "lite2obj_out"))
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.out_dir, 1)
        self.out_dir_button = QPushButton("…")
        self.out_dir_button.setFixedWidth(34)
        dir_row.addWidget(self.out_dir_button)
        export_layout.addRow(_("Carpeta"), dir_row)
        self.name_edit = QLineEdit()
        export_layout.addRow(_("Nombre"), self.name_edit)
        self.group_combo = QComboBox()
        self.group_combo.addItem(_("Ninguna"), "none")
        self.group_combo.addItem(_("Por bloque"), "block")
        self.group_combo.addItem(_("Por transparencia"), "alpha")
        self.group_combo.setCurrentIndex(1)
        export_layout.addRow(_("Agrupar"), self.group_combo)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.01, 1000.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setValue(1.0)
        export_layout.addRow(_("Escala OBJ"), self.scale_spin)
        self.z_up_check = QCheckBox(_("Z arriba (nativo de Blender)"))
        export_layout.addRow("", self.z_up_check)
        self.export_button = QPushButton(_("Exportar OBJ…"))
        self.export_button.setToolTip(_("Escribe <nombre>.obj, <nombre>.mtl y atlas.png"))
        export_layout.addRow(self.export_button)
        layout.addWidget(export_group)

        # Estadísticas
        stats_group = QGroupBox(_("Estadísticas"))
        stats_layout = QVBoxLayout(stats_group)
        self.stats_label = QLabel("—")
        self.stats_label.setWordWrap(True)
        self.stats_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        stats_layout.addWidget(self.stats_label)
        layout.addWidget(stats_group)

        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def app_dir(self) -> Path:
        """Carpeta donde está el .AppImage, el .exe o el script."""
        # AppImage: ruta real del archivo .AppImage
        appimage = os.environ.get("APPIMAGE")
        if appimage:
            return Path(appimage).resolve().parent

        # Ejecutable congelado (PyInstaller): carpeta del .exe
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent

        # Desarrollo: carpeta del .py
        return Path(__file__).resolve().parent

    def _build_actions(self) -> None:
        self.open_action = QAction(_("Abrir esquemático…"), self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self._open_dialog)

        self.render_action = QAction(_("Renderizar"), self)
        self.render_action.setShortcut(QKeySequence("F5"))
        self.render_action.triggered.connect(lambda: self.render_mesh())

        self.export_action = QAction(_("Exportar OBJ…"), self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.triggered.connect(self.export)

        self.screenshot_action = QAction(_("Guardar captura…"), self)
        self.screenshot_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.screenshot_action.triggered.connect(self._screenshot)

        self.quit_action = QAction(_("Salir"), self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(lambda: self.close())

        self.fit_action = QAction(_("Reencuadrar"), self)
        self.fit_action.setShortcut(QKeySequence("F"))
        self.fit_action.triggered.connect(lambda: self.viewport.fit())

        self.grid_action = QAction(_("Rejilla"), self)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.grid_action.setShortcut(QKeySequence("G"))

        self.axes_action = QAction(_("Ejes"), self)
        self.axes_action.setCheckable(True)
        self.axes_action.setChecked(True)

        self.wire_action = QAction("Wireframe", self)
        self.wire_action.setCheckable(True)
        self.wire_action.setShortcut(QKeySequence("W"))

        self.assets_action = QAction(_("Generar archivo de assets…"), self)
        self.assets_action.setToolTip(
            _("Elige el client jar de Minecraft y extrae blockstates, modelos y texturas")
        )
        self.assets_action.triggered.connect(lambda: self._generate_asset_pack())

        self.associate_action = QAction(_("Asociar archivos .litematic…"), self)
        self.associate_action.setToolTip(
            _("Haz que los .litematic se abran con lite2obj al hacer doble clic")
        )
        self.associate_action.triggered.connect(lambda: self._install_association())
        self.unassociate_action = QAction(_("Quitar asociación de .litematic"), self)
        self.unassociate_action.setToolTip(_("Deshace la asociación creada desde lite2obj"))
        self.unassociate_action.triggered.connect(lambda: self._remove_association())
        if not associations.supported():
            self.associate_action.setEnabled(False)
            self.unassociate_action.setEnabled(False)

        self.demo_action = QAction(_("Renderizar litemática de prueba"), self)
        self.demo_action.setToolTip(_("Abre y renderiza all_blocks_and_entities.litematic"))
        self.demo_action.triggered.connect(lambda: self._render_demo())

        self.about_action = QAction(_("Acerca de lite2obj"), self)
        self.about_action.triggered.connect(lambda: self._about())

        self.smooth_action = QAction(_("Suavizar texturas"), self)
        self.smooth_action.setCheckable(True)
        self.smooth_action.toggled.connect(self.smooth_check.setChecked)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu(_("Archivo"))
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.render_action)
        file_menu.addAction(self.export_action)
        file_menu.addAction(self.screenshot_action)
        file_menu.addSeparator()
        file_menu.addAction(self.assets_action)
        file_menu.addSeparator()
        file_menu.addAction(self.associate_action)
        file_menu.addAction(self.unassociate_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        view_menu = self.menuBar().addMenu(_("Ver"))
        view_menu.addAction(self.fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.grid_action)
        view_menu.addAction(self.axes_action)
        view_menu.addAction(self.wire_action)
        view_menu.addAction(self.smooth_action)

        help_menu = self.menuBar().addMenu(_("Ayuda"))
        help_menu.addAction(self.demo_action)
        help_menu.addSeparator()
        help_menu.addAction(self.about_action)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar(_("Principal"))
        toolbar.setMovable(False)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.render_action)
        toolbar.addAction(self.export_action)
        toolbar.addSeparator()
        toolbar.addAction(self.fit_action)
        toolbar.addAction(self.grid_action)
        toolbar.addAction(self.wire_action)

    def _build_status(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.status_label = QLabel(_("Listo"))
        status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)

    def _connect_signals(self) -> None:
        self.open_button.clicked.connect(lambda: self._open_dialog())
        self.reload_button.clicked.connect(lambda: self._reload_current())
        self.assets_button.clicked.connect(lambda: self._generate_asset_pack())

        self.pack_add_file.clicked.connect(lambda: self._add_pack_dialog(False))
        self.pack_add_dir.clicked.connect(lambda: self._add_pack_dialog(True))
        self.pack_remove.clicked.connect(lambda: self._remove_pack())
        self.pack_up.clicked.connect(lambda: self._move_pack(-1))
        self.pack_down.clicked.connect(lambda: self._move_pack(1))
        self.pack_list.itemChanged.connect(self._pack_changed)

        self.cull_check.toggled.connect(lambda: self.render_mesh())
        self.tint_check.toggled.connect(lambda: self.render_mesh())
        self.mobs_check.toggled.connect(lambda: self.render_mesh())
        self.droped_items_check.toggled.connect(lambda: self.render_mesh())
        self.atlas_scale_combo.currentIndexChanged.connect(lambda: self.render_mesh())
        self.smooth_check.toggled.connect(self.viewport.set_smooth)
        self.smooth_check.toggled.connect(self.smooth_action.setChecked)

        self.grid_check.toggled.connect(self._on_grid)
        self.grid_action.toggled.connect(self._on_grid)
        self.axes_check.toggled.connect(self._on_axes)
        self.axes_action.toggled.connect(self._on_axes)
        self.wire_check.toggled.connect(self._on_wire)
        self.wire_action.toggled.connect(self._on_wire)

        self.layer_min_slider.valueChanged.connect(self._layers_changed)
        self.layer_max_slider.valueChanged.connect(self._layers_changed)
        self.bg_button.clicked.connect(lambda: self._pick_background())
        self.render_button.clicked.connect(lambda: self.render_mesh())
        self.export_button.clicked.connect(lambda: self.export())
        self.out_dir_button.clicked.connect(lambda: self._pick_out_dir())

    # -- assets ------------------------------------------------------------ #

    def _asset_pack_path(self) -> Path | None:
        pack = paths.asset_pack()
        if pack is not None and assetpack.is_asset_pack(pack):
            return pack
        return None

    def _update_assets_label(self) -> None:
        pack = paths.asset_pack()
        missing = self.mesh.missing_blocks if self.mesh is not None else ()
        count = self.mesh.missing_count if self.mesh is not None else 0
        status, detail, color = _asset_status(pack, missing, count)
        self.assets_status.setText(status)
        self.assets_status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.assets_label.setText(detail)
        self.assets_label.setToolTip(str(pack) if pack is not None else "")

    def _ensure_assets(self) -> bool:
        if self._asset_pack_path() is not None:
            return True
        self._show_missing_assets()
        return False

    def _show_missing_assets(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Faltan las texturas de Minecraft"))
        box.setText(_("No se encontró el archivo de assets de Minecraft."))
        box.setInformativeText(
            assets.missing_asset_pack_message()
            + "\n\n"
            + _("También puedes generarlo ahora eligiendo el client jar.")
        )
        generate = box.addButton(_("Generar ahora…"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Cerrar"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is generate:
            self._generate_asset_pack()

    def _reload_current(self) -> None:
        if self._busy or self.path is None:
            return
        self.open_path(self.path)

    # -- resource packs ----------------------------------------------------- #

    def _settings_list(self, key: str) -> list[str]:
        value = self.settings.value(key, [])
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return [str(value)]

    def _load_packs(self) -> None:
        """Carga los packs añadidos por el usuario, sin buscar en el disco."""
        paths = self._settings_list("resource_packs")
        enabled = set(self._settings_list("resource_packs_enabled"))
        self._packs_loading = True
        self.pack_list.clear()
        missing = False
        for raw in paths:
            path = Path(raw).expanduser()
            if not _is_resource_pack(path):
                missing = True
                continue
            self._append_pack_item(path, checked=str(path) in enabled)
        self._packs_loading = False
        if missing:
            self._save_packs()

    def _append_pack_item(self, path: Path, *, checked: bool, index: int | None = None) -> None:
        name = path.stem if path.is_file() else path.name
        item = QListWidgetItem(name)
        item.setData(Qt.ItemDataRole.UserRole, str(path))
        item.setFlags(
            item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEnabled
        )
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        item.setToolTip(str(path))
        if index is None:
            self.pack_list.addItem(item)
        else:
            self.pack_list.insertItem(index, item)

    def _save_packs(self) -> None:
        paths: list[str] = []
        enabled: list[str] = []
        for index in range(self.pack_list.count()):
            item = self.pack_list.item(index)
            path = item.data(Qt.ItemDataRole.UserRole)
            if not path:
                continue
            paths.append(str(path))
            if item.checkState() == Qt.CheckState.Checked:
                enabled.append(str(path))
        self.settings.setValue("resource_packs", paths)
        self.settings.setValue("resource_packs_enabled", enabled)

    def _add_pack_dialog(self, as_dir: bool) -> None:
        start = str(self.settings.value("last_pack_dir", str(Path.home())))
        if as_dir:
            directory = QFileDialog.getExistingDirectory(self, _("Añadir resource packs"), start)
            if not directory:
                return
            path = Path(directory)
            self.settings.setValue("last_pack_dir", str(path))
            self._add_pack_directory(path)
            return
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, _("Añadir resource packs"), start, _pack_filter()
        )
        if not chosen:
            return
        paths = [Path(item) for item in chosen]
        self.settings.setValue("last_pack_dir", str(paths[0].parent))
        self._add_packs(paths)

    def _add_pack_directory(self, directory: Path) -> None:
        """Añade un pack o todos los packs que haya dentro de una carpeta elegida."""
        packs = _resource_packs_in(directory)
        if not packs:
            self._show_error(
                _("No se encontraron resource packs en la carpeta."),
                f"{directory}\n\nElige un .zip, una carpeta con 'assets' o una carpeta "
                "que contenga varios packs.",
            )
            return
        self._add_packs(packs)

    def _add_packs(self, paths: list[Path]) -> None:
        """Añade varios packs a la vez, sin duplicados y con una sola actualización."""
        existing: set[str] = set()
        for index in range(self.pack_list.count()):
            key = self.pack_list.item(index).data(Qt.ItemDataRole.UserRole)
            if key:
                existing.add(str(key))
        valid: list[Path] = []
        invalid: list[Path] = []
        for path in paths:
            path = path.expanduser()
            if not _is_resource_pack(path):
                invalid.append(path)
                continue
            resolved = path.resolve()
            key = str(resolved)
            if key in existing:
                continue
            existing.add(key)
            valid.append(resolved)
        if valid:
            # Se insertan en orden inverso para que el primero de la selección quede
            # arriba, que es la mayor prioridad.
            self._packs_loading = True
            for path in reversed(valid):
                self._append_pack_item(path, checked=True, index=0)
            self._packs_loading = False
            self.pack_list.setCurrentRow(0)
            self._packs_changed()
        if invalid:
            self._show_error(
                _("Algunos resource packs no son válidos."),
                "\n".join(str(path) for path in invalid)
                + "\n\nDeben ser archivos .zip o carpetas con una subcarpeta 'assets'.",
            )

    def _remove_pack(self) -> None:
        row = self.pack_list.currentRow()
        if row < 0:
            return
        self.pack_list.takeItem(row)
        self._packs_changed()

    def _move_pack(self, delta: int) -> None:
        row = self.pack_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.pack_list.count()):
            return
        item = self.pack_list.takeItem(row)
        self.pack_list.insertItem(target, item)
        self.pack_list.setCurrentRow(target)
        self._packs_changed()

    def _pack_changed(self, _item: QListWidgetItem) -> None:
        if not self._packs_loading:
            self._packs_changed()

    def _packs_changed(self) -> None:
        self._save_packs()
        # Las fuentes y el atlas dependen de los packs: hay que reconstruirlos.
        self._resources = None
        self._resources_key = None
        self._invalidate()

    def _selected_packs(self) -> list[str]:
        # La lista se muestra con la mayor prioridad arriba, pero ``discover`` interpreta
        # la lista de menor a mayor prioridad (el último gana).
        paths: list[str] = []
        for index in range(self.pack_list.count()):
            item = self.pack_list.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                paths.append(str(path))
        return list(reversed(paths))

    # -- apertura y render -------------------------------------------------- #

    def open_path(self, path: Path) -> None:
        if not self._ensure_assets():
            return
        try:
            document = litematic.load(path)
        except Exception:
            self._show_error(_("No se pudo leer el esquemático."), traceback.format_exc())
            return

        self.path = path
        self.doc = document
        self.mesh = None
        self._mesh_key = None
        self._dirty = True
        self._update_assets_label()
        self.settings.setValue("last_dir", str(path.parent))
        self.setWindowTitle(f"lite2obj — {path.name}")

        if path.stem:
            self.name_edit.setText(_clean_name(document.name or path.stem))
        self._update_info()
        self._configure_layers(document.enclosing_size[1])
        self.render_mesh()

    def _update_info(self) -> None:
        if self.doc is None or self.path is None:
            self.info_label.setText(_("Ningún esquemático abierto."))
            return
        size = self.doc.enclosing_size
        lines = [
            self.doc.name or self.path.stem,
            _("{width} × {height} × {depth} bloques").format(
                width=size[0], height=size[1], depth=size[2]
            ),
            _("{regions} región(es) · {blocks:,} bloques").format(
                regions=len(self.doc.regions), blocks=self.doc.block_count
            ),
        ]
        if self.doc.author:
            lines.append(_("Autor: {author}").format(author=self.doc.author))
        self.info_label.setText("\n".join(lines))

    def _configure_layers(self, height: int) -> None:
        height = max(height, 1)
        self._layers_guard = True
        for slider in (self.layer_min_slider, self.layer_max_slider):
            slider.setRange(0, height - 1)
            slider.setEnabled(height > 1)
        self.layer_min_slider.setValue(0)
        self.layer_max_slider.setValue(height - 1)
        self._layers_guard = False
        self._layers_changed()

    def _layers_changed(self, *_args) -> None:
        if self._layers_guard or self.doc is None:
            return
        height = max(self.doc.enclosing_size[1], 1)
        low = min(self.layer_min_slider.value(), self.layer_max_slider.value())
        high = max(self.layer_min_slider.value(), self.layer_max_slider.value())
        self.layer_label.setText(
            _("Capas Y: {low}–{high} de {maximum}").format(low=low, high=high, maximum=height - 1)
        )
        self.viewport.set_layer(low, high + 1)

    def _render_options(self) -> RenderOptions:
        return RenderOptions(
            packs=self._selected_packs(),
            cull=self.cull_check.isChecked(),
            tint=self.tint_check.isChecked(),
            atlas_scale=int(self.atlas_scale_combo.currentData()),
            entities=True,
            mobs=self.mobs_check.isChecked(),
            droped_items=self.droped_items_check.isChecked(),
        )

    def _render_key(self) -> tuple:
        return (
            str(self.path),
            tuple(self._selected_packs()),
            self.cull_check.isChecked(),
            self.tint_check.isChecked(),
            int(self.atlas_scale_combo.currentData()),
            True,
            self.mobs_check.isChecked(),
            self.droped_items_check.isChecked(),
        )

    def render_mesh(self) -> None:
        if self.path is None:
            return
        if self._busy:
            return
        if not self._ensure_assets():
            return
        options = self._render_options()
        key = self._render_key()
        resources = self._resources if self._resources_key == tuple(options.packs) else None
        task = RenderTask(self.path, options, resources)
        task.signals.finished.connect(self._on_mesh)
        task.signals.failed.connect(self._on_error)
        task.signals.progress.connect(self._on_progress)
        self._task = task
        self._task_key = key
        self._task_packs = tuple(options.packs)
        self._render_started = time.perf_counter()
        self._set_busy(True, "render")
        self._pool.start(task)

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _on_mesh(self, mesh: Mesh) -> None:
        first = self.mesh is None
        self.mesh = mesh
        self._mesh_key = self._task_key
        self._task = None
        self._set_busy(False)
        self.viewport.set_mesh(mesh, fit=first)
        self._update_stats(mesh)
        self._update_assets_label()
        current = self._render_key()
        self._dirty = current != self._mesh_key

        if self._dirty:
            self.dirty_label.show()
        else:
            self.dirty_label.hide()

        self._resources = mesh.atlas.resources
        self._resources_key = self._task_packs
        if self._export_after_render:
            self._export_after_render = False
            if self._dirty:
                self.status_label.setText(_("Hay cambios pendientes; vuelve a exportar"))
            else:
                self._start_export(mesh)
        elif not self._dirty:
            self.status_label.setText(_("Malla lista"))

    def _update_stats(self, mesh: Mesh) -> None:
        stats = mesh.stats()
        width, height = mesh.atlas.size
        elapsed = max(time.perf_counter() - self._render_started, 0.0)
        self.stats_label.setText(
            _(
                "Tamaño: {width} × {height} × {depth} bloques\n"
                "Caras: {faces:,} ({triangles:,} triángulos)\n"
                "Bloques: {states:,} estados distintos\n"
                "Sprites: {sprites} · atlas {atlas_width}×{atlas_height}\n"
                "Tiempo: {elapsed:.2f} s"
            ).format(
                width=mesh.size[0],
                height=mesh.size[1],
                depth=mesh.size[2],
                faces=mesh.quad_count,
                triangles=mesh.quad_count * 2,
                states=len(mesh.palette),
                sprites=stats["sprites"],
                atlas_width=width,
                atlas_height=height,
                elapsed=elapsed,
            )
        )
        if mesh.missing_blocks:
            names = ", ".join(mesh.missing_blocks[:5])
            if len(mesh.missing_blocks) > 5:
                names += " " + _("y {count} más").format(count=len(mesh.missing_blocks) - 5)
            self.stats_label.setText(
                self.stats_label.text()
                + "\n"
                + _("⚠ Omitidos: {count:,} bloques ({names})").format(
                    count=mesh.missing_count, names=names
                )
            )

    def _invalidate(self, *_args) -> None:
        if self.path is None:
            return
        self._dirty = True
        self.dirty_label.show()
        if not self._busy:
            self.status_label.setText(_("Opciones cambiadas"))

    # -- exportación -------------------------------------------------------- #

    def export(self) -> None:
        if self.path is None:
            self.status_label.setText(_("Abre un esquemático antes de exportar"))
            return
        if self._busy:
            return
        if not self._ensure_assets():
            return
        if self.mesh is None or self._dirty or self._mesh_key != self._render_key():
            self._export_after_render = True
            self.status_label.setText(_("Renderizando antes de exportar…"))
            self.render_mesh()
            return
        self._start_export(self.mesh)

    def _start_export(self, mesh: Mesh) -> None:
        text = self.out_dir.text().strip()
        directory = Path(text).expanduser() if text else Path.cwd()
        name = _clean_name(self.name_edit.text().strip() or "model")
        options = ExportOptions(
            name=name,
            up_axis="z" if self.z_up_check.isChecked() else "y",
            group_by=str(self.group_combo.currentData()),
            scale=self.scale_spin.value(),
        )
        task = ExportTask(mesh, directory, options)
        task.signals.finished.connect(self._on_exported)
        task.signals.failed.connect(self._on_error)
        self._task = task
        self._set_busy(True, "export")
        self._pool.start(task)

    def _on_exported(self, result: ExportResult) -> None:
        self._task = None
        self._set_busy(False)
        self.settings.setValue("out_dir", self.out_dir.text())
        lines = [
            f"OBJ:   {result.obj}",
            f"MTL:   {result.mtl}" if result.mtl else "",
            f"Atlas: {result.atlas}" if result.atlas else "",
            "",
            _("{faces:,} caras · {vertices:,} vértices · {uvs:,} UVs").format(
                faces=result.faces, vertices=result.vertices, uvs=result.uvs
            ),
        ]
        message = "\n".join(line for line in lines if line)
        self.status_label.setText(_("Exportado: {path}").format(path=result.obj))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(_("Exportación completada"))
        box.setText(_("Se escribieron los archivos:"))
        box.setInformativeText(message)
        box.exec()

    # -- errores ------------------------------------------------------------ #

    def _on_error(self, trace: str) -> None:
        self._task = None
        self._export_after_render = False
        self._set_busy(False)
        self._show_error(_("No se pudo completar la operación."), trace)

    def _show_error(self, message: str, trace: str) -> None:
        self.status_label.setText(_("Error"))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("lite2obj")
        box.setText(message)
        box.setDetailedText(trace)
        box.exec()

    def _on_gl_error(self, message: str) -> None:  # pragma: no cover - depende de la GPU
        self.status_label.setText(_("OpenGL no disponible"))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(_("OpenGL no disponible"))
        box.setText(
            _(
                "No se pudo crear el contexto OpenGL 3.3.\n\n"
                "En Linux con NVIDIA + Wayland prueba a ejecutar con QT_QPA_PLATFORM=xcb."
            )
        )
        box.setDetailedText(message)
        box.exec()

    # -- acciones de la vista ---------------------------------------------- #

    def _on_grid(self, enabled: bool) -> None:
        if self.grid_check.isChecked() != enabled:
            self.grid_check.setChecked(enabled)
        if self.grid_action.isChecked() != enabled:
            self.grid_action.setChecked(enabled)
        self.viewport.set_grid(enabled)

    def _on_axes(self, enabled: bool) -> None:
        if self.axes_check.isChecked() != enabled:
            self.axes_check.setChecked(enabled)
        if self.axes_action.isChecked() != enabled:
            self.axes_action.setChecked(enabled)
        self.viewport.set_axes(enabled)

    def _on_wire(self, enabled: bool) -> None:
        if self.wire_check.isChecked() != enabled:
            self.wire_check.setChecked(enabled)
        if self.wire_action.isChecked() != enabled:
            self.wire_action.setChecked(enabled)
        self.viewport.set_wireframe(enabled)

    def _pick_background(self) -> None:
        color = QColorDialog.getColor(QColor.fromRgbF(*self.viewport.background), self)
        if color.isValid():
            self.viewport.set_background(color)

    def _open_dialog(self) -> None:
        if self._busy:
            return
        start = str(self.settings.value("last_dir", str(Path.home())))
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Abrir esquemático"), start, _litematic_filter()
        )
        if path:
            self.open_path(Path(path))

    def _pick_out_dir(self) -> None:
        start = self.out_dir.text() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, _("Carpeta de exportación"), start)
        if directory:
            self.out_dir.setText(directory)

    def _screenshot(self) -> None:
        start = Path(str(self.settings.value("last_dir", str(Path.home())))) / "captura.png"
        path, _filter = QFileDialog.getSaveFileName(
            self, _("Guardar captura"), str(start), _png_filter()
        )
        if not path:
            return
        if self.viewport.screenshot(path):
            self.status_label.setText(_("Captura guardada: {path}").format(path=path))
        else:
            self._show_error(_("No se pudo guardar la captura."), path)

    def _generate_asset_pack(self) -> None:
        if self._busy:
            self.status_label.setText(_("Espera a que termine la operación actual"))
            return
        start = str(self.settings.value("last_jar_dir", str(Path.home())))
        jar_path, _filter = QFileDialog.getOpenFileName(
            self, _("Selecciona el client jar de Minecraft"), start, _jar_filter()
        )
        if not jar_path:
            return
        jar = Path(jar_path)
        if not jar.is_file():
            self._show_error(_("No se pudo leer el client jar."), str(jar))
            return
        self.settings.setValue("last_jar_dir", str(jar.parent))

        default = paths.asset_pack_output()
        default.parent.mkdir(parents=True, exist_ok=True)
        path, _filter = QFileDialog.getSaveFileName(
            self, _("Guardar archivo de assets"), str(default), _asset_pack_filter()
        )
        if not path:
            return
        output = Path(path)
        if output.suffix.lower() != assetpack.ASSET_PACK_SUFFIX:
            output = output.with_suffix(assetpack.ASSET_PACK_SUFFIX)

        self.status_label.setText(_("Generando assets desde {name}…").format(name=jar.name))
        task = AssetPackTask(jar, output)
        task.signals.finished.connect(self._on_asset_pack)
        task.signals.failed.connect(self._on_error)
        self._task = task
        self._set_busy(True, "assets")
        self._pool.start(task)

    def _on_asset_pack(self, info: assetpack.AssetPackInfo) -> None:
        self._task = None
        self._set_busy(False)
        # El gestor y la detección cachean las fuentes; hay que recrearlos para que
        # usen el archivo recién generado. Si se guardó fuera de las rutas habituales,
        # la variable de entorno hace que esta sesión lo encuentre.
        os.environ["LITE2OBJ_ASSET_PACK"] = str(info.path)
        self._resources = None
        self._resources_key = None
        paths.asset_pack.cache_clear()
        self._update_assets_label()
        self._dirty = self.mesh is not None
        if self._dirty:
            self.dirty_label.show()
        self.status_label.setText(
            _("Assets de Minecraft {version} guardados en {name}").format(
                version=info.minecraft_version, name=info.path.name
            )
        )
        QMessageBox.information(
            self,
            _("Archivo de assets generado"),
            _(
                "Minecraft: {version}\nArchivos: {files:,}\nTamaño: {size:.1f} MB\n\n"
                "{path}\n\nEl programa ya usa este archivo. Vuelve a renderizar si había "
                "una malla abierta."
            ).format(
                version=info.minecraft_version,
                files=info.files,
                size=info.compressed_bytes / (1024 * 1024),
                path=info.path,
            ),
        )
        if self.path is not None:
            self.render_mesh()

    def _render_demo(self) -> None:
        if self._busy:
            self.status_label.setText(_("Espera a que termine la operación actual"))
            return
        path = _demo_schematic_path()
        if not path.is_file():
            self._show_error(
                _("No se encontró el esquemático de prueba."),
                _("Se esperaba en {path}").format(path=path),
            )
            return
        self.status_label.setText(_("Abriendo litemática de prueba…"))
        self.mobs_check.setChecked(True)
        self.grid_check.setChecked(False)
        self.axes_check.setChecked(False)
        self.droped_items_check.setChecked(True)
        self.open_path(path)

    def _install_association(self) -> None:
        if not associations.supported():
            QMessageBox.information(
                self,
                _("Asociación de archivos"),
                _("La asociación automática sólo está disponible en Linux y Windows."),
            )
            return
        state = _("activa") if associations.is_associated() else _("inactiva")
        answer = QMessageBox.question(
            self,
            _("Asociar archivos .litematic"),
            _(
                "Los archivos .litematic se abrirán con lite2obj al hacer doble clic.\n\n"
                "Programa: {command}\nAsociación instalada: {state}\n\n"
                "La asociación se guarda sólo para tu usuario. ¿Continuar?"
            ).format(command=associations.command_description(), state=state),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            detail = associations.install(icon=_app_icon_path())
        except associations.AssociationError as exc:
            self._show_error(_("No se pudo asociar el archivo .litematic."), str(exc))
            return
        QMessageBox.information(self, _("Asociación creada"), detail)
        self.status_label.setText(_("Archivos .litematic asociados a lite2obj"))

    def _remove_association(self) -> None:
        if not associations.supported():
            return
        answer = QMessageBox.question(
            self,
            _("Quitar asociación de .litematic"),
            _(
                "Los archivos .litematic dejarán de abrirse con lite2obj al hacer doble "
                "clic.\n\n¿Continuar?"
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            detail = associations.uninstall()
        except associations.AssociationError as exc:
            self._show_error(_("No se pudo quitar la asociación."), str(exc))
            return
        QMessageBox.information(self, _("Asociación eliminada"), detail)
        self.status_label.setText(_("Asociación de .litematic eliminada"))

    def _about(self) -> None:
        QMessageBox.about(
            self,
            _("Acerca de lite2obj"),
            _(
                "lite2obj\n\nRenderiza esquemáticos de Litematica y los exporta a OBJ + "
                "MTL + atlas para Blender.\n\nPython + PySide6 + OpenGL de Qt · texturas "
                "del archivo de assets."
            ),
        )

    # -- estado ocupado / cierre ------------------------------------------- #

    def _set_busy(self, busy: bool, kind: str = "render") -> None:
        self._busy = busy
        for widget in (
            self.open_button,
            self.reload_button,
            self.render_button,
            self.export_button,
            self.pack_add_file,
            self.pack_add_dir,
            self.pack_remove,
            self.pack_up,
            self.pack_down,
            self.pack_list,
        ):
            widget.setEnabled(not busy)
        self.assets_action.setEnabled(not busy)
        self.assets_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            label = {"render": _("Renderizando…"), "export": _("Exportando…")}
            self.status_label.setText(label.get(kind, _("Generando assets…")))
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def closeEvent(self, event) -> None:
        if self._busy:
            self._pool.waitForDone(10000)
        self.viewport.shutdown()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
        self.settings.setValue("out_dir", self.out_dir.text())
        super().closeEvent(event)

    # -- arrastrar y soltar ------------------------------------------------- #

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if any(url.toLocalFile().lower().endswith(".litematic") for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".litematic":
                self.open_path(path)
                event.acceptProposedAction()
                return


def _clean_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in value.strip())
    return cleaned.strip("_") or "model"


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#23262d"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e6e8ec"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1b1e24"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#262a32"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e6e8ec"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2c313a"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e6e8ec"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#4f8cff"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2c313a"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6e8ec"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a919c"))
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.Text, QColor("#737984"))
    palette.setColor(disabled, QPalette.ColorRole.ButtonText, QColor("#737984"))
    return palette


def _configure_platform() -> None:
    """Evita el fallo de contexto EGL de NVIDIA bajo Wayland usando XWayland."""
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("QT_QPA_PLATFORM"):
        return
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"


def _configure_opengl() -> None:
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


def _install_qt_translator(app: QApplication) -> None:
    """Traduce los textos propios de Qt (botones Sí/No, diálogos no nativos...)."""
    language = i18n.current_language()
    if language == "en":
        return
    translator = QTranslator(app)
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(f"qtbase_{language}", directory):
        app.installTranslator(translator)
        # La lista mantiene viva la referencia Python mientras exista la aplicación.
        _QT_TRANSLATORS.append(translator)


def run(path: str | Path | None = None) -> None:
    """Arranca la aplicación de escritorio; ``path`` abre un esquemático al inicio."""
    _configure_platform()
    _configure_opengl()
    instance = QApplication.instance()
    app = instance if isinstance(instance, QApplication) else QApplication(sys.argv[:1])
    app.setApplicationName("lite2obj")
    app.setOrganizationName("lite2obj")
    i18n.configure()
    _install_qt_translator(app)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(
        "QGroupBox { font-weight: 600; margin-top: 8px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }"
        "QLabel#hint { color: #9aa1ad; font-size: 11px; }"
        "QLabel#warning { color: #e8b45a; font-size: 11px; }"
        "QPushButton { padding: 5px 9px; }"
    )
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow(Path(path) if path else None)
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.show()
    app.exec()


def main() -> int:
    import argparse

    arguments, language = i18n.extract_language_option(sys.argv[1:])
    i18n.configure(language)
    parser = argparse.ArgumentParser(
        prog="lite2obj-gui", description=_("Abre la aplicación de escritorio de lite2obj.")
    )
    parser.add_argument(
        "--lang",
        choices=("en", "es"),
        default=None,
        help=_("idioma de la interfaz (en, es)"),
    )
    parser.add_argument("path", nargs="?", type=Path, help=_("archivo .litematic a abrir"))
    args = parser.parse_args(arguments)
    run(args.path)
    return 0
