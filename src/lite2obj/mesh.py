"""Generación de malla (F4).

Convierte un :class:`~lite2obj.litematic.Litematic` en una malla con posiciones, normales y
coordenadas UV de atlas. El proceso:

1. **Plantillas por estado de bloque**: para cada estado distinto se resuelve su geometría
   (F2), se aplica la rotación del variant alrededor del centro del bloque y se calculan las
   UVs. El resultado se guarda en coordenadas locales 0..1, de modo que colocar un bloque es
   sólo sumar su posición. Como los estados se repiten muchísimo, esto se hace una sola vez.
2. **Descarte de caras**: una cara con ``cullface`` se omite si el bloque vecino en esa
   dirección es un cubo completo opaco, igual que hace Minecraft.
3. **Emisión**: se recorre la rejilla de vóxeles y se vuelcan las caras visibles.

Orden de caras y UV
-------------------
Cada cara se emite con 4 vértices en sentido antihorario visto desde fuera (normal hacia
fuera), listo para Blender y para el visor. El orden de vértices y la asignación de UV si
guen la convención del formato de modelos de Minecraft, verificada contra modelos reales del
jar en ``tests/test_mesh.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .litematic import BlockState, Litematic
from .models import (
    BLOCK_CENTER,
    Element,
    ModelResolver,
    Variant,
    pick_variants,
    position_seed,
    variant_matrix,
)
from .textures import DEFAULT_TINTS, Sprite, TextureAtlas, TintResolver

__all__ = [
    "FACE_CORNERS",
    "FACE_NORMALS",
    "NEIGHBOURS",
    "Mesh",
    "MeshBuilder",
    "MeshOptions",
    "QuadTemplate",
    "StateMesh",
    "UV_CORNERS",
    "rotate_uv",
]


# --------------------------------------------------------------------------- #
# Tablas de caras
# --------------------------------------------------------------------------- #

#: Orden de las esquinas de cada cara (índices en la lista de 8 esquinas del elemento).
#:
#: El orden de ``Element.corners`` es::
#:
#:   0:(x0,y0,z0) 1:(x1,y0,z0) 2:(x1,y1,z0) 3:(x0,y1,z0)
#:   4:(x0,y0,z1) 5:(x1,y0,z1) 6:(x1,y1,z1) 7:(x0,y1,z1)
FACE_CORNERS: dict[str, tuple[int, int, int, int]] = {
    "up": (3, 7, 6, 2),
    "down": (4, 0, 1, 5),
    "north": (1, 0, 3, 2),
    "south": (4, 5, 6, 7),
    "west": (0, 4, 7, 3),
    "east": (5, 1, 2, 6),
}

#: Esquinas del rectángulo UV, en el mismo orden que :data:`FACE_CORNERS`.
#: ``0 = (u0,v0)``, ``1 = (u0,v1)``, ``2 = (u1,v1)``, ``3 = (u1,v0)`` (v hacia abajo).
FACE_UV_ORDER: dict[str, tuple[int, int, int, int]] = {
    "up": (0, 1, 2, 3),
    "down": (0, 1, 2, 3),
    "north": (1, 2, 3, 0),
    "south": (1, 2, 3, 0),
    "west": (1, 2, 3, 0),
    "east": (1, 2, 3, 0),
}

UV_CORNERS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))

FACE_NORMALS: dict[str, tuple[float, float, float]] = {
    "up": (0.0, 1.0, 0.0),
    "down": (0.0, -1.0, 0.0),
    "north": (0.0, 0.0, -1.0),
    "south": (0.0, 0.0, 1.0),
    "west": (-1.0, 0.0, 0.0),
    "east": (1.0, 0.0, 0.0),
}

#: Desplazamiento al bloque vecino en cada dirección.
NEIGHBOURS: dict[str, tuple[int, int, int]] = {
    "up": (0, 1, 0),
    "down": (0, -1, 0),
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
}

_MODEL_UNITS = 16.0


def _winding_normal(quad: np.ndarray) -> np.ndarray:
    """Normal unitaria según el giro de los vértices (hacia fuera).

    Se usa en vez de girar el vector de la tabla cuando hay rotaciones: hay modelos con
    elementos planos (las cruces de las flores, con grosor 0) donde la matriz de ``rescale``
    es singular y su inversa traspuesta no existe. El producto vectorial siempre es válido
    para una cara que se vaya a ver.
    """
    vector = np.cross(quad[1] - quad[0], quad[2] - quad[0])
    length = float(np.linalg.norm(vector))
    if length < 1e-9:
        return np.zeros(3, dtype=np.float32)
    return (vector / length).astype(np.float32)


def rotate_uv(s: float, t: float, rotation: int) -> tuple[float, float]:
    """Rota una coordenada UV normalizada dentro de su cara (``rotation`` en grados)."""
    rotation %= 360
    if rotation == 90:
        return t, 1.0 - s
    if rotation == 180:
        return 1.0 - s, 1.0 - t
    if rotation == 270:
        return 1.0 - t, s
    return s, t


# --------------------------------------------------------------------------- #
# Plantillas
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class QuadTemplate:
    """Una cara ya rotada, en coordenadas locales del bloque (0..1)."""

    positions: np.ndarray  # (4, 3) float32
    normal: np.ndarray  # (3,) float32
    st: np.ndarray  # (4, 2) float32, dentro del sprite (0..1)
    sprite: Sprite | None
    cullface: str | None
    alpha_mode: int
    tintindex: int | None


@dataclass(slots=True)
class StateMesh:
    """Geometría local de un estado de bloque, lista para colocar en el mundo."""

    block: BlockState
    templates: tuple[QuadTemplate, ...]
    covers: bool  # cubo completo opaco: oculta las caras de los vecinos
    uvs: np.ndarray = field(default_factory=lambda: np.zeros((0, 4, 2), dtype=np.float32))
    sprite_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int32))
    alpha_modes: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))

    @property
    def quad_count(self) -> int:
        return len(self.templates)

    def finalize(self, atlas: TextureAtlas) -> None:
        """Calcula UVs e índices de sprite definitivos (requiere el atlas ya empaquetado)."""
        width, height = atlas.size
        uvs = np.zeros((self.quad_count, 4, 2), dtype=np.float32)
        sprite_ids = np.zeros(self.quad_count, dtype=np.int32)
        alpha_modes = np.zeros(self.quad_count, dtype=np.int8)
        for index, template in enumerate(self.templates):
            sprite = template.sprite
            alpha_modes[index] = template.alpha_mode
            if sprite is None:
                continue
            sprite_ids[index] = sprite.index
            u0 = sprite.x / width
            v0 = sprite.y / height
            du = sprite.width / width
            dv = sprite.height / height
            uvs[index, :, 0] = u0 + template.st[:, 0] * du
            uvs[index, :, 1] = v0 + template.st[:, 1] * dv
        self.uvs = uvs
        self.sprite_ids = sprite_ids
        self.alpha_modes = alpha_modes


# --------------------------------------------------------------------------- #
# Malla resultante
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Mesh:
    """Malla completa del esquemático.

    ``quads`` indexa ``positions``/``uvs``/``normals`` (4 vértices por cara). Los vértices
    están en unidades de bloque, con la esquina mínima del esquemático en el origen y el eje
    Y hacia arriba (igual que Minecraft).
    """

    positions: np.ndarray  # (N, 3) float32
    uvs: np.ndarray  # (N, 2) float32
    normals: np.ndarray  # (N, 3) float32
    quads: np.ndarray  # (Q, 4) int32
    sprite: np.ndarray  # (Q,) int32
    alpha_mode: np.ndarray  # (Q,) int8
    block: np.ndarray  # (Q,) int32 (índice en ``palette``)
    palette: list[BlockState]
    atlas: TextureAtlas
    size: tuple[int, int, int]
    origin: tuple[int, int, int] = (0, 0, 0)
    partial: bool = False

    @property
    def quad_count(self) -> int:
        return int(self.quads.shape[0])

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    def triangle_indices(self) -> np.ndarray:
        """Índices de triángulos (2 por cara) para el visor."""
        quads = self.quads
        return np.concatenate([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]], axis=0)

    def stats(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for index in self.block:
            name = self.palette[int(index)].name
            counts[name] = counts.get(name, 0) + 1
        return {
            "quads": self.quad_count,
            "vertices": self.vertex_count,
            "triangles": self.quad_count * 2,
            "distinct_blocks": len(self.palette),
            "sprites": self.atlas.sprite_count,
            "atlas": self.atlas.size,
            "size": self.size,
            "partial": self.partial,
            "top_blocks": sorted(counts.items(), key=lambda kv: -kv[1])[:10],
        }


@dataclass(slots=True)
class MeshOptions:
    """Opciones de generación."""

    cull: bool = True
    fallback_mode: str = "cube"
    tint: bool = True
    tint_colors: dict[str, tuple[int, int, int]] | None = None
    padding: int = 2
    max_quads: int = 0  # 0 = sin límite
    progress: Callable[[int, int], None] | None = None


class _Buffer:
    """Búfer de tamaño variable para posiciones/UVs/normales."""

    def __init__(self, initial: int) -> None:
        capacity = max(initial, 64)
        self.positions = np.zeros((capacity, 3), dtype=np.float32)
        self.uvs = np.zeros((capacity, 2), dtype=np.float32)
        self.normals = np.zeros((capacity, 3), dtype=np.float32)
        self.cursor = 0

    def ensure(self, extra: int) -> None:
        needed = self.cursor + extra
        capacity = self.positions.shape[0]
        if needed <= capacity:
            return
        new_capacity = max(capacity * 2, needed)
        for name in ("positions", "uvs", "normals"):
            old = getattr(self, name)
            new = np.zeros((new_capacity, old.shape[1]), dtype=np.float32)
            new[: self.cursor] = old[: self.cursor]
            setattr(self, name, new)

    def add_quad(self, positions: np.ndarray, uvs: np.ndarray, normal: np.ndarray) -> None:
        self.ensure(4)
        start = self.cursor
        self.positions[start : start + 4] = positions
        self.uvs[start : start + 4] = uvs
        self.normals[start : start + 4] = normal
        self.cursor += 4


class MeshBuilder:
    """Construye la malla de un esquemático reutilizando plantillas por estado."""

    def __init__(
        self,
        resolver: ModelResolver,
        atlas: TextureAtlas | None = None,
        options: MeshOptions | None = None,
    ) -> None:
        self.resolver = resolver
        self.options = options or MeshOptions()
        if atlas is None:
            atlas = TextureAtlas(resolver.resources, padding=self.options.padding)
        self.atlas = atlas
        self.tints = TintResolver(
            enabled=self.options.tint,
            tints=self.options.tint_colors or dict(DEFAULT_TINTS),
        )
        self._states: dict[tuple[str, tuple[tuple[str, int, int, bool], ...]], StateMesh] = {}

    # -- plantillas -------------------------------------------------------- #

    def state_key(self, block: BlockState) -> str:
        return f"{block.name}[{block.variant_key()}]"

    def state_mesh(
        self, block: BlockState, position: tuple[int, int, int] | None = None
    ) -> StateMesh:
        """Geometría local del estado, con caché por estado y variant elegido.

        Los bloques de terreno traen varias rotaciones y Minecraft escoge una por posición,
        así que la plantilla se cachea por ``(estado, variants escogidos)``.
        """
        resolved = self.resolver.resolve(block)
        seed = position_seed(position) if position is not None else resolved.seed
        variants = pick_variants(resolved.groups, seed)
        signature = tuple((v.model, v.x, v.y, v.uvlock) for v in variants)
        key = (self.state_key(block), signature)
        cached = self._states.get(key)
        if cached is not None:
            return cached

        geometry = self.resolver.elements_for(resolved, variants)
        templates: list[QuadTemplate] = []
        # Con un solo elemento no puede haber caras coincidentes: se salta el agrupado.
        merge = len(geometry) > 1
        merged: dict[tuple[object, ...], int] = {}
        covers = False
        for element, variant in geometry:
            model = self.resolver.model(variant.model)
            matrix = variant_matrix(variant.x, variant.y)
            corners = element.corners()
            local = ((matrix @ (corners - BLOCK_CENTER).T).T + BLOCK_CENTER) / _MODEL_UNITS
            rotated = element.rotation is not None or bool(variant.x or variant.y)
            element_opaque = True
            for direction, face in element.faces.items():
                sprite = self.atlas.add(
                    model.texture(face.texture),
                    self.tints.tint_for(block.name, face.tintindex),
                )
                alpha_mode = self.atlas.alpha_mode(sprite)
                if alpha_mode != 0:
                    element_opaque = False
                st = np.zeros((4, 2), dtype=np.float32)
                for slot, corner in enumerate(FACE_UV_ORDER[direction]):
                    st[slot] = rotate_uv(*UV_CORNERS[corner], face.rotation)
                positions = local[list(FACE_CORNERS[direction])].astype(np.float32)
                if rotated:
                    normal = _winding_normal(positions)
                else:
                    normal = np.array(FACE_NORMALS[direction], dtype=np.float32)
                if merge:
                    # Dos caras en el mismo plano (la hierba y su overlay) se hornean en una:
                    # así el visor y Blender enseñan lo mismo que Minecraft.
                    plane = (direction, *np.round(positions, 4).ravel().tolist())
                    previous = merged.get(plane)
                    if previous is not None:
                        quad = templates[previous]
                        quad.sprite = self.atlas.combine(quad.sprite, sprite)
                        quad.alpha_mode = max(quad.alpha_mode, self.atlas.alpha_mode(quad.sprite))
                        continue
                    merged[plane] = len(templates)
                templates.append(
                    QuadTemplate(
                        positions=positions,
                        normal=normal,
                        st=st,
                        sprite=sprite,
                        cullface=face.cullface,
                        alpha_mode=alpha_mode,
                        tintindex=face.tintindex,
                    )
                )
            # Ocluye si *algún* elemento es el cubo unidad y sus texturas son opacas: así los
            # bloques con una capa encima (la hierba y su overlay) siguen tapando lo de dentro.
            if element_opaque and _is_full_cube(element, variant):
                covers = True

        state = StateMesh(
            block=block,
            templates=tuple(templates),
            covers=covers,
        )
        self._states[key] = state
        return state

    # -- construcción ------------------------------------------------------ #

    def build(self, doc: Litematic) -> Mesh:
        """Genera la malla completa del esquemático."""
        options = self.options
        origin = doc.origin

        voxels: dict[tuple[int, int, int], BlockState] = {}
        for region in doc.regions:
            ox, oy, oz = region.origin
            grid = region.grid
            palette = region.palette
            mask = region.air_mask
            for y in range(region.size[1]):
                layer = grid[y]
                for z in range(region.size[2]):
                    row = layer[z]
                    for x in range(region.size[0]):
                        index = int(row[x])
                        if not mask[index]:
                            voxels[(ox + x, oy + y, oz + z)] = palette[index]

        placed: dict[tuple[int, int, int], StateMesh] = {}
        block_index: dict[str, int] = {}
        block_palette: list[BlockState] = []
        for position, block in voxels.items():
            key = self.state_key(block)
            if key not in block_index:
                block_index[key] = len(block_palette)
                block_palette.append(block)
            placed[position] = self.state_mesh(block, position)

        self.atlas.build()
        finalized: set[int] = set()
        for state in placed.values():
            marker = id(state)
            if marker not in finalized:
                finalized.add(marker)
                state.finalize(self.atlas)

        covers = {position: state.covers for position, state in placed.items()}

        buffer = _Buffer(len(placed))
        meta: list[tuple[int, int, int, int]] = []
        partial = False
        total = len(placed)
        limit = options.max_quads

        for done, (position, state) in enumerate(placed.items()):
            if options.progress is not None and done % 20000 == 0:
                options.progress(done, total)
            index = block_index[self.state_key(state.block)]
            x, y, z = position
            for quad_index, template in enumerate(state.templates):
                if options.cull and template.cullface is not None:
                    offset = NEIGHBOURS[template.cullface]
                    if covers.get((x + offset[0], y + offset[1], z + offset[2]), False):
                        continue
                if limit and len(meta) >= limit:
                    partial = True
                    break
                positions = template.positions + (x - origin[0], y - origin[1], z - origin[2])
                start = buffer.cursor
                buffer.add_quad(positions, state.uvs[quad_index], template.normal)
                meta.append(
                    (
                        start,
                        int(state.sprite_ids[quad_index]),
                        int(state.alpha_modes[quad_index]),
                        index,
                    )
                )
            if partial:
                break

        if options.progress is not None:
            options.progress(total, total)

        count = buffer.cursor
        quads = (
            np.arange(count, dtype=np.int32).reshape(-1, 4)
            if count
            else np.zeros((0, 4), dtype=np.int32)
        )
        meta_array = np.array(meta, dtype=np.int32) if meta else np.zeros((0, 4), dtype=np.int32)
        return Mesh(
            positions=buffer.positions[:count].copy(),
            uvs=buffer.uvs[:count].copy(),
            normals=buffer.normals[:count].copy(),
            quads=quads,
            sprite=meta_array[:, 1].copy(),
            alpha_mode=meta_array[:, 2].astype(np.int8),
            block=meta_array[:, 3].copy(),
            palette=block_palette,
            atlas=self.atlas,
            size=doc.enclosing_size,
            origin=origin,
            partial=partial,
        )


def _is_full_cube(element: Element, variant: Variant) -> bool:
    """Cierto si el elemento es el cubo 0..16 con una rotación de 90º (que lo deja igual).

    Un cubo girado sigue siendo el cubo: si sólo mirásemos ``x == 0 and y == 0`` los bloques
    de terreno (que traen cuatro rotaciones) no ocultarían nada y no habría descarte de caras.
    """
    if element.rotation is not None:
        return False
    if variant.x % 90 or variant.y % 90:
        return False
    return element.frm == (0.0, 0.0, 0.0) and element.to == (16.0, 16.0, 16.0)
