"""Asociación de archivos ``.litematic`` con la aplicación.

En Linux se instala una entrada ``.desktop`` y un MIME propio en los datos del usuario,
sin tocar el sistema. En Windows se escriben las claves de ``HKCU\\Software\\Classes``,
así que tampoco hacen falta permisos de administrador.

La asociación es siempre una acción explícita del usuario: la app no registra nada al
arrancar ni busca gestores de archivos.
"""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

__all__ = [
    "AssociationError",
    "command",
    "command_description",
    "icon_path",
    "install",
    "is_associated",
    "supported",
    "uninstall",
]

MIME_TYPE = "application/x-litematic"
DESKTOP_ID = "lite2obj.desktop"
PROG_ID = "lite2obj.litematic"


class AssociationError(RuntimeError):
    """No se pudo crear o quitar la asociación de archivos."""


def supported() -> bool:
    """Cierto si la plataforma actual permite asociar archivos desde la app."""
    return sys.platform.startswith("linux") or sys.platform == "win32"


def command() -> list[str]:
    """Argumentos que abren la GUI, sin el archivo que se va a abrir.

    En una AppImage se usa la ruta del propio archivo (``APPIMAGE``) porque el binario
    interno vive en una carpeta temporal. En un ejecutable congelado se usa el ejecutable.
    En desarrollo se prefiere el lanzador ``lite2obj-gui`` del entorno virtual y, si no
    existe, ``python -m lite2obj.gui``.
    """
    if sys.platform == "win32":
        if getattr(sys, "frozen", False):
            return [str(Path(sys.executable).resolve())]
        return [sys.executable, "-m", "lite2obj.gui"]

    appimage = os.environ.get("APPIMAGE", "").strip()
    if appimage:
        path = Path(appimage).expanduser()
        if path.is_file():
            return [str(path.resolve())]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]

    script = Path(sys.executable).with_name("lite2obj-gui")
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "lite2obj.gui"]


def command_description() -> str:
    """Texto legible del comando que abrirá los archivos."""
    argv = command()
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(part) for part in argv)


def icon_path() -> Path | None:
    """Ruta del icono instalado para la entrada de escritorio (sólo Linux)."""
    if not sys.platform.startswith("linux"):
        return None
    return _data_home() / "icons" / "hicolor" / "256x256" / "apps" / "lite2obj.png"


def install(*, icon: Path | None = None) -> str:
    """Asocia ``.litematic`` con lite2obj y devuelve un resumen para el usuario."""
    if sys.platform == "win32":
        return _install_windows()
    if sys.platform.startswith("linux"):
        return _install_linux(icon)
    raise AssociationError("La asociación automática sólo está disponible en Linux y Windows.")


def uninstall() -> str:
    """Quita la asociación creada por :func:`install`."""
    if sys.platform == "win32":
        return _uninstall_windows()
    if sys.platform.startswith("linux"):
        return _uninstall_linux()
    raise AssociationError("La asociación automática sólo está disponible en Linux y Windows.")


def is_associated() -> bool:
    """Cierto si la asociación de este usuario apunta a lite2obj."""
    if sys.platform == "win32":
        return _is_associated_windows()
    if sys.platform.startswith("linux"):
        desktop = _desktop_path()
        if not desktop.is_file() or not _mime_path().is_file():
            return False
        try:
            content = desktop.read_text(encoding="utf-8")
        except OSError:
            return False
        return _desktop_exec(command()) in content
    return False


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #


def _data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".local" / "share"


def _desktop_path() -> Path:
    return _data_home() / "applications" / DESKTOP_ID


def _mime_path() -> Path:
    return _data_home() / "mime" / "packages" / "lite2obj.xml"


def _config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".config"


def _desktop_quote(value: str) -> str:
    """Escapa un argumento para la clave ``Exec`` de un archivo .desktop."""
    value = value.replace("%", "%%")
    if not value or any(character in value for character in " \t\n\"'\\`$"):
        for character in ("\\", '"', "`", "$"):
            value = value.replace(character, "\\" + character)
        return f'"{value}"'
    return value


def _desktop_exec(argv: list[str]) -> str:
    return "Exec=" + " ".join(_desktop_quote(part) for part in argv) + " %f"


def _desktop_entry(argv: list[str]) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=lite2obj\n"
        "GenericName=Renderizador de Litematica\n"
        "Comment=Renderiza esquemáticos .litematic y los exporta a OBJ para Blender\n"
        f"{_desktop_exec(argv)}\n"
        "Icon=lite2obj\n"
        "Terminal=false\n"
        "Categories=Graphics;3DGraphics;\n"
        "Keywords=Minecraft;Litematica;OBJ;Blender;\n"
        f"MimeType={MIME_TYPE};\n"
        "StartupWMClass=lite2obj\n"
    )


