"""Detección automática de instalaciones de Minecraft y sus assets.

Busca el client jar, los ``assets`` externos, los resource packs y las carpetas de
schematics en las ubicaciones habituales de Linux (vanilla, Modrinth, Prism, MultiMC,
ATLauncher, GDLauncher, Flatpak...). Cualquier ruta puede forzarse con una variable de
entorno:

=================================== ==========================================
Variable                            Qué sobreescribe
=================================== ==========================================
``LITE2OBJ_CLIENT_JAR``             ruta al client jar de Minecraft
``LITE2OBJ_ASSET_PACK``             archivo ``.l2oassets`` ya generado
``LITE2OBJ_ASSETS``                 directorio ``assets`` (con ``objects``/``indexes``)
``LITE2OBJ_RESOURCEPACKS``          directorios de resource packs (separados por ``:``)
``LITE2OBJ_SCHEMATICS``             directorios de schematics (separados por ``:``)
``LITE2OBJ_MINECRAFT``              raíz tipo ``.minecraft`` (se le añaden subrutas)
=================================== ==========================================
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

__all__ = [
    "ASSET_PACK_NAME",
    "asset_dirs",
    "asset_pack",
    "asset_pack_output",
    "client_jar",
    "explicit_client_jar",
    "minecraft_roots",
    "resourcepack_dirs",
    "schematic_dirs",
    "find_schematics",
    "user_data_dir",
]

_HOME = Path.home()

#: Nombre por defecto del archivo de assets que genera ``lite2obj assets build``.
ASSET_PACK_NAME = "lite2obj-assets.l2oassets"


def _env_paths(var: str) -> list[Path]:
    value = os.environ.get(var, "")
    return [Path(p).expanduser() for p in value.split(os.pathsep) if p.strip()]


def _env_path(var: str) -> Path | None:
    value = os.environ.get(var, "").strip()
    return Path(value).expanduser() if value else None


def user_data_dir() -> Path:
    """Directorio de datos del usuario donde se guarda el archivo de assets."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (_HOME / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = _HOME / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (_HOME / ".local" / "share"))
    return base / "lite2obj"


def asset_pack_output() -> Path:
    """Ruta por defecto del archivo de assets generado."""
    return user_data_dir() / ASSET_PACK_NAME


