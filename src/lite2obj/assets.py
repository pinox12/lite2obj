"""Pila de recursos de Minecraft (client jar + resource packs).

Resuelve rutas internas como ``assets/minecraft/models/block/oak_stairs.json`` aplicando
la misma prioridad que Minecraft: los resource packs del usuario tienen prioridad sobre
el jar vanilla, y cada asset se busca capa por capa (con fallback automático). Esto es
importante porque permite usar packs antiguos (p. ej. 1.20) sobre un cliente nuevo: los
assets que falten se toman del vanilla.
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

from . import paths

__all__ = [
    "DEFAULT_NAMESPACE",
    "DirSource",
    "MemorySource",
    "PackInfo",
    "ResourceManager",
    "ResourceSource",
    "ZipSource",
    "blockstate_path",
    "list_packs",
    "model_path",
    "parse_resource_id",
    "texture_path",
]

DEFAULT_NAMESPACE = "minecraft"
_ASSETS = "assets"


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


@dataclass(slots=True)
class PackInfo:
    """Resource pack disponible en la máquina."""

    name: str
    path: Path
    kind: str  # "zip" o "dir"

    def as_source(self) -> ResourceSource:
        return ZipSource(self.path) if self.kind == "zip" else DirSource(self.path)


def list_packs() -> list[PackInfo]:
    """Resource packs detectados, ordenados por nombre."""
    packs: list[PackInfo] = []
    seen: set[Path] = set()
    for directory in paths.resourcepack_dirs():
        for entry in sorted(directory.iterdir()):
            if entry in seen:
                continue
            if entry.is_file() and entry.suffix.lower() == ".zip":
                packs.append(PackInfo(name=entry.stem, path=entry, kind="zip"))
                seen.add(entry)
            elif entry.is_dir() and (entry / "assets").is_dir():
                packs.append(PackInfo(name=entry.name, path=entry, kind="dir"))
                seen.add(entry)
    return sorted(packs, key=lambda p: p.name.lower())


class ResourceManager:
    """Busca assets en una lista ordenada de fuentes (mayor prioridad primero)."""

    def __init__(self, sources: list[ResourceSource]) -> None:
        if not sources:
            raise ValueError("Se necesita al menos una fuente de recursos")
        self.sources = sources
        self._json_cache: dict[str, dict[str, Any] | None] = {}
        self._texture_cache: dict[str, Image.Image | None] = {}
        self._missing: set[str] = set()

    # -- construcción ------------------------------------------------------ #

    @classmethod
    def discover(cls, packs: list[str | Path] | None = None) -> ResourceManager:
        """Construye el gestor detectando el jar y aplicando los packs indicados.

        ``packs`` se interpreta en orden de prioridad creciente (el último gana),
        igual que la lista de packs de Minecraft. Los packs pueden indicarse por
        nombre (``"Mizuno's ..."``) o por ruta.
        """
        jar = paths.client_jar()
        if jar is None:
            raise FileNotFoundError(
                "No se encontró ningún client jar de Minecraft. Define LITE2OBJ_CLIENT_JAR."
            )

        available = list_packs()
        by_name = {p.name.lower(): p for p in available}
        selected: list[PackInfo] = []
        for item in packs or []:
            if isinstance(item, Path) or "/" in str(item) or str(item).endswith(".zip"):
                path = Path(item)
                kind = "zip" if path.suffix.lower() == ".zip" or path.is_file() else "dir"
                selected.append(PackInfo(name=path.stem, path=path, kind=kind))
                continue
            info = by_name.get(str(item).lower())
            if info is None:
                raise FileNotFoundError(
                    f"Resource pack no encontrado: {item!r}. Disponibles: "
                    f"{[p.name for p in available]}"
                )
            selected.append(info)

        # Prioridad: packs (el último indicado gana) y, por debajo, el jar vanilla.
        sources: list[ResourceSource] = [p.as_source() for p in reversed(selected)]
        sources.append(ZipSource(jar, name=jar.stem))
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