def _mime_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">\n'
        f'  <mime-type type="{MIME_TYPE}">\n'
        "    <comment>Esquemático de Litematica</comment>\n"
        '    <comment xml:lang="en">Litematica schematic</comment>\n'
        '    <glob pattern="*.litematic"/>\n'
        '    <magic priority="50">\n'
        '      <match value="\\x1f\\x8b" type="string" offset="0"/>\n'
        "    </magic>\n"
        "  </mime-type>\n"
        "</mime-info>\n"
    )


def _install_linux(icon: Path | None) -> str:
    desktop = _desktop_path()
    mime = _mime_path()
    try:
        desktop.parent.mkdir(parents=True, exist_ok=True)
        mime.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(_desktop_entry(command()), encoding="utf-8")
        mime.write_text(_mime_xml(), encoding="utf-8")
        if icon is not None and icon.is_file():
            destination = icon_path()
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(icon, destination)
    except OSError as exc:
        raise AssociationError(
            f"No se pudieron escribir los archivos de asociación: {exc}"
        ) from exc

    _refresh_linux(set_default=True)
    return (
        "Asociación creada para los archivos .litematic.\n"
        f"Programa: {command_description()}\n"
        f"Entrada: {desktop}\n\n"
        "Si el gestor de archivos no lo detecta al momento, cierra la sesión o vuelve a "
        "abrir el diálogo «Abrir con»."
    )


def _uninstall_linux() -> str:
    removed = False
    for path in (_desktop_path(), _mime_path()):
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError as exc:
            raise AssociationError(f"No se pudo borrar {path}: {exc}") from exc
        removed = True
    _remove_linux_default()
    _refresh_linux(set_default=False)
    return (
        "Asociación de .litematic eliminada." if removed else "No había una asociación instalada."
    )


def _remove_linux_default() -> None:
    """Quita la entrada de ``mimeapps.list`` que dejó ``xdg-mime default``."""
    for path in (
        _config_home() / "mimeapps.list",
        _data_home() / "applications" / "mimeapps.list",
    ):
        _remove_mimeapps_entry(path)


def _remove_mimeapps_entry(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    changed = False
    section = ""
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            result.append(line)
            continue
        if section == "Default Applications" and "=" in line:
            key, _, value = line.partition("=")
            if key.strip() == MIME_TYPE:
                entries = [item for item in value.split(";") if item and item != DESKTOP_ID]
                if entries:
                    result.append(f"{key}={';'.join(entries)};")
                changed = True
                continue
        result.append(line)
    if not changed:
        return
    with suppress(OSError):
        path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def _refresh_linux(*, set_default: bool) -> None:
    data = _data_home()
    _run(["update-mime-database", str(data / "mime")])
    _run(["update-desktop-database", str(data / "applications")])
    if set_default and not _run(["xdg-mime", "default", DESKTOP_ID, MIME_TYPE]):
        _run(["gio", "mime", MIME_TYPE, DESKTOP_ID])
    icon_dir = data / "icons" / "hicolor"
    if icon_dir.is_dir():
        _run(["gtk-update-icon-cache", "-f", "-t", str(icon_dir)])


def _run(argv: list[str]) -> bool:
    try:
        result = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #


def _winreg() -> Any:
    # ``winreg`` sólo existe en Windows; importarlo así evita fallos en Linux y en mypy.
    return importlib.import_module("winreg")


def _windows_quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _windows_open_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) + ' "%1"'


def _install_windows() -> str:
    winreg = _winreg()
    argv = command()
    exe = argv[0]
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.litematic") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROG_ID)
            winreg.SetValueEx(key, "Content Type", 0, winreg.REG_SZ, MIME_TYPE)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Esquemático de Litematica")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\DefaultIcon"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{_windows_quote(exe)},0")
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command"
        ) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _windows_open_command(argv))
    except OSError as exc:
        raise AssociationError(f"No se pudo escribir en el Registro de Windows: {exc}") from exc

    _notify_windows()
    return (
        "Asociación creada para los archivos .litematic.\n"
        f"Programa: {command_description()}\n\n"
        "Windows puede pedirte que confirmes lite2obj la primera vez que abras un archivo."
    )


def _uninstall_windows() -> str:
    winreg = _winreg()
    removed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.litematic") as key:
            value, _ = winreg.QueryValueEx(key, "")
        if str(value) == PROG_ID:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.litematic")
            removed = True
    except OSError:
        pass

    if _delete_tree(winreg, winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}"):
        removed = True
    _notify_windows()
    return (
        "Asociación de .litematic eliminada." if removed else "No había una asociación instalada."
    )


def _is_associated_windows() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.litematic") as key:
            value, _ = winreg.QueryValueEx(key, "")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command"
        ) as key:
            open_command, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return False
    return str(value) == PROG_ID and str(open_command) == _windows_open_command(command())


def _delete_tree(winreg: Any, root: Any, path: str) -> bool:
    removed = False
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    subkey = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(winreg, root, f"{path}\\{subkey}")
    except OSError:
        return False
    try:
        winreg.DeleteKey(root, path)
        removed = True
    except OSError:
        pass
    return removed


def _notify_windows() -> None:
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except AttributeError, OSError:
        pass
