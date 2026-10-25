"""Aplicación de escritorio de lite2obj.

Sustituye al antiguo visor web: todo el flujo (abrir esquemático, elegir resource packs,
ajustar el render, previsualizar en 3D y exportar a OBJ) ocurre en una única ventana Qt.
El renderizado 3D se hace en Python con el OpenGL nativo de Qt (:class:`QOpenGLWidget`).
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette, QSurfaceFormat
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

from .. import assets, litematic, paths
from ..assets import ResourceManager
from ..export import ExportOptions, ExportResult, export_obj
from ..mesh import Mesh, MeshBuilder
from ..models import ModelResolver
from ..pipeline import RenderOptions, build_resources
from .viewport import Viewport

__all__ = ["MainWindow", "main", "run"]

_LITEMATIC_FILTER = "Litematica (*.litematic);;Todos los archivos (*)"
_PNG_FILTER = "PNG (*.png);;Todos los archivos (*)"
_DEMO_SCHEMATIC_NAME = "all_blocks_and_entities.litematic"


def _demo_schematic_path() -> Path:
    """Ruta del esquemático de prueba incluido en el repositorio."""
    candidates = (
        Path(__file__).resolve().parents[3] / _DEMO_SCHEMATIC_NAME,
        Path.cwd() / _DEMO_SCHEMATIC_NAME,
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


class MainWindow(QMainWindow):
    """Ventana principal: panel de opciones a la izquierda y viewport a la derecha."""

    def __init__(self, initial: Path | None = None) -> None:
        super().__init__()
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
        self._render_started = 0.0
        self._packs_loading = False
        self._layers_guard = False
        self._combo_guard = False
        self._pool = QThreadPool.globalInstance()

        self._build_ui()
        self._load_packs()
        self._load_schematics()

        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        splitter_state = self.settings.value("splitter")
        if splitter_state is not None:
            self.splitter.restoreState(splitter_state)

        self.viewport.failed.connect(self._on_gl_error)
        if initial is not None:
            self.open_path(Path(initial))

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
        schematic_group = QGroupBox("Esquemático")
        schematic_layout = QVBoxLayout(schematic_group)
        self.schematic_combo = QComboBox()
        self.schematic_combo.setToolTip("Esquemáticos .litematic encontrados en el disco")
        schematic_layout.addWidget(self.schematic_combo)
        open_row = QHBoxLayout()
        self.open_button = QPushButton("Abrir…")
        self.reload_button = QPushButton("Actualizar")
        open_row.addWidget(self.open_button)
        open_row.addWidget(self.reload_button)
        schematic_layout.addLayout(open_row)
        self.info_label = QLabel("Ningún esquemático abierto.")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        schematic_layout.addWidget(self.info_label)
        layout.addWidget(schematic_group)

        # Resource packs
        packs_group = QGroupBox("Resource packs")
        packs_layout = QVBoxLayout(packs_group)
        hint = QLabel("Arriba = mayor prioridad. Los assets que falten se toman del vanilla.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        packs_layout.addWidget(hint)
        self.pack_list = QListWidget()
        self.pack_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.pack_list.setFixedHeight(150)
        self.pack_list.setToolTip("Marca los packs que quieres aplicar al render")
        packs_layout.addWidget(self.pack_list)
        pack_row = QHBoxLayout()
        self.pack_up = QPushButton("Subir")
        self.pack_down = QPushButton("Bajar")
        self.pack_reload = QPushButton("Recargar")
        pack_row.addWidget(self.pack_up)
        pack_row.addWidget(self.pack_down)
        pack_row.addWidget(self.pack_reload)
        packs_layout.addLayout(pack_row)
        layout.addWidget(packs_group)

        self.dirty_label = QLabel("Hay cambios sin aplicar. Pulsa Renderizar (F5).")
        self.dirty_label.setObjectName("warning")
        self.dirty_label.setWordWrap(True)
        self.dirty_label.hide()
        packs_layout.addWidget(self.dirty_label)

        self.render_button = QPushButton("Renderizar")
        self.render_button.setToolTip("Vuelve a generar la malla con las opciones actuales (F5)")
        packs_layout.addWidget(self.render_button)

        # Vista
        view_group = QGroupBox("Vista y render")
        view_layout = QVBoxLayout(view_group)
        self.cull_check = QCheckBox("Descartar caras ocultas")
        self.cull_check.setChecked(True)
        self.cull_check.setToolTip("Activa el culling por cullface del modelo")
        self.tint_check = QCheckBox("Teñir biomas (plains)")
        self.tint_check.setChecked(True)
        self.mobs_check = QCheckBox("Dibujar mobs")
        self.mobs_check.setChecked(False)
        self.mobs_check.setToolTip("Criaturas como cerdos, aldeanos o monstruos (desactivado)")
        self.droped_items_check = QCheckBox("Dibujar items caídos")
        self.droped_items_check.setChecked(False)
        self.droped_items_check.setToolTip("Items caídos en el suelo (desactivado)")
        self.smooth_check = QCheckBox("Suavizar texturas")
        self.smooth_check.setToolTip("Filtrado lineal con mipmaps en el visor")
        self.grid_check = QCheckBox("Mostrar rejilla")
        self.grid_check.setChecked(True)
        self.axes_check = QCheckBox("Mostrar ejes")
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
        scale_row.addWidget(QLabel("Resolución del atlas"))
        self.atlas_scale_combo = QComboBox()
        for value in (1, 2, 4):
            self.atlas_scale_combo.addItem(f"×{value}", value)
        self.atlas_scale_combo.setToolTip("Multiplica el tamaño de las texturas del atlas")
        scale_row.addWidget(self.atlas_scale_combo, 1)
        view_layout.addLayout(scale_row)

        layer_row = QHBoxLayout()
        layer_row.addWidget(QLabel("Capas Y"))
        self.layer_min_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_max_slider = QSlider(Qt.Orientation.Horizontal)
        self.layer_min_slider.setToolTip("Primera capa visible")
        self.layer_max_slider.setToolTip("Última capa visible")
        layer_row.addWidget(self.layer_min_slider)
        layer_row.addWidget(self.layer_max_slider)
        view_layout.addLayout(layer_row)
        self.layer_label = QLabel("—")
        self.layer_label.setObjectName("hint")
        view_layout.addWidget(self.layer_label)

        colors_row = QHBoxLayout()
        self.bg_button = QPushButton("Color de fondo")
        colors_row.addWidget(self.bg_button)
        colors_row.addStretch(1)
        view_layout.addLayout(colors_row)

        layout.addWidget(view_group)

        # Exportación
        export_group = QGroupBox("Exportar a Blender")
        export_layout = QFormLayout(export_group)
        self.out_dir = QLineEdit()
        self.out_dir.setText(str(self.app_dir() / "lite2obj_out"))
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.out_dir, 1)
        self.out_dir_button = QPushButton("…")
        self.out_dir_button.setFixedWidth(34)
        dir_row.addWidget(self.out_dir_button)
        export_layout.addRow("Carpeta", dir_row)
        self.name_edit = QLineEdit()
        export_layout.addRow("Nombre", self.name_edit)
        self.group_combo = QComboBox()
        self.group_combo.addItem("Ninguna", "none")
        self.group_combo.addItem("Por bloque", "block")
        self.group_combo.addItem("Por transparencia", "alpha")
        self.group_combo.setCurrentIndex(1)
        export_layout.addRow("Agrupar", self.group_combo)
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.01, 1000.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setValue(1.0)
        export_layout.addRow("Escala OBJ", self.scale_spin)
        self.z_up_check = QCheckBox("Z arriba (nativo de Blender)")
        export_layout.addRow("", self.z_up_check)
        self.export_button = QPushButton("Exportar OBJ…")
        self.export_button.setToolTip("Escribe <nombre>.obj, <nombre>.mtl y atlas.png")
        export_layout.addRow(self.export_button)
        layout.addWidget(export_group)

        # Estadísticas
        stats_group = QGroupBox("Estadísticas")
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
        self.open_action = QAction("Abrir esquemático…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self._open_dialog)

        self.render_action = QAction("Renderizar", self)
        self.render_action.setShortcut(QKeySequence("F5"))
        self.render_action.triggered.connect(lambda: self.render_mesh())

        self.export_action = QAction("Exportar OBJ…", self)
        self.export_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_action.triggered.connect(self.export)

        self.screenshot_action = QAction("Guardar captura…", self)
        self.screenshot_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.screenshot_action.triggered.connect(self._screenshot)

        self.quit_action = QAction("Salir", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(lambda: self.close())

        self.fit_action = QAction("Reencuadrar", self)
        self.fit_action.setShortcut(QKeySequence("F"))
        self.fit_action.triggered.connect(lambda: self.viewport.fit())

        self.grid_action = QAction("Rejilla", self)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(True)
        self.grid_action.setShortcut(QKeySequence("G"))

        self.axes_action = QAction("Ejes", self)
        self.axes_action.setCheckable(True)
        self.axes_action.setChecked(True)

        self.wire_action = QAction("Wireframe", self)
        self.wire_action.setCheckable(True)
        self.wire_action.setShortcut(QKeySequence("W"))

        self.demo_action = QAction("Renderizar litemática de prueba", self)
        self.demo_action.setToolTip("Abre y renderiza all_blocks_and_entities.litematic")
        self.demo_action.triggered.connect(lambda: self._render_demo())

        self.about_action = QAction("Acerca de lite2obj", self)
        self.about_action.triggered.connect(lambda: self._about())

        self.smooth_action = QAction("Suavizar texturas", self)
        self.smooth_action.setCheckable(True)
        self.smooth_action.toggled.connect(self.smooth_check.setChecked)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Archivo")
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.render_action)
        file_menu.addAction(self.export_action)
        file_menu.addAction(self.screenshot_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        view_menu = self.menuBar().addMenu("Ver")
        view_menu.addAction(self.fit_action)
        view_menu.addSeparator()
        view_menu.addAction(self.grid_action)
        view_menu.addAction(self.axes_action)
        view_menu.addAction(self.wire_action)
        view_menu.addAction(self.smooth_action)

        help_menu = self.menuBar().addMenu("Ayuda")
        help_menu.addAction(self.demo_action)
        help_menu.addSeparator()
        help_menu.addAction(self.about_action)

    def _build_toolbar(self) -> None:
        toolbar = self.addToolBar("Principal")
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
        self.status_label = QLabel("Listo")
        status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)

    def _connect_signals(self) -> None:
        self.open_button.clicked.connect(lambda: self._open_dialog())
        self.reload_button.clicked.connect(lambda: self._refresh_lists())
        self.schematic_combo.activated.connect(self._combo_activated)

        self.pack_reload.clicked.connect(lambda: self._load_packs())
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

    # -- listas ------------------------------------------------------------ #

    def _load_schematics(self) -> None:
        current = self.path
        self._combo_guard = True
        self.schematic_combo.clear()
        found = paths.find_schematics()
        if not found:
            self.schematic_combo.addItem("No se encontraron .litematic")
        for path in found:
            self.schematic_combo.addItem(path.name, str(path))
            self.schematic_combo.setItemData(
                self.schematic_combo.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole
            )
        if current is not None:
            index = self.schematic_combo.findData(str(current))
            if index >= 0:
                self.schematic_combo.setCurrentIndex(index)
        self._combo_guard = False

    def _load_packs(self) -> None:
        checked = self._checked_pack_names()
        self._packs_loading = True
        self.pack_list.clear()
        for pack in assets.list_packs():
            item = QListWidgetItem(pack.name)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(
                Qt.CheckState.Checked if pack.name in checked else Qt.CheckState.Unchecked
            )
            item.setToolTip(str(pack.path))
            self.pack_list.addItem(item)
        self._packs_loading = False
        if checked:
            self._invalidate()

    def _refresh_lists(self) -> None:
        paths.resourcepack_dirs.cache_clear()
        paths.schematic_dirs.cache_clear()
        self._load_schematics()
        self._load_packs()
        self.status_label.setText("Listas actualizadas")

    def _checked_pack_names(self) -> list[str]:
        names: list[str] = []
        for index in range(self.pack_list.count()):
            item = self.pack_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                names.append(item.text())
        return names

    def _selected_packs(self) -> list[str]:
        # La lista se muestra con la mayor prioridad arriba, pero ``discover`` interpreta
        # la lista de menor a mayor prioridad (el último gana).
        return list(reversed(self._checked_pack_names()))

    def _combo_activated(self, index: int) -> None:
        if self._combo_guard:
            return
        path = self.schematic_combo.itemData(index)
        if path:
            self.open_path(Path(path))

    def _move_pack(self, delta: int) -> None:
        row = self.pack_list.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.pack_list.count()):
            return
        item = self.pack_list.takeItem(row)
        self.pack_list.insertItem(target, item)
        self.pack_list.setCurrentRow(target)
        if item.checkState() == Qt.CheckState.Checked:
            self._invalidate()

    def _pack_changed(self, _item: QListWidgetItem) -> None:
        if not self._packs_loading:
            self._invalidate()

    # -- apertura y render -------------------------------------------------- #

    def open_path(self, path: Path) -> None:
        try:
            document = litematic.load(path)
        except Exception:
            self._show_error("No se pudo leer el esquemático.", traceback.format_exc())
            return

        self.path = path
        self.doc = document
        self.mesh = None
        self._mesh_key = None
        self._dirty = True
        self.settings.setValue("last_dir", str(path.parent))
        self.setWindowTitle(f"lite2obj — {path.name}")

        if path.stem:
            self.name_edit.setText(_clean_name(document.name or path.stem))
        self._update_info()
        self._configure_layers(document.enclosing_size[1])

        index = self.schematic_combo.findData(str(path))
        if index < 0:
            self.schematic_combo.addItem(path.name, str(path))
            index = self.schematic_combo.count() - 1
        self._combo_guard = True
        self.schematic_combo.setCurrentIndex(index)
        self._combo_guard = False
        self.render_mesh()

    def _update_info(self) -> None:
        if self.doc is None or self.path is None:
            self.info_label.setText("Ningún esquemático abierto.")
            return
        size = self.doc.enclosing_size
        lines = [
            self.doc.name or self.path.stem,
            f"{size[0]} × {size[1]} × {size[2]} bloques",
            f"{len(self.doc.regions)} región(es) · {self.doc.block_count:,} bloques",
        ]
        if self.doc.author:
            lines.append(f"Autor: {self.doc.author}")
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
        self.layer_label.setText(f"Capas Y: {low}–{high} de {height - 1}")
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
        options = self._render_options()
        key = self._render_key()
        resources = self._resources if self._resources_key == tuple(options.packs) else None
        task = RenderTask(self.path, options, resources)
        task.signals.finished.connect(self._on_mesh)
        task.signals.failed.connect(self._on_error)
        task.signals.progress.connect(self._on_progress)
        self._task = task
        self._task_key = key
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
        current = self._render_key()
        self._dirty = current != self._mesh_key

        if self._dirty:
            self.dirty_label.show()
        else:
            self.dirty_label.hide()

        self._resources = mesh.atlas.resources
        if self._task_key is not None:
            self._resources_key = tuple(self._task_key[1])
        if self._export_after_render:
            self._export_after_render = False
            if self._dirty:
                self.status_label.setText("Hay cambios pendientes; vuelve a exportar")
            else:
                self._start_export(mesh)
        elif not self._dirty:
            self.status_label.setText("Malla lista")

    def _update_stats(self, mesh: Mesh) -> None:
        stats = mesh.stats()
        width, height = mesh.atlas.size
        elapsed = max(time.perf_counter() - self._render_started, 0.0)
        self.stats_label.setText(
            f"Tamaño: {mesh.size[0]} × {mesh.size[1]} × {mesh.size[2]} bloques\n"
            f"Caras: {mesh.quad_count:,} ({mesh.quad_count * 2:,} triángulos)\n"
            f"Bloques: {len(mesh.palette):,} estados distintos\n"
            f"Sprites: {stats['sprites']} · atlas {width}×{height}\n"
            f"Tiempo: {elapsed:.2f} s"
        )

    def _invalidate(self, *_args) -> None:
        if self.path is None:
            return
        self._dirty = True
        self.dirty_label.show()
        if not self._busy:
            self.status_label.setText("Opciones cambiadas")

    # -- exportación -------------------------------------------------------- #

    def export(self) -> None:
        if self.path is None:
            self.status_label.setText("Abre un esquemático antes de exportar")
            return
        if self._busy:
            return
        if self.mesh is None or self._dirty or self._mesh_key != self._render_key():
            self._export_after_render = True
            self.status_label.setText("Renderizando antes de exportar…")
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
            f"{result.faces:,} caras · {result.vertices:,} vértices · {result.uvs:,} UVs",
        ]
        message = "\n".join(line for line in lines if line)
        self.status_label.setText(f"Exportado: {result.obj}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Exportación completada")
        box.setText("Se escribieron los archivos:")
        box.setInformativeText(message)
        box.exec()

    # -- errores ------------------------------------------------------------ #

    def _on_error(self, trace: str) -> None:
        self._task = None
        self._export_after_render = False
        self._set_busy(False)
        self._show_error("No se pudo completar la operación.", trace)

    def _show_error(self, message: str, trace: str) -> None:
        self.status_label.setText("Error")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("lite2obj")
        box.setText(message)
        box.setDetailedText(trace)
        box.exec()

    def _on_gl_error(self, message: str) -> None:  # pragma: no cover - depende de la GPU
        self.status_label.setText("OpenGL no disponible")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("OpenGL no disponible")
        box.setText(
            "No se pudo crear el contexto OpenGL 3.3.\n\n"
            "En Linux con NVIDIA + Wayland prueba a ejecutar con QT_QPA_PLATFORM=xcb."
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
        path, _ = QFileDialog.getOpenFileName(self, "Abrir esquemático", start, _LITEMATIC_FILTER)
        if path:
            self.open_path(Path(path))

    def _pick_out_dir(self) -> None:
        start = self.out_dir.text() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Carpeta de exportación", start)
        if directory:
            self.out_dir.setText(directory)

    def _screenshot(self) -> None:
        start = Path(str(self.settings.value("last_dir", str(Path.home())))) / "captura.png"
        path, _ = QFileDialog.getSaveFileName(self, "Guardar captura", str(start), _PNG_FILTER)
        if not path:
            return
        if self.viewport.screenshot(path):
            self.status_label.setText(f"Captura guardada: {path}")
        else:
            self._show_error("No se pudo guardar la captura.", path)

    def _render_demo(self) -> None:
        if self._busy:
            self.status_label.setText("Espera a que termine la operación actual")
            return
        path = _demo_schematic_path()
        if not path.is_file():
            self._show_error(
                "No se encontró el esquemático de prueba.",
                f"Se esperaba en {path}",
            )
            return
        self.status_label.setText("Abriendo litemática de prueba…")
        self.mobs_check.setChecked(True)
        self.grid_check.setChecked(False)
        self.axes_check.setChecked(False)
        self.droped_items_check.setChecked(True)
        self.open_path(path)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "Acerca de lite2obj",
            "lite2obj\n\n"
            "Renderiza esquemáticos de Litematica y los exporta a OBJ + MTL + atlas "
            "para Blender.\n\n"
            "Python + PySide6 + OpenGL de Qt · texturas del client jar y resource packs.",
        )

    # -- estado ocupado / cierre ------------------------------------------- #

    def _set_busy(self, busy: bool, kind: str = "render") -> None:
        self._busy = busy
        for widget in (
            self.open_button,
            self.reload_button,
            self.render_button,
            self.export_button,
        ):
            widget.setEnabled(not busy)
        self.schematic_combo.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.status_label.setText("Renderizando…" if kind == "render" else "Exportando…")
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


def run(path: str | Path | None = None) -> None:
    """Arranca la aplicación de escritorio; ``path`` abre un esquemático al inicio."""
    _configure_platform()
    _configure_opengl()
    instance = QApplication.instance()
    app = instance if isinstance(instance, QApplication) else QApplication(sys.argv[:1])
    app.setApplicationName("lite2obj")
    app.setOrganizationName("lite2obj")
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(
        "QGroupBox { font-weight: 600; margin-top: 8px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }"
        "QLabel#hint { color: #9aa1ad; font-size: 11px; }"
        "QLabel#warning { color: #e8b45a; font-size: 11px; }"
        "QPushButton { padding: 5px 9px; }"
    )
    window = MainWindow(Path(path) if path else None)
    window.show()
    app.exec()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="lite2obj-gui", description="Abre la aplicación de escritorio de lite2obj."
    )
    parser.add_argument("path", nargs="?", type=Path, help="archivo .litematic a abrir")
    args = parser.parse_args()
    run(args.path)
    return 0
