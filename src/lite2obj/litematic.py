"""Lectura de esquemáticos ``.litematic`` (mod Litematica).

Un ``.litematic`` es un archivo NBT comprimido con gzip cuya raíz contiene:

``Regions``
    Compuesto ``nombre de región -> datos``. Cada región tiene ``Position``,
    ``Size`` (puede ser negativo, lo que indica la dirección), ``BlockStatePalette``,
    ``BlockStates`` (array empaquetado de longs) y listas de ``TileEntities`` /
    ``Entities``.
``Metadata``
    Nombre, autor, descripción, tamaños y contadores.
``Version`` / ``SubVersion`` / ``MinecraftDataVersion``
    Versiones del formato y del juego.

Los bloques se empaquetan en orden **YZX** con bit-packing contiguo
(``bits = ceil(log2(len(palette)))``), el mismo esquema que usa
``LitematicaBitArray`` en Litematica y ``BitStorage`` en Minecraft.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import nbt

__all__ = [
    "BlockState",
    "Litematic",
    "LitematicError",
    "Region",
    "load",
    "loads",
    "unpack_bits",
]


class LitematicError(ValueError):
    """El archivo no es un ``.litematic`` válido."""


@dataclass(frozen=True, slots=True)
class BlockState:
    """Un bloque de la paleta: identificador más propiedades de estado."""

    name: str
    properties: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_nbt(cls, entry: dict[str, Any]) -> BlockState:
        name = str(entry.get("Name", "minecraft:air"))
        props = entry.get("Properties") or {}
        return cls(name=name, properties=tuple(sorted((str(k), str(v)) for k, v in props.items())))

    @property
    def namespace(self) -> str:
        return self.name.split(":", 1)[0] if ":" in self.name else "minecraft"

    @property
    def path(self) -> str:
        """Ruta del bloque sin el namespace, p. ej. ``oak_stairs``."""
        return self.name.split(":", 1)[-1]

    @property
    def is_air(self) -> bool:
        return self.path in ("air", "void_air", "cave_air")

    def properties_dict(self) -> dict[str, str]:
        return dict(self.properties)

    def variant_key(self) -> str:
        """Clave de variante de blockstate, p. ej. ``facing=north,half=top``."""
        return ",".join(f"{k}={v}" for k, v in self.properties)

    def __str__(self) -> str:  # pragma: no cover - sólo presentación
        if not self.properties:
            return self.name
        return f"{self.name}[{self.variant_key()}]"


def unpack_bits(longs: np.ndarray, bits: int, count: int) -> np.ndarray:
    """Desempaqueta ``count`` valores de ``bits`` bits desde el array de longs.

    Implementa el bit-packing contiguo little-endian que usan Litematica y
    Minecraft: los valores pueden cruzar el límite entre dos longs.

    La operación es vectorizada para soportar paletas y volúmenes grandes.
    """
    if count <= 0:
        return np.zeros(0, dtype=np.int32)
    if bits <= 0:
        return np.zeros(count, dtype=np.int32)
    if bits > 32:
        raise LitematicError(f"Ancho de bit inválido: {bits}")

    longs = np.asarray(longs)
    if longs.dtype != np.uint64:
        longs = longs.astype(np.int64, copy=False).view(np.uint64)

    if longs.size * 64 < count * bits:
        raise LitematicError(
            f"BlockStates demasiado pequeño: {longs.size} longs para {count} entradas de "
            f"{bits} bits"
        )

    starts = np.arange(count, dtype=np.int64) * bits
    long_idx = (starts >> 6).astype(np.intp)
    bit_off = (starts & np.int64(63)).astype(np.uint64)

    out = longs[long_idx] >> bit_off

    # Los valores que cruzan el límite entre dos longs se completan con el
    # siguiente long desplazado.
    crossing = np.nonzero(((starts & 63) + bits) > 64)[0]
    if crossing.size:
        next_shift = (64 - bit_off[crossing]).astype(np.uint64)
        out[crossing] |= longs[long_idx[crossing] + 1] << next_shift

    out &= np.uint64((1 << bits) - 1)
    return out.astype(np.int32)


def _vec3(node: dict[str, Any]) -> tuple[int, int, int]:
    return (int(node.get("x", 0)), int(node.get("y", 0)), int(node.get("z", 0)))


@dataclass(slots=True)
class Region:
    """Una región del esquemático.

    ``grid`` es un array ``(size_y, size_z, size_x)`` de índices a ``palette``
    ya normalizado: las dimensiones negativas de Litematica se reflejan para que
    los índices del array crezcan siempre en la misma dirección que las
    coordenadas globales.
    """

    name: str
    origin: tuple[int, int, int]
    size: tuple[int, int, int]
    palette: list[BlockState]
    grid: np.ndarray
    tile_entities: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    air_mask: np.ndarray = field(default_factory=lambda: np.zeros(1, dtype=bool))

    @property
    def volume(self) -> int:
        sx, sy, sz = self.size
        return sx * sy * sz

    def block_at(self, x: int, y: int, z: int) -> BlockState:
        return self.palette[int(self.grid[y, z, x])]

    def counts(self) -> dict[BlockState, int]:
        """Recuento de bloques por estado, excluyendo los estados de aire."""
        flat = np.bincount(self.grid.ravel(), minlength=len(self.palette))
        return {
            self.palette[i]: int(n)
            for i, n in enumerate(flat)
            if n and i < len(self.palette) and not self.air_mask[i]
        }

    def iter_blocks(self, include_air: bool = False) -> Iterator[tuple[int, int, int, BlockState]]:
        """Itera ``(x, y, z, block)`` en coordenadas globales (con el origen sumado)."""
        ox, oy, oz = self.origin
        mask = self.air_mask
        for y in range(self.size[1]):
            layer = self.grid[y]
            for z in range(self.size[2]):
                row = layer[z]
                for x in range(self.size[0]):
                    index = int(row[x])
                    if not include_air and mask[index]:
                        continue
                    yield ox + x, oy + y, oz + z, self.palette[index]

    def palette_counts(self) -> np.ndarray:
        return np.bincount(self.grid.ravel(), minlength=len(self.palette))


@dataclass(slots=True)
class Litematic:
    """Contenido completo de un archivo ``.litematic``."""

    name: str
    author: str
    description: str
    regions: list[Region]
    version: int = 0
    sub_version: int = 0
    data_version: int = 0
    total_blocks: int = 0
    total_volume: int = 0
    time_created: int = 0
    time_modified: int = 0
    source: Path | None = None

    @property
    def block_count(self) -> int:
        """Número de bloques no-aire, calculado desde las regiones.

        Se excluyen todos los estados de aire (``air``, ``cave_air``, ``void_air``),
        igual que hace Litematica al calcular ``Metadata.TotalBlocks``.
        """
        return sum(count for block, count in self.counts().items() if not block.is_air)

    @property
    def enclosing_size(self) -> tuple[int, int, int]:
        """Tamaño de la caja que engloba todas las regiones."""
        xs = [r.origin[0] + r.size[0] for r in self.regions]
        ys = [r.origin[1] + r.size[1] for r in self.regions]
        zs = [r.origin[2] + r.size[2] for r in self.regions]
        min_x = min(r.origin[0] for r in self.regions)
        min_y = min(r.origin[1] for r in self.regions)
        min_z = min(r.origin[2] for r in self.regions)
        return (max(xs) - min_x, max(ys) - min_y, max(zs) - min_z)

    @property
    def origin(self) -> tuple[int, int, int]:
        """Esquina mínima global (para normalizar coordenadas a 0..size)."""
        return (
            min(r.origin[0] for r in self.regions),
            min(r.origin[1] for r in self.regions),
            min(r.origin[2] for r in self.regions),
        )

    def counts(self) -> dict[BlockState, int]:
        """Recuento de bloques combinando todas las regiones."""
        merged: dict[BlockState, int] = {}
        for region in self.regions:
            for block, count in region.counts().items():
                merged[block] = merged.get(block, 0) + count
        return merged

    def block_counts(self, include_air: bool = False) -> dict[str, int]:
        """Recuento agregado por id de bloque (sin propiedades), ya ordenado."""
        merged: dict[str, int] = {}
        for block, count in self.counts().items():
            if not include_air and block.is_air:
                continue
            merged[block.name] = merged.get(block.name, 0) + count
        return dict(sorted(merged.items(), key=lambda kv: (-kv[1], kv[0])))

    def iter_blocks(self, include_air: bool = False) -> Iterator[tuple[int, int, int, BlockState]]:
        for region in self.regions:
            yield from region.iter_blocks(include_air=include_air)


def _build_region(name: str, data: dict[str, Any]) -> Region:
    size_raw = _vec3(data.get("Size", {}))
    position = _vec3(data.get("Position", {}))

    abs_size = tuple(abs(v) for v in size_raw)
    directions = tuple(1 if v >= 0 else -1 for v in size_raw)
    sx, sy, sz = abs_size

    palette = [BlockState.from_nbt(entry) for entry in data.get("BlockStatePalette", [])]
    if not palette:
        palette = [BlockState("minecraft:air")]

    count = sx * sy * sz
    bits = (len(palette) - 1).bit_length()
    longs = np.asarray(data.get("BlockStates", np.zeros(0, dtype=np.int64)), dtype=np.int64)
    raw = unpack_bits(longs, bits, count) if bits else np.zeros(count, dtype=np.int32)

    grid = raw.reshape(sy, sz, sx)

    # Refleja las dimensiones negativas para que el índice del array crezca en la
    # misma dirección que las coordenadas globales.
    if directions[0] < 0:
        grid = grid[:, :, ::-1]
    if directions[1] < 0:
        grid = grid[::-1, :, :]
    if directions[2] < 0:
        grid = grid[:, ::-1, :]
    grid = np.ascontiguousarray(grid, dtype=np.int32)

    # Esquina mínima de la región en coordenadas globales.
    origin = tuple(position[i] - (abs_size[i] - 1 if directions[i] < 0 else 0) for i in range(3))

    return Region(
        name=name,
        origin=origin,  # type: ignore[arg-type]
        size=abs_size,  # type: ignore[arg-type]
        palette=palette,
        grid=grid,
        tile_entities=list(data.get("TileEntities") or []),
        entities=list(data.get("Entities") or []),
        air_mask=np.array([b.is_air for b in palette], dtype=bool),
    )


def loads(data: bytes, *, source: Path | None = None) -> Litematic:
    """Parsea los bytes de un ``.litematic`` (comprimido o no)."""
    root = nbt.loads(data)
    if not isinstance(root, dict):
        raise LitematicError("La raíz NBT no es un compuesto")

    regions_node = root.get("Regions")
    if not isinstance(regions_node, dict) or not regions_node:
        raise LitematicError("El archivo no contiene 'Regions'")

    regions = [_build_region(name, data_) for name, data_ in regions_node.items()]

    metadata = root.get("Metadata") or {}
    return Litematic(
        name=str(metadata.get("Name", "")),
        author=str(metadata.get("Author", "")),
        description=str(metadata.get("Description", "")),
        regions=regions,
        version=int(root.get("Version", 0)),
        sub_version=int(root.get("SubVersion", 0)),
        data_version=int(root.get("MinecraftDataVersion", 0)),
        total_blocks=int(metadata.get("TotalBlocks", 0)),
        total_volume=int(metadata.get("TotalVolume", 0)),
        time_created=int(metadata.get("TimeCreated", 0)),
        time_modified=int(metadata.get("TimeModified", 0)),
        source=source,
    )


def load(path: str | Path) -> Litematic:
    """Carga un archivo ``.litematic`` desde disco."""
    path = Path(path)
    return loads(path.read_bytes(), source=path)
