"""Pila de recursos de Minecraft basada en el archivo ``.l2oassets``.

Resuelve rutas internas como ``assets/minecraft/models/block/oak_stairs.json``. La fuente
vanilla es siempre el archivo de assets; encima se pueden apilar resource packs indicados
**explícitamente** por ruta. No se buscan instalaciones de Minecraft ni packs en el disco.
Cada asset se busca capa por capa, así que un pack antiguo (p. ej. 1.20) puede usarse sobre
un archivo vanilla más nuevo: lo que falte se toma del vanilla.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from PIL import Image

from . import assetpack, paths
from .i18n import _

__all__ = [
    "DEFAULT_NAMESPACE",
    "DirSource",
    "InvalidAssetPackError",
    "MemorySource",
    "MissingAssetPackError",
    "ResourceManager",
    "ResourceSource",
    "ZipSource",
    "blockstate_path",
    "missing_asset_pack_message",
    "model_path",
    "parse_resource_id",
    "texture_path",
]

DEFAULT_NAMESPACE = "minecraft"
_ASSETS = "assets"


def missing_asset_pack_message() -> str:
    """Mensaje con instrucciones para generar el archivo de assets."""
    return _(
        "No se encontraron las texturas de Minecraft.\n\n"
        "Falta el archivo {name}. El programa no busca instalaciones de Minecraft "
        "ni resource packs en el disco.\n\n"
        "Genera el archivo en un equipo con Minecraft instalado:\n"
        "  lite2obj assets build --jar <ruta-al-client.jar>\n\n"
        "Después colócalo junto al programa, en los datos del usuario o define "
        "LITE2OBJ_ASSET_PACK."
    ).format(name=paths.ASSET_PACK_NAME)


class MissingAssetPackError(FileNotFoundError):
    """No hay ningún archivo de assets disponible."""


class InvalidAssetPackError(ValueError):
    """El archivo de assets existe pero no es válido."""


def parse_resource_id(
    value: str | None, default_namespace: str = DEFAULT_NAMESPACE
) -> tuple[str, str]:
    """Separa ``namespace:ruta`` en una tupla; sin ``:`` usa el namespace por defecto."""
    if not value:
        return default_namespace, ""
    if ":" in value:
        namespace, _, path = value.partition(":")
        return namespace or default_namespace, path
    return default_namespace, value


def blockstate_path(block: str) -> str:
    namespace, path = parse_resource_id(block)
    return f"{_ASSETS}/{namespace}/blockstates/{path}.json"


def model_path(model: str) -> str:
    namespace, path = parse_resource_id(model)
    return f"{_ASSETS}/{namespace}/models/{path}.json"


def texture_path(texture: str) -> str:
    namespace, path = parse_resource_id(texture)
    if not path.endswith(".png"):
        path = f"{path}.png"
    return f"{_ASSETS}/{namespace}/textures/{path}"


def items_model_path(item: str) -> str:
    namespace, path = parse_resource_id(item)
    return f"{_ASSETS}/{namespace}/items/{path}.json"


@runtime_checkable
class ResourceSource(Protocol):
    """Una capa de recursos (jar, zip de pack o carpeta)."""

    name: str

    def read(self, path: str) -> bytes | None: ...

    def exists(self, path: str) -> bool: ...

    def list(self, prefix: str) -> list[str]: ...


@dataclass(slots=True)
class ZipSource:
    """Resource pack o jar empaquetado en zip."""

    path: Path
    name: str = ""
    _zip: zipfile.ZipFile | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.name:
            self.name = self.path.stem

    @property
    def archive(self) -> zipfile.ZipFile:
        if self._zip is None:
            self._zip = zipfile.ZipFile(self.path)
        return self._zip

    def read(self, path: str) -> bytes | None:
        try:
            return self.archive.read(path)
        except KeyError, zipfile.BadZipFile:
            return None

    def exists(self, path: str) -> bool:
        try:
            self.archive.getinfo(path)
        except KeyError:
            return False
        return True

    def list(self, prefix: str) -> list[str]:
        return [n for n in self.archive.namelist() if n.startswith(prefix)]


@dataclass(slots=True)
class DirSource:
    """Resource pack descomprimido en disco."""

    path: Path
    name: str = ""

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.name:
            self.name = self.path.name

    def read(self, path: str) -> bytes | None:
        target = self.path / path
        return target.read_bytes() if target.is_file() else None

    def exists(self, path: str) -> bool:
        return (self.path / path).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self.path / prefix
        if not base.is_dir():
            return []
        return [f"{prefix}{p.relative_to(base).as_posix()}" for p in base.rglob("*") if p.is_file()]


@dataclass(slots=True)
class MemorySource:
    """Fuente en memoria, para tests y pruebas deterministas."""

    entries: dict[str, bytes]
    name: str = "memory"

    def read(self, path: str) -> bytes | None:
        return self.entries.get(path)

    def exists(self, path: str) -> bool:
        return path in self.entries

    def list(self, prefix: str) -> list[str]:
        return [k for k in self.entries if k.startswith(prefix)]


class ResourceManager:
    """Busca assets en una lista ordenada de fuentes (mayor prioridad primero)."""

    def __init__(self, sources: list[ResourceSource]) -> None:
        if not sources:
            raise ValueError(_("Se necesita al menos una fuente de recursos"))
        self.sources = sources
        self._json_cache: dict[str, dict[str, Any] | None] = {}
        self._texture_cache: dict[str, Image.Image | None] = {}
        self._missing: set[str] = set()

    # -- construcción ------------------------------------------------------ #

    @classmethod
    def discover(
        cls,
        packs: list[str | Path] | None = None,
        *,
        asset_pack: str | Path | None = None,
    ) -> ResourceManager:
        """Construye el gestor con el archivo de assets y packs explícitos.

        ``packs`` contiene rutas a archivos ``.zip`` o carpetas de resource pack; el
        último indicado tiene más prioridad. No se buscan packs por nombre ni en el disco.

        El archivo ``.l2oassets`` es obligatorio. Si falta, lanza
        :class:`MissingAssetPackError` con instrucciones para generarlo; el renderizador
        nunca cae al client jar.
        """
        pack_path = Path(asset_pack).expanduser() if asset_pack is not None else paths.asset_pack()
        if pack_path is None:
            raise MissingAssetPackError(missing_asset_pack_message())
        if not assetpack.is_asset_pack(pack_path):
            raise InvalidAssetPackError(
                _(
                    "El archivo de assets no es válido: {path}. "
                    "Genera uno con 'lite2obj assets build'."
                ).format(path=pack_path)
            )

        selected: list[ResourceSource] = []
        for item in packs or []:
            path = Path(item).expanduser()
            if path.is_file() and path.suffix.lower() == ".zip":
                selected.append(ZipSource(path))
            elif path.is_dir() and (path / _ASSETS).is_dir():
                selected.append(DirSource(path))
            else:
                raise FileNotFoundError(_("Resource pack no válido: {path}").format(path=path))

        # Prioridad: packs (el último indicado gana) y, por debajo, los assets vanilla.
        sources: list[ResourceSource] = list(reversed(selected))
        sources.append(ZipSource(pack_path, name=pack_path.stem))
        return cls(sources)

    # -- acceso ------------------------------------------------------------ #

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.sources]

    def read(self, path: str) -> bytes | None:
        for source in self.sources:
            data = source.read(path)
            if data is not None:
                return data
        self._missing.add(path)
        return None

    def exists(self, path: str) -> bool:
        return any(source.exists(path) for source in self.sources)

    def which(self, path: str) -> str | None:
        """Nombre de la fuente de mayor prioridad que contiene ``path``."""
        for source in self.sources:
            if source.exists(path):
                return source.name
        return None

    def read_json(self, path: str) -> dict[str, Any] | None:
        if path in self._json_cache:
            return self._json_cache[path]
        data = self.read(path)
        result: dict[str, Any] | None = None
        if data is not None:
            try:
                loaded = json.loads(data)
                result = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                result = None
        self._json_cache[path] = result
        return result

    def read_texture(self, texture: str) -> Image.Image | None:
        path = texture_path(texture)
        if path in self._texture_cache:
            return self._texture_cache[path]
        data = self.read(path)
        image: Image.Image | None = None
        if data is not None:
            try:
                with Image.open(BytesIO(data)) as handle:
                    handle.load()
                    image = handle.convert("RGBA")
            except OSError:
                image = None
        self._texture_cache[path] = image
        return image

    @property
    def missing(self) -> set[str]:
        """Rutas que se pidieron y no se encontraron en ninguna capa."""
        return set(self._missing)

    def iter_blockstates(self) -> Iterator[str]:
        """Todos los ``<namespace>:<bloque>`` con blockstate, recorriendo las capas."""
        seen: set[str] = set()
        for source in self.sources:
            for name in source.list(f"{_ASSETS}/"):
                marker = "/blockstates/"
                if marker not in name or not name.endswith(".json"):
                    continue
                prefix, _, rest = name.partition(f"{_ASSETS}/")
                namespace = rest.split("/", 1)[0]
                block = rest.split(marker, 1)[1][: -len(".json")]
                key = f"{namespace}:{block}"
                if key not in seen:
                    seen.add(key)
                    yield key
