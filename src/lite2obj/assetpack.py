"""Genera y lee el archivo de assets de Minecraft que usa lite2obj.

El archivo es un zip con extensión ``.l2oassets``. Sólo guarda lo que necesita el
renderizador: blockstates, modelos, texturas (con sus ``.mcmeta``), modelos de objeto y
las variantes de cuadro. No incluye código, sonidos ni el resto del client jar.

El manifiesto ``lite2obj-assets.json`` de la raíz indica la versión de Minecraft y el
recuento de archivos. Así el programa puede usar un archivo ya generado sin tener
Minecraft instalado, y el usuario puede regenerarlo cuando salga una versión nueva.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__

__all__ = [
    "ASSET_PACK_SUFFIX",
    "MANIFEST_NAME",
    "AssetPackError",
    "AssetPackInfo",
    "build_asset_pack",
    "is_asset_pack",
    "read_manifest",
]

ASSET_PACK_SUFFIX = ".l2oassets"
MANIFEST_NAME = "lite2obj-assets.json"
_FORMAT = 1

#: Prefijos del jar que necesita el pipeline. Todo lo demás (código, sonidos, recetas,
#: estructuras...) se queda fuera para que el archivo sea pequeño.
_INCLUDE_PREFIXES = (
    "assets/minecraft/blockstates/",
    "assets/minecraft/models/",
    "assets/minecraft/textures/",
    "assets/minecraft/items/",
    "data/minecraft/painting_variant/",
)

#: Fecha fija para que dos builds del mismo jar produzcan un zip reproducible.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


class AssetPackError(Exception):
    """El jar de origen o el archivo de assets no son válidos."""


@dataclass(frozen=True, slots=True)
class AssetPackInfo:
    """Resultado de :func:`build_asset_pack`."""

    path: Path
    minecraft_version: str
    files: int
    uncompressed_bytes: int
    compressed_bytes: int


def _included(name: str) -> bool:
    return name.startswith(_INCLUDE_PREFIXES)


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _minecraft_version(archive: zipfile.ZipFile, jar: Path) -> str:
    """Lee ``version.json`` del jar; si no está, usa el nombre del archivo."""
    try:
        data = json.loads(archive.read("version.json"))
    except KeyError, json.JSONDecodeError, OSError:
        return jar.stem
    for key in ("id", "name"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return jar.stem


def build_asset_pack(
    jar: Path,
    output: Path,
    *,
    force: bool = False,
) -> AssetPackInfo:
    """Extrae los assets necesarios de ``jar`` y escribe ``output``.

    ``force=False`` evita sobrescribir un archivo existente. El zip se escribe primero
    en un temporal y sólo se mueve al destino cuando termina bien, para no dejar un
    archivo a medias si el jar está corrupto.
    """
    jar = Path(jar)
    output = Path(output)
    if not jar.is_file():
        raise AssetPackError(f"No existe el client jar: {jar}")
    if not zipfile.is_zipfile(jar):
        raise AssetPackError(f"El client jar no es un zip válido: {jar}")
    if output.exists() and not force:
        raise FileExistsError(f"El archivo ya existe: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary = output.with_name(f"{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(jar) as source:
            entries = [
                info for info in source.infolist() if not info.is_dir() and _included(info.filename)
            ]
            if not entries:
                raise AssetPackError(
                    "El jar no contiene assets de Minecraft reconocibles "
                    f"(se esperaban rutas como {_INCLUDE_PREFIXES[0]})."
                )
            version = _minecraft_version(source, jar)
            uncompressed = sum(info.file_size for info in entries)
            manifest = {
                "format": _FORMAT,
                "generator": f"lite2obj {__version__}",
                "minecraft_version": version,
                "source": jar.name,
                "created": datetime.fromtimestamp(jar.stat().st_mtime, tz=UTC).isoformat(
                    timespec="seconds"
                ),
                "files": len(entries),
                "uncompressed_bytes": uncompressed,
            }

            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as target:
                _write_entry(
                    target,
                    MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode(),
                )
                for info in entries:
                    _write_entry(target, info.filename, source.read(info))

        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return AssetPackInfo(
        path=output,
        minecraft_version=version,
        files=len(entries),
        uncompressed_bytes=uncompressed,
        compressed_bytes=output.stat().st_size,
    )


def read_manifest(path: Path) -> dict[str, Any] | None:
    """Devuelve el manifiesto del archivo de assets, o ``None`` si no es válido."""
    try:
        with zipfile.ZipFile(path) as archive:
            data = json.loads(archive.read(MANIFEST_NAME))
    except OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def is_asset_pack(path: Path) -> bool:
    """Cierto si ``path`` es un archivo de assets de lite2obj."""
    manifest = read_manifest(path)
    return manifest is not None and manifest.get("format") == _FORMAT
