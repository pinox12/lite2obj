"""Localización del archivo de assets de lite2obj.

El programa **no busca** instalaciones de Minecraft, resource packs ni esquemáticos en el
disco. Sólo usa el archivo ``lite2obj-assets.l2oassets`` incluido en el bundle, colocado
junto al ejecutable o guardado en los datos del usuario. Si no existe, el renderizador
falla con instrucciones para generarlo.

Variables de entorno:

=================================== ==========================================
Variable                            Qué hace
=================================== ==========================================
``LITE2OBJ_ASSET_PACK``             ruta explícita al archivo ``.l2oassets``
``LITE2OBJ_CLIENT_JAR``             client jar para ``lite2obj assets build``
=================================== ==========================================
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

__all__ = [
    "ASSET_PACK_NAME",
    "asset_pack",
    "asset_pack_output",
    "explicit_client_jar",
    "user_data_dir",
]

_HOME = Path.home()

#: Nombre por defecto del archivo de assets que genera ``lite2obj assets build``.
ASSET_PACK_NAME = "lite2obj-assets.l2oassets"


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
    """Localiza el archivo de assets, sin recorrer el disco.

    Sólo consulta ubicaciones conocidas: el bundle de PyInstaller, la carpeta del
    ejecutable y los datos del usuario. ``LITE2OBJ_ASSET_PACK`` fuerza una ruta concreta.
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
    candidates.append(user_data_dir() / ASSET_PACK_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def explicit_client_jar() -> Path | None:
    """Ruta indicada en ``LITE2OBJ_CLIENT_JAR``, aunque no exista.

    Sólo se usa para generar el archivo de assets; el renderizador nunca lee el jar.
    """
    return _env_path("LITE2OBJ_CLIENT_JAR")
