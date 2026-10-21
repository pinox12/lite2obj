"""Generación de malla (F4).

Convierte un :class:`~lite2obj.litematic.Litematic` en una malla con posiciones, normales y
coordenadas UV de atlas. El proceso:

1. **Plantillas por estado de bloque**: para cada estado distinto se resuelve su geometría
   (F2), se aplica la rotación del variant alrededor del centro del bloque y se calculan las
   UVs. El resultado se guarda en coordenadas locales 0..1, de modo que colocar un bloque es
   sólo sumar su posición. Como los estados se repiten muchísimo, esto se hace una sola vez.
2. **Descarte de caras**: una cara con ``cullface`` se omite si el bloque vecino en esa
   dirección es un cubo completo opaco (y, entre vidrieras conectadas, por
   ``IronBarsBlock.skipRendering``), igual que hace Minecraft.
3. **Emisión**: se recorre la rejilla de vóxeles y se vuelcan las caras visibles.

Orden de caras y UV
-------------------
Cada cara se emite con 4 vértices en sentido antihorario visto desde fuera (normal hacia
fuera), listo para Blender y para el visor. El orden de vértices y la asignación de UV si
guen la convención del formato de modelos de Minecraft, verificada contra modelos reales del
jar en ``tests/test_mesh.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .fallbacks import fluid_height
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
    "rotate_direction",
    "rotate_uv",
    "rotate_uv_about",
    "uvlock_rotation",
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

#: Vuelta a dirección desde el vector unitario (para girar los ``cullface``).
_DIRECTION_BY_VECTOR: dict[tuple[int, int, int], str] = {
    (int(normal[0]), int(normal[1]), int(normal[2])): name for name, normal in FACE_NORMALS.items()
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

#: Dirección contraria, para comprobar la conexión del vecino.
_OPPOSITE: dict[str, str] = {
    "up": "down",
    "down": "up",
    "north": "south",
    "south": "north",
    "west": "east",
    "east": "west",
}

#: Etiqueta ``minecraft:bars`` del jar: barras que se ocultan entre sí (no las vidrieras).
_BARS_TAG = frozenset(
    {
        "minecraft:iron_bars",
        "minecraft:copper_bars",
        "minecraft:exposed_copper_bars",
        "minecraft:weathered_copper_bars",
        "minecraft:oxidized_copper_bars",
        "minecraft:waxed_copper_bars",
        "minecraft:waxed_exposed_copper_bars",
        "minecraft:waxed_weathered_copper_bars",
        "minecraft:waxed_oxidized_copper_bars",
    }
)

#: Etiqueta ``minecraft:wooden_fences``: las vallas de madera conectan entre sí, pero no
#: con la de ladrillo del Nether (que forma su propia familia).
_WOODEN_FENCES = frozenset(
    {
        "minecraft:oak_fence",
        "minecraft:acacia_fence",
        "minecraft:dark_oak_fence",
        "minecraft:pale_oak_fence",
        "minecraft:spruce_fence",
        "minecraft:birch_fence",
        "minecraft:jungle_fence",
        "minecraft:crimson_fence",
        "minecraft:warped_fence",
        "minecraft:mangrove_fence",
        "minecraft:bamboo_fence",
        "minecraft:cherry_fence",
        "minecraft:poplar_fence",
    }
)

#: Direcciones horizontales con su desplazamiento al vecino.
_HORIZONTAL: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("north", (0, 0, -1)),
    ("south", (0, 0, 1)),
    ("east", (1, 0, 0)),
    ("west", (-1, 0, 0)),
)

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


#: Ciclos de dirección que usa Minecraft al aplicar ``uvlock`` con ``x`` e ``y``.
_UVLOCK_X_CYCLE: dict[str, str] = {
    "north": "up",
    "up": "south",
    "south": "down",
    "down": "north",
}
_UVLOCK_Y_CYCLE: dict[str, str] = {
    "north": "east",
    "east": "south",
    "south": "west",
    "west": "north",
}

#: Centro (en píxeles) alrededor del que Minecraft gira las UV con ``uvlock``.
_UV_CENTER = 8.0


def uvlock_rotation(direction: str, x: int, y: int) -> float:
    """Ángulo que hay que girar las UV absolutas (0..16) para ``uvlock``.

    Minecraft no limita ``uvlock`` a las UV de la cara: gira los cuatro vértices de textura
    alrededor del píxel ``(8, 8)`` de la textura. Como las UV de una cara pueden ocupar sólo
    media textura (escaleras, losas, postes...), el giro también cambia la región muestreada;
    es justo lo que hace que una escalera boca abajo enseñe la mitad superior y no la inferior
    invertida. La secuencia de direcciones y los ángulos son los de ``uvlock`` de Minecraft.
    """
    angle = 0.0
    xn = x % 360
    effective = direction
    if xn:
        if direction == "east":
            angle += xn
        elif direction == "west":
            angle -= xn
        elif direction == "north":
            angle += 180
        elif direction == "south":
            angle += 180 if xn == 180 else 0
        elif direction == "up":
            angle += 180 if xn == 90 else 0
        elif direction == "down":
            angle += 180 if xn == 270 else 0
        for _ in range(xn // 90):
            effective = _UVLOCK_X_CYCLE.get(effective, effective)
    if y:
        for _ in range((y % 360) // 90):
            effective = _UVLOCK_Y_CYCLE.get(effective, effective)
        if effective in ("up", "down"):
            # ``up`` gira en un sentido y ``down`` en el contrario; un ``x`` de 90/270
            # intercambia ambos papeles.
            angle += y if (effective == "up") ^ (xn % 180 != 0) else -y
    return angle % 360


def rotate_uv_about(u: float, v: float, angle: float) -> tuple[float, float]:
    """Gira una UV absoluta alrededor del centro ``(8, 8)`` de la textura.

    Se usan casos exactos para los múltiplos de 90 (los únicos válidos en el formato de
    modelos), de modo que las UV no arrastran errores de coma flotante.
    """
    angle %= 360
    if not angle:
        return u, v
    du, dv = u - _UV_CENTER, v - _UV_CENTER
    if angle == 90:
        return _UV_CENTER - dv, _UV_CENTER + du
    if angle == 180:
        return _UV_CENTER - du, _UV_CENTER - dv
    if angle == 270:
        return _UV_CENTER + dv, _UV_CENTER - du
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)
    return (
        _UV_CENTER + du * cos - dv * sin,
        _UV_CENTER + du * sin + dv * cos,
    )


def rotate_direction(direction: str, matrix: np.ndarray) -> str:
    """Gira la dirección de un ``cullface`` con la matriz del variant.

    Minecraft rota el ``cullface`` junto con la geometría (``UnbakedCuboidGeometry``:
    ``Direction.rotate(modelState.transformation(), cullForDirection)``). Si no se gira, una
    escalera o una valla con ``y``/``x`` descarta caras contra el vecino equivocado: se ven
    huecos donde no toca y las vallas y escaleras parecen mal orientadas.
    """
    vector = matrix @ np.array(FACE_NORMALS[direction], dtype=float)
    key = (int(round(vector[0])), int(round(vector[1])), int(round(vector[2])))
    return _DIRECTION_BY_VECTOR[key]


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
    atlas_scale: int = 1
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
            atlas = TextureAtlas(
                resolver.resources,
                padding=self.options.padding,
                scale=self.options.atlas_scale,
            )
        self.atlas = atlas
        self.tints = TintResolver(
            enabled=self.options.tint,
            tints=self.options.tint_colors or dict(DEFAULT_TINTS),
        )
        self._states: dict[tuple[str, tuple[tuple[str, int, int, bool], ...], bool], StateMesh] = {}

    # -- plantillas -------------------------------------------------------- #

    def state_key(self, block: BlockState) -> str:
        return f"{block.name}[{block.variant_key()}]"

    def state_mesh(
        self,
        block: BlockState,
        position: tuple[int, int, int] | None = None,
        *,
        fluid_above: bool = False,
    ) -> StateMesh:
        """Geometría local del estado, con caché por estado y variant elegido.

        Los bloques de terreno traen varias rotaciones y Minecraft escoge una por posición,
        así que la plantilla se cachea por ``(estado, variants escogidos)``. ``fluid_above``
        forma parte de la clave: un fluido con otro encima ocupa el bloque entero y uno en
        la superficie queda a ``8/9``.
        """
        resolved = self.resolver.resolve(block)
        seed = position_seed(position) if position is not None else resolved.seed
        variants = pick_variants(resolved.groups, seed)
        signature = tuple((v.model, v.x, v.y, v.uvlock) for v in variants)
        key = (self.state_key(block), signature, fluid_above)
        cached = self._states.get(key)
        if cached is not None:
            return cached

        geometry = self.resolver.elements_for(resolved, variants, fluid_above=fluid_above)
        templates: list[QuadTemplate] = []
        # Con un solo elemento no puede haber caras coincidentes: se salta el agrupado.
        merge = len(geometry) > 1
        merged: dict[tuple[object, ...], int] = {}
        covers = False
        for element, variant in geometry:
            model = self.resolver.model(variant.model)
            matrix = variant_matrix(variant.x, variant.y)
            variant_rotation = bool(variant.x or variant.y)
            corners = element.corners()
            local = ((matrix @ (corners - BLOCK_CENTER).T).T + BLOCK_CENTER) / _MODEL_UNITS
            rotated = element.rotation is not None or variant_rotation
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
                # UV explícita del modelo (o derivada de la caja). Sin esto cada cara estira
                # la textura entera: en modelos con varias piezas en un mismo PNG (maceta,
                # farol, vallas, plantas...) cada cara enseñaba el dibujo completo.
                u0, v0, u1, v1 = face.uv_or_default(element.frm, element.to, direction)
                lock_angle = (
                    uvlock_rotation(direction, variant.x, variant.y) if variant.uvlock else 0.0
                )
                for slot, corner in enumerate(FACE_UV_ORDER[direction]):
                    s, t = rotate_uv(*UV_CORNERS[corner], face.rotation)
                    u = u0 + s * (u1 - u0)
                    v = v0 + t * (v1 - v0)
                    if lock_angle:
                        u, v = rotate_uv_about(u, v, lock_angle)
                    st[slot] = (u / _MODEL_UNITS, v / _MODEL_UNITS)
                positions = local[list(FACE_CORNERS[direction])].astype(np.float32)
                if rotated:
                    normal = _winding_normal(positions)
                else:
                    normal = np.array(FACE_NORMALS[direction], dtype=np.float32)
                cullface = face.cullface
                if cullface is not None and variant_rotation:
                    # El ``cullface`` viaja con el giro del variant (como Minecraft): si no,
                    # escaleras y vallas giradas descartan caras contra el vecino equivocado.
                    cullface = rotate_direction(cullface, matrix)
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
                        cullface=cullface,
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

    # -- fluidos ----------------------------------------------------------- #

    def _fluid(self, block: BlockState | None) -> str | None:
        """Nombre del fluido del bloque (``water``/``lava``) o ``None``."""
        if block is None:
            return None
        return self.resolver.fallbacks.is_fluid(block.path)

    def _fluid_above(
        self,
        voxels: dict[tuple[int, int, int], BlockState],
        position: tuple[int, int, int],
        fluid: str,
    ) -> bool:
        """Cierto si el bloque de encima contiene el mismo fluido."""
        above = voxels.get((position[0], position[1] + 1, position[2]))
        return self._fluid(above) == fluid

    def _fluid_culls(
        self,
        block: BlockState,
        neighbour: BlockState,
        direction: str,
        voxels: dict[tuple[int, int, int], BlockState],
        position: tuple[int, int, int],
        neighbour_pos: tuple[int, int, int],
    ) -> bool:
        """Descarta una cara de fluido contra otro bloque del mismo fluido.

        El descarte normal sólo mira bloques opacos, y el agua es translúcida: sin esta
        regla cada bloque dibuja sus seis caras y el interior de una masa de agua se ve
        como una rejilla de paredes superpuestas. En los laterales sólo se descarta si el
        vecino llega a nuestra altura (si es agua de un nivel inferior, la cara de arriba
        sigue siendo visible).
        """
        fluid = self._fluid(block)
        if fluid is None or self._fluid(neighbour) != fluid:
            return False
        if direction in ("up", "down"):
            return True
        my_height = fluid_height(
            _fluid_level(block),
            fluid_above=self._fluid_above(voxels, position, fluid),
        )
        their_height = fluid_height(
            _fluid_level(neighbour),
            fluid_above=self._fluid_above(voxels, neighbour_pos, fluid),
        )
        return their_height >= my_height - 1e-6

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

        voxels = _recompute_connections(voxels)

        placed: dict[tuple[int, int, int], StateMesh] = {}
        block_index: dict[str, int] = {}
        block_palette: list[BlockState] = []
        for position, block in voxels.items():
            key = self.state_key(block)
            if key not in block_index:
                block_index[key] = len(block_palette)
                block_palette.append(block)
            fluid = self._fluid(block)
            fluid_above = fluid is not None and self._fluid_above(voxels, position, fluid)
            placed[position] = self.state_mesh(block, position, fluid_above=fluid_above)

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
                    neighbour_pos = (x + offset[0], y + offset[1], z + offset[2])
                    if covers.get(neighbour_pos, False):
                        continue
                    neighbour = voxels.get(neighbour_pos)
                    if neighbour is not None and _skips_rendering(
                        state.block, neighbour, template.cullface
                    ):
                        continue
                    if neighbour is not None and self._fluid_culls(
                        state.block,
                        neighbour,
                        template.cullface,
                        voxels,
                        position,
                        neighbour_pos,
                    ):
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


def _block_prop(block: BlockState, name: str) -> bool:
    """Cierto si el estado tiene la propiedad booleana ``name`` a ``true``."""
    return any(key == name and value == "true" for key, value in block.properties)


def _fluid_level(block: BlockState) -> int:
    """Nivel de fluido del estado (0 = fuente); 0 si no lo declara."""
    try:
        return int(block.properties_dict().get("level", "0"))
    except ValueError:
        return 0


def _is_bars(block: BlockState) -> bool:
    """Cierto para los bloques de :class:`IronBarsBlock` (vidrieras y barras)."""
    return block.path.endswith(("_pane", "_bars"))


def _is_fence(block: BlockState) -> bool:
    return block.path.endswith("_fence")


def _same_fence(block: BlockState, neighbour: BlockState) -> bool:
    """Cierto si ambas vallas son de la misma familia (madera o ladrillo del Nether)."""
    return (block.name in _WOODEN_FENCES) == (neighbour.name in _WOODEN_FENCES)


def _with_props(block: BlockState, overrides: dict[str, bool]) -> BlockState:
    """Copia el estado cambiando sólo las propiedades indicadas."""
    return BlockState(
        block.name,
        tuple(
            (key, ("true" if overrides[key] else "false") if key in overrides else value)
            for key, value in block.properties
        ),
    )


def _connectable(block: BlockState, neighbour: BlockState) -> bool:
    """Cierto si ``block`` debería connectarse con ``neighbour`` (misma familia).

    Cubre los enlaces que nos interesan aquí: vidrieras/barras entre sí y vallas de la
    misma familia. Las conexiones contra caras sólidas se dejan como vengan en el archivo.
    """
    if _is_bars(block):
        return _is_bars(neighbour)
    if _is_fence(block):
        return _is_fence(neighbour) and _same_fence(block, neighbour)
    return False


def _recompute_connections(
    voxels: dict[tuple[int, int, int], BlockState],
) -> dict[tuple[int, int, int], BlockState]:
    """Normaliza las conexiones como una actualización de vecinos de Minecraft.

    Los ``.litematic`` guardan el estado tal cual estaba en el mundo. A veces una vidriera o
    valla se colocó antes que su vecina y se quedó con la conexión a ``false``; Minecraft la
    corrige al recibir la actualización del bloque nuevo, pero el archivo conserva el valor
    viejo. Hacemos lo mismo:

    - entre bloques conectables (vidrieras/barras, vallas de la misma familia) se fuerza
      ``true``;
    - si no hay bloque (aire o fuera de la región) se fuerza ``false``, para no dibujar
      raíles hacia la nada;
    - contra cualquier otro bloque se respeta el valor guardado, porque el archivo sí suele
      traer bien las conexiones con caras sólidas.
    """
    updated = dict(voxels)
    for position, block in voxels.items():
        if not (_is_bars(block) or _is_fence(block)):
            continue
        overrides: dict[str, bool] = {}
        for direction, (dx, dy, dz) in _HORIZONTAL:
            connected = _block_prop(block, direction)
            neighbour = voxels.get((position[0] + dx, position[1] + dy, position[2] + dz))
            if neighbour is None:
                wanted = False
            elif _connectable(block, neighbour):
                wanted = True
            else:
                wanted = connected
            if wanted != connected:
                overrides[direction] = wanted
        if overrides:
            updated[position] = _with_props(block, overrides)
    return updated


def _skips_rendering(block: BlockState, neighbour: BlockState, direction: str) -> bool:
    """Reproduce ``IronBarsBlock.skipRendering`` para la cara de unión entre paneles.

    Minecraft no dibuja el canto de una vidriera cuando el vecino es el mismo bloque (o
    una barra de la etiqueta ``minecraft:bars``) y **ambos** estados están conectados en
    ese eje. Sin esto, cada bloque de una vidriera continua deja una línea vertical con el
    sprite ``*_pane_top`` y los paneles parecen piezas sueltas.
    """
    if direction not in ("north", "south", "east", "west") or not _is_bars(block):
        return False
    if not (
        block.name == neighbour.name or (block.name in _BARS_TAG and neighbour.name in _BARS_TAG)
    ):
        return False
    opposite = _OPPOSITE[direction]
    return _block_prop(block, direction) and _block_prop(neighbour, opposite)


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