@lru_cache(maxsize=1)
def asset_pack() -> Path | None:
    """Localiza el archivo de assets ya generado, si existe.

    Se busca primero el que va dentro del bundle (PyInstaller), luego junto al
    ejecutable, en el directorio actual, en la raíz del repositorio y, por último, en
    los datos del usuario. ``LITE2OBJ_ASSET_PACK`` fuerza una ruta concreta.
    """
    explicit = _env_path("LITE2OBJ_ASSET_PACK")
    if explicit is not None:
        return explicit if explicit.is_file() else None

    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / ASSET_PACK_NAME)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / ASSET_PACK_NAME)
    candidates.extend(
        (
            Path.cwd() / ASSET_PACK_NAME,
            Path(__file__).resolve().parents[2] / ASSET_PACK_NAME,
            user_data_dir() / ASSET_PACK_NAME,
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


#: Raíces conocidas de instalaciones (``.minecraft`` y lanzadores de terceros).
_KNOWN_ROOTS: tuple[Path, ...] = (
    _HOME / ".minecraft",
    _HOME / ".local" / "share" / "ModrinthApp",
    _HOME / ".var" / "app" / "com.modrinth.ModrinthApp" / "data" / "ModrinthApp",
    _HOME / ".local" / "share" / "PrismLauncher",
    _HOME / ".var" / "app" / "org.prismlauncher.PrismLauncher" / "data" / "PrismLauncher",
    _HOME / ".local" / "share" / "multimc",
    _HOME / ".local" / "share" / "ATLauncher",
    _HOME / ".local" / "share" / "gdlauncher",
    _HOME / "curseforge" / "minecraft",
)


@lru_cache(maxsize=1)
def minecraft_roots() -> list[Path]:
    """Raíces existentes donde pueden vivir versiones, assets y packs."""
    roots: list[Path] = []
    explicit = _env_path("LITE2OBJ_MINECRAFT")
    if explicit is not None:
        roots.append(explicit)
    for root in _KNOWN_ROOTS:
        if root.is_dir() and root not in roots:
            roots.append(root)
    return roots


def _version_key(path: Path) -> tuple[int, ...]:
    """Clave de orden por versión a partir del nombre, p. ej. ``26.3`` -> (26, 3)."""
    parts: list[int] = []
    for chunk in path.stem.replace("-", ".").replace("_", ".").split("."):
        if chunk.isdigit():
            parts.append(int(chunk))
    return tuple(parts) or (0,)


def explicit_client_jar() -> Path | None:
    """Ruta indicada en ``LITE2OBJ_CLIENT_JAR``, aunque no exista."""
    return _env_path("LITE2OBJ_CLIENT_JAR")


@lru_cache(maxsize=1)
def client_jar() -> Path | None:
    """Localiza el client jar de Minecraft más reciente que exista."""
    explicit = explicit_client_jar()
    if explicit is not None:
        return explicit if explicit.is_file() else None

    candidates: list[Path] = []

    # Lanzadores tipo Modrinth/Prism: <root>/meta|versions/<version>/<version>.jar
    for root in minecraft_roots():
        for container in (root / "versions", root / "meta" / "versions"):
            if not container.is_dir():
                continue
            for version_dir in container.iterdir():
                if not version_dir.is_dir():
                    continue
                candidates.extend(
                    jar
                    for jar in version_dir.glob("*.jar")
                    if "slim" not in jar.name and "sources" not in jar.name
                )

    if not candidates:
        return None
    # Ordena por versión detectada y, como desempate, por tamaño.
    candidates.sort(key=lambda p: (_version_key(p), p.stat().st_size))
    return candidates[-1]


@lru_cache(maxsize=1)
def asset_dirs() -> list[Path]:
    """Directorios ``assets`` existentes (con ``objects`` o ``indexes``)."""
    dirs: list[Path] = []
    explicit = _env_path("LITE2OBJ_ASSETS")
    if explicit is not None and explicit.is_dir():
        dirs.append(explicit)
    for root in minecraft_roots():
        for candidate in (root / "assets", root / "meta" / "assets"):
            if candidate.is_dir() and (candidate / "objects").exists() and candidate not in dirs:
                dirs.append(candidate)
    return dirs


@lru_cache(maxsize=1)
def resourcepack_dirs() -> list[Path]:
    """Directorios que contienen resource packs (``.zip`` y carpetas)."""
    dirs: list[Path] = []
    dirs.extend(p for p in _env_paths("LITE2OBJ_RESOURCEPACKS") if p.is_dir())
    for root in minecraft_roots():
        for candidate in (root / "resourcepacks",):
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
        # Perfiles de lanzadores: <root>/profiles/<perfil>/resourcepacks
        profiles = root / "profiles"
        if profiles.is_dir():
            for profile in profiles.iterdir():
                candidate = profile / "resourcepacks"
                if profile.is_dir() and candidate.is_dir() and candidate not in dirs:
                    dirs.append(candidate)
    return dirs


@lru_cache(maxsize=1)
def schematic_dirs() -> list[Path]:
    """Directorios que contienen archivos ``.litematic``."""
    dirs: list[Path] = []
    dirs.extend(p for p in _env_paths("LITE2OBJ_SCHEMATICS") if p.is_dir())
    for root in minecraft_roots():
        candidate = root / "schematics"
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
        profiles = root / "profiles"
        if profiles.is_dir():
            for profile in profiles.iterdir():
                candidate = profile / "schematics"
                if profile.is_dir() and candidate.is_dir() and candidate not in dirs:
                    dirs.append(candidate)
    return dirs


def find_schematics() -> list[Path]:
    """Todos los ``.litematic`` encontrados, ordenados por ruta."""
    found: set[Path] = set()
    for directory in schematic_dirs():
        found.update(directory.glob("*.litematic"))
    return sorted(found)
