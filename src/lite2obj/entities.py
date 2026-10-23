"""Entidades y *block entities* de Minecraft.

El cliente dibuja muchas cosas que no están en la rejilla de bloques: cofres, marcos de
objeto con su contenido, cuadros, estandartes, cajas de shulker, calaveras, botes, carritos,
armaduras... y todas las criaturas. Sus modelos no viven en ``assets/.../models`` sino en
clases Java, así que este módulo trae una instantánea de los árboles de ``ModelPart`` ya
horneados (``entity_models.json``) y los coloca con las mismas matrices que usa vanilla.

La geometría resultante se devuelve como plantillas de :mod:`lite2obj.mesh`, de modo que
comparte atlas, materiales y exportación con los bloques.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import mesh as _mesh
from .items import ItemRenderer
from .litematic import BlockState
from .models import ModelResolver, rot_x, rot_y, rot_z
from .textures import TextureAtlas

#: Colores de tinte de los 16 tintes de Minecraft (``DyeColor.getTextureDiffuseColor``).
_DYE_COLORS: tuple[tuple[int, int, int], ...] = (
    (249, 255, 254),
    (249, 128, 29),
    (199, 78, 189),
    (58, 179, 218),
    (254, 216, 61),
    (128, 199, 31),
    (243, 139, 170),
    (66, 70, 73),
    (154, 161, 161),
    (21, 137, 145),
    (121, 42, 172),
    (53, 57, 157),
    (114, 71, 40),
    (84, 109, 27),
    (161, 39, 34),
    (20, 21, 25),
)

_DYE_NAMES: tuple[str, ...] = (
    "white",
    "orange",
    "magenta",
    "light_blue",
    "yellow",
    "lime",
    "pink",
    "gray",
    "light_gray",
    "cyan",
    "purple",
    "blue",
    "brown",
    "green",
    "red",
    "black",
)

#: ``Direction.toYRot``: sur = 0, oeste = 90, norte = 180, este = 270.
_Y_ROT = {"south": 0.0, "west": 90.0, "north": 180.0, "east": 270.0}

#: ``Direction`` por su id legacy (el que guarda ``Facing`` en el NBT de los marcos).
_FACING_LEGACY: tuple[str, ...] = ("down", "up", "north", "south", "west", "east")

#: Material del cofre según el bloque.
_CHEST_MATERIALS: dict[str, str] = {
    "chest": "normal",
    "trapped_chest": "trapped",
    "ender_chest": "ender",
    "copper_chest": "copper",
    "exposed_copper_chest": "copper_exposed",
    "weathered_copper_chest": "copper_weathered",
    "oxidized_copper_chest": "copper_oxidized",
    "waxed_copper_chest": "copper",
    "waxed_exposed_copper_chest": "copper_exposed",
    "waxed_weathered_copper_chest": "copper_weathered",
    "waxed_oxidized_copper_chest": "copper_oxidized",
}

#: Contenido que dibuja cada vagoneta. ``displayOffset`` es el de vanilla (por defecto 6).
_MINECART_CONTENTS: dict[str, str] = {
    "chest_minecart": "chest",
    "hopper_minecart": "hopper",
    "furnace_minecart": "furnace",
    "tnt_minecart": "tnt",
    "command_block_minecart": "command_block",
    "spawner_minecart": "spawner",
}

_MINECART_OFFSETS: dict[str, int] = {"chest_minecart": 8, "hopper_minecart": 1}


#: Modelos cuyo renderer aplica la escala ``0.9375``. En 26.3 sólo el avatar lo hace;
#: las criaturas ya llevan su escala en la raíz del modelo o se dibujan a tamaño completo.
_HUMANOID = frozenset({"player", "player_slim"})

#: Entidades cuyo identificador no coincide con el nombre de su capa de modelo.
_ENTITY_LAYERS: dict[str, str] = {
    "oak_boat": "boat/oak",
    "spruce_boat": "boat/spruce",
    "birch_boat": "boat/birch",
    "jungle_boat": "boat/jungle",
    "acacia_boat": "boat/acacia",
    "cherry_boat": "boat/cherry",
    "dark_oak_boat": "boat/dark_oak",
    "mangrove_boat": "boat/mangrove",
    "pale_oak_boat": "boat/pale_oak",
    "bamboo_raft": "boat/bamboo",
    "poplar_boat": "boat/poplar",
    "oak_chest_boat": "chest_boat/oak",
    "spruce_chest_boat": "chest_boat/spruce",
    "birch_chest_boat": "chest_boat/birch",
    "jungle_chest_boat": "chest_boat/jungle",
    "acacia_chest_boat": "chest_boat/acacia",
    "cherry_chest_boat": "chest_boat/cherry",
    "dark_oak_chest_boat": "chest_boat/dark_oak",
    "mangrove_chest_boat": "chest_boat/mangrove",
    "pale_oak_chest_boat": "chest_boat/pale_oak",
    "bamboo_chest_raft": "chest_boat/bamboo",
    "poplar_chest_boat": "chest_boat/poplar",
    "hopper_minecart": "hopper_minecart",
    "chest_minecart": "chest_minecart",
    "furnace_minecart": "furnace_minecart",
    "tnt_minecart": "tnt_minecart",
    "command_block_minecart": "command_block_minecart",
    "spawner_minecart": "spawner_minecart",
    "glow_item_frame": "item_frame",
    "item_frame": "item_frame",
    "tropical_fish": "tropical_fish_large",
    "salmon": "salmon",
    "pufferfish": "pufferfish_medium",
    "magma_cube": "magma_cube",
    "mooshroom": "mooshroom",
    "mushroom_cow": "mooshroom",
}

#: Criaturas a las que hay que aplicar la escala del renderer (no la de la raíz).
_LAYER_HUMANOID = _HUMANOID

#: Colores/nombres de variantes que usan el sufijo de la textura.
_HORSE_COLORS = ("white", "creamy", "chestnut", "brown", "black", "gray", "darkbrown")
_RABBIT_VARIANTS = ("brown", "white", "black", "white_splotched", "gold", "salt", "caerbannog")
_PARROT_VARIANTS = ("red_blue", "blue", "green", "yellow_blue", "grey")
_AXOLOTL_VARIANTS = ("lucy", "wild", "gold", "cyan", "blue")
_LLAMA_VARIANTS = ("creamy", "white", "brown", "gray")
_WOLF_VARIANTS = (
    "pale",
    "spotted",
    "snowy",
    "black",
    "ashen",
    "rusty",
    "woods",
    "chestnut",
    "striped",
)
_CAT_VARIANTS = (
    "tabby",
    "black",
    "red",
    "siamese",
    "british_shorthair",
    "calico",
    "persian",
    "ragdoll",
    "white",
    "jellie",
    "all_black",
)


# --------------------------------------------------------------------------- #
# Datos empaquetados
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def _models() -> dict[str, dict[str, Any]]:
    path = Path(__file__).with_name("entity_models.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    layers = data.get("layers", data)
    return {str(key): value for key, value in layers.items()}


@lru_cache(maxsize=1)
def _textures() -> dict[str, str]:
    path = Path(__file__).with_name("entity_textures.json")
    return {str(k): str(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


# --------------------------------------------------------------------------- #
# Matemáticas
# --------------------------------------------------------------------------- #


def _m4(rotation: np.ndarray) -> np.ndarray:
    """Convierte una matriz 3x3 en una homogénea 4x4."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    return matrix


def _translate(x: float, y: float, z: float) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, 3] = (x, y, z)
    return matrix


def _scale(x: float, y: float, z: float) -> np.ndarray:
    matrix = np.eye(4)
    matrix[0, 0], matrix[1, 1], matrix[2, 2] = x, y, z
    return matrix


def _part_matrix(t: Any) -> np.ndarray:
    """Matriz local de un ``PartPose``: traslación, giros Z/Y/X y escala.

    ``ModelPart`` compone ``T · Rz · Ry · Rx · S``. Las traslaciones están en píxeles de
    modelo (16 por bloque) y las rotaciones en radianes; ``rot_y``/``rot_x`` de este
    proyecto ya llevan el signo de Minecraft.
    """
    values = [float(v) for v in (t or (0, 0, 0, 0, 0, 0, 1, 1, 1))]
    while len(values) < 9:
        values.append(1.0 if len(values) in (6, 7, 8) else 0.0)
    x, y, z, rx, ry, rz, sx, sy, sz = values[:9]
    return (
        _translate(x / 16.0, y / 16.0, z / 16.0)
        @ _m4(rot_z(math.degrees(rz)))
        @ _m4(rot_y(-math.degrees(ry)))
        @ _m4(rot_x(-math.degrees(rx)))
        @ _scale(sx, sy, sz)
    )


def _part_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


@dataclass(frozen=True, slots=True)
class _BakedQuad:
    positions: np.ndarray  # (4, 3), en bloques
    st: np.ndarray  # (4, 2)


def _bake_node(
    node: dict[str, Any],
    parent: np.ndarray,
    poses: dict[str, Any],
    skip: frozenset[str],
    out: list[_BakedQuad],
) -> None:
    name = str(node.get("n", ""))
    if _part_key(name) in skip:
        return
    override = poses.get(_part_key(name))
    matrix = parent @ _part_matrix(override if override is not None else node.get("t"))
    for cube in node.get("c") or ():
        for polygon in cube:
            positions = np.array([vertex[:3] for vertex in polygon], dtype=np.float64) / 16.0
            # Las cajas de grosor cero (los bordes de una maceta, por ejemplo) generan
            # polígonos degenerados que Minecraft dibuja como triángulos invisibles.
            if (
                np.linalg.norm(np.cross(positions[1] - positions[0], positions[2] - positions[0]))
                < 1e-9
            ):
                continue
            moved = (matrix[:3, :3] @ positions.T).T + matrix[:3, 3]
            st = np.array([vertex[3:5] for vertex in polygon], dtype=np.float32)
            out.append(_BakedQuad(moved.astype(np.float32), st))
    for child in node.get("ch") or ():
        _bake_node(child, matrix, poses, skip, out)


def _bake(
    layer: str,
    *,
    poses: dict[str, Any] | None = None,
    skip: frozenset[str] = frozenset(),
) -> tuple[_BakedQuad, ...]:
    data = _models().get(layer)
    if data is None:
        return ()
    quads: list[_BakedQuad] = []
    _bake_node(data, np.eye(4), poses or {}, skip, quads)
    return tuple(quads)


# --------------------------------------------------------------------------- #
# Utilidades NBT y de estado
# --------------------------------------------------------------------------- #


def _path(value: str) -> str:
    return str(value).split(":")[-1]


def _is_skull_path(path: str) -> bool:
    """Cierto para calaveras y cabezas colocadas; excluye ``piston_head``."""
    if path == "piston_head" or path.startswith("potted"):
        return False
    return "skull" in path or path.endswith("_head")


def is_skull_block(block: BlockState | None) -> bool:
    """Cierto si el bloque es una calavera o una cabeza colocada."""
    return block is not None and _is_skull_path(block.path)


def _is_banner_path(path: str) -> bool:
    """Cierto para estandartes de pie o de pared, no para macetas."""
    return "banner" in path and not path.startswith("potted")


def is_banner_block(block: BlockState | None) -> bool:
    """Cierto si el bloque es un estandarte colocado."""
    return block is not None and _is_banner_path(block.path)


def is_bell_block(block: BlockState | None) -> bool:
    """Cierto si el bloque es una campana colocada."""
    return block is not None and block.path == "bell"


def _entity_id(entity: dict[str, Any]) -> str:
    return _path(str(entity.get("id", "")))


def _prop(block: BlockState | None, name: str, default: str | None = None) -> str | None:
    if block is None:
        return default
    for key, value in block.properties:
        if key == name:
            return value
    return default


def _vec3(entity: dict[str, Any], key: str = "Pos") -> tuple[float, float, float] | None:
    value = entity.get(key)
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) < 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except TypeError, ValueError:
        return None


def _entity_yaw(entity: dict[str, Any]) -> float:
    value = entity.get("Rotation")
    if not isinstance(value, (list, tuple, np.ndarray)) or not value:
        return 0.0
    try:
        return float(value[0])
    except TypeError, ValueError:
        return 0.0


def _is_baby(entity: dict[str, Any]) -> bool:
    for key in ("IsBaby", "Baby"):
        if key in entity and bool(entity[key]):
            return True
    try:
        return int(entity.get("Age", 0)) < 0
    except TypeError, ValueError:
        return False


def _dye(value: Any, default: str = "white") -> str:
    if isinstance(value, (int, np.integer)) and 0 <= int(value) < len(_DYE_NAMES):
        return _DYE_NAMES[int(value)]
    text = _path(str(value or default)).lower()
    return text if text in _DYE_NAMES else default


# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EntityPlacement:
    """Geometría de una entidad o *block entity* colocada en el mundo."""

    position: tuple[float, float, float]
    state: _mesh.StateMesh
    #: El modelo del bloque no debe dibujarse: la entidad lo sustituye (cofres, carteles,
    #: cajas de shulker...). El modelo de bloque de estos estados es un cubo de respaldo.
    replace_block: bool = False


class EntityRenderer:
    """Convierte NBT de entidades en geometría colocada.

    ``state_templates`` devuelve las plantillas de un blockstate; se usa para el marco de
    objeto, que es un modelo de bloque normal.
    """

    def __init__(
        self,
        resolver: ModelResolver,
        atlas: TextureAtlas,
        state_templates: Callable[[BlockState], tuple[_mesh.QuadTemplate, ...]],
        *,
        mobs: bool = False,
    ) -> None:
        self.resolver = resolver
        self.atlas = atlas
        self.state_templates = state_templates
        self.mobs = mobs
        self.items = ItemRenderer(resolver, atlas)

    # -- construcción de plantillas --------------------------------------- #

    def _sprite(self, texture: str | None, tint: tuple[int, int, int] | None = None):
        if not texture:
            return None
        return self.atlas.add(f"minecraft:{texture}", tint)

    def _templates(
        self,
        layer: str,
        texture: str | None = None,
        *,
        tint: tuple[int, int, int] | None = None,
        poses: dict[str, Any] | None = None,
        skip: frozenset[str] = frozenset(),
        matrix: np.ndarray | None = None,
    ) -> tuple[_mesh.QuadTemplate, ...]:
        texture = texture or _textures().get(layer)
        quads = _bake(layer, poses=poses, skip=skip)
        sprite = self._sprite(texture, tint)
        alpha_mode = self.atlas.alpha_mode(sprite)
        templates: list[_mesh.QuadTemplate] = []
        for quad in quads:
            positions = quad.positions
            if matrix is not None:
                positions = ((matrix[:3, :3] @ positions.T).T + matrix[:3, 3]).astype(np.float32)
            templates.append(
                _mesh.QuadTemplate(
                    positions=positions,
                    normal=_mesh._winding_normal(positions),
                    st=quad.st,
                    sprite=sprite,
                    cullface=None,
                    alpha_mode=alpha_mode,
                    tintindex=None,
                )
            )
        return tuple(templates)

    def _transformed(
        self,
        templates: tuple[_mesh.QuadTemplate, ...],
        matrix: np.ndarray,
    ) -> tuple[_mesh.QuadTemplate, ...]:
        """Aplica una matriz a plantillas ya construidas."""
        result: list[_mesh.QuadTemplate] = []
        for template in templates:
            positions = ((matrix[:3, :3] @ template.positions.T).T + matrix[:3, 3]).astype(
                np.float32
            )
            result.append(
                _mesh.QuadTemplate(
                    positions=positions,
                    normal=_mesh._winding_normal(positions),
                    st=template.st,
                    sprite=template.sprite,
                    cullface=None,
                    alpha_mode=template.alpha_mode,
                    tintindex=template.tintindex,
                )
            )
        return tuple(result)

    def _state(self, block: BlockState, templates: tuple[_mesh.QuadTemplate, ...]):
        return _mesh.StateMesh(block=block, templates=templates, covers=False)

    # -- block entities ---------------------------------------------------- #

    def tile_entity(
        self,
        tile: dict[str, Any],
        block: BlockState | None,
        anchor: tuple[float, float, float],
    ) -> EntityPlacement | None:
        """Geometría de un *block entity*, anclada a su posición de bloque."""
        path = block.path if block is not None else _path(str(tile.get("id", "")))
        if path in _CHEST_MATERIALS:
            return self._chest(block, path, anchor)
        if _is_banner_path(path):
            return self._banner(tile, block, anchor)
        if path.endswith("shulker_box"):
            return self._shulker(block, anchor)
        if path == "decorated_pot":
            return self._decorated_pot(block, anchor)
        if path == "conduit":
            return self._conduit(anchor)
        if path == "bell":
            return self._bell(anchor)
        if _is_skull_path(path):
            return self._skull(block, anchor)
        return None

    def _chest(
        self,
        block: BlockState | None,
        path: str,
        anchor: tuple[float, float, float],
    ) -> EntityPlacement:
        material = _CHEST_MATERIALS.get(path, "normal")
        kind = _prop(block, "type", "single")
        facing = _prop(block, "facing", "north") or "north"
        if kind == "left":
            layer = "minecraft:double_chest_left#main"
            texture = f"entity/chest/{material}_left"
        elif kind == "right":
            layer = "minecraft:double_chest_right#main"
            texture = f"entity/chest/{material}_right"
        else:
            layer = "minecraft:chest#main"
            texture = f"entity/chest/{material}"

        # El modelo del cofre no ocupa la esquina del bloque: gira alrededor del centro
        # vertical del bloque, no alrededor de su centro geométrico.
        matrix = (
            _translate(0.5, 0.0, 0.5)
            @ _m4(rot_y(_Y_ROT.get(facing, 180.0)))
            @ _translate(-0.5, 0.0, -0.5)
        )
        templates = self._templates(layer, texture, matrix=matrix)
        return EntityPlacement(
            anchor, self._state(block or BlockState("minecraft:chest"), templates), True
        )

    def _banner(
        self,
        tile: dict[str, Any],
        block: BlockState | None,
        anchor: tuple[float, float, float],
    ) -> EntityPlacement:
        path = block.path if block is not None else "banner"
        # ``light_blue_banner`` no se puede cortar por el primer ``_``: hay colores con
        # guion bajo. Se quita el sufijo completo del bloque.
        stem = path.removesuffix("_wall_banner").removesuffix("_banner")
        color = _dye(stem, "white")
        tint = _DYE_COLORS[_DYE_NAMES.index(color)]
        wall = "_wall_" in path
        layer = "minecraft:wall_banner" if wall else "minecraft:standing_banner"
        if wall:
            facing = _prop(block, "facing", "north") or "north"
            angle = _Y_ROT.get(facing, 180.0)
        else:
            # El NBT puede faltar en el esquemático; el blockstate guarda la rotación.
            rotation = tile.get("Rotation")
            if rotation is None:
                rotation = _prop(block, "rotation", "0")
            try:
                angle = float(rotation or 0) * 22.5
            except TypeError, ValueError:
                angle = 0.0

        matrix = (
            _translate(0.5, 0.0, 0.5)
            @ _m4(rot_y(angle))
            @ _scale(2.0 / 3.0, -2.0 / 3.0, -2.0 / 3.0)
        )
        # De momento sólo se pinta el color base: los patrones del NBT se ignoran.
        # El palo usa la textura completa (madera incluida); la tela va aparte y teñida.
        main = self._templates(f"{layer}#main", "entity/banner/banner_base", matrix=matrix)
        flag = self._templates(f"{layer}#flag", "entity/banner/base", tint=tint, matrix=matrix)
        label = block or BlockState("minecraft:banner")
        return EntityPlacement(anchor, self._state(label, main + flag), True)

    def _shulker(
        self,
        block: BlockState | None,
        anchor: tuple[float, float, float],
    ) -> EntityPlacement:
        path = block.path if block is not None else "shulker_box"
        stem = path[: -len("_shulker_box")] if path.endswith("_shulker_box") else ""
        # Cada caja de shulker tiene su textura; la oficial blanca es ``shulker_white``
        # (``shulker`` a secas es la del shulker sin teñir).
        texture = f"entity/shulker/shulker_{_dye(stem)}" if stem else "entity/shulker/shulker"
        facing = _prop(block, "facing", "up") or "up"
        matrix = (
            _translate(0.5, 0.5, 0.5)
            @ _scale(0.9995, 0.9995, 0.9995)
            @ self._direction_rotation(facing)
            @ _scale(1.0, -1.0, -1.0)
            @ _translate(0.0, -1.0, 0.0)
        )
        templates = self._templates("minecraft:shulker_box#main", texture, matrix=matrix)
        label = block or BlockState("minecraft:shulker_box")
        return EntityPlacement(anchor, self._state(label, templates), True)

    def _decorated_pot(
        self,
        block: BlockState | None,
        anchor: tuple[float, float, float],
    ) -> EntityPlacement:
        facing = _prop(block, "facing", "north") or "north"
        angle = _Y_ROT.get(facing, 180.0) - 180.0
        matrix = _translate(0.5, 0.5, 0.5) @ _m4(rot_y(angle)) @ _translate(-0.5, -0.5, -0.5)
        base = self._templates(
            "minecraft:decorated_pot_base#main",
            "entity/decorated_pot/decorated_pot_base",
            matrix=matrix,
        )
        sides = self._templates(
            "minecraft:decorated_pot_sides#main",
            "entity/decorated_pot/decorated_pot_side",
            matrix=matrix,
        )
        label = block or BlockState("minecraft:decorated_pot")
        return EntityPlacement(anchor, self._state(label, base + sides), True)

    def _conduit(self, anchor: tuple[float, float, float]) -> EntityPlacement:
        matrix = _translate(0.5, 0.5, 0.5)
        templates: tuple[_mesh.QuadTemplate, ...] = ()
        for layer, texture in (
            ("minecraft:conduit#shell", "entity/conduit/base"),
            ("minecraft:conduit#cage", "entity/conduit/cage"),
            ("minecraft:conduit#eye", "entity/conduit/open_eye"),
        ):
            templates += self._templates(layer, texture, matrix=matrix)
        label = BlockState("minecraft:conduit")
        return EntityPlacement(anchor, self._state(label, templates), True)

    def _bell(self, anchor: tuple[float, float, float]) -> EntityPlacement:
        templates = self._templates("minecraft:bell#main", "entity/bell/bell_body")
        label = BlockState("minecraft:bell")
        return EntityPlacement(anchor, self._state(label, templates))

    def _skull(
        self,
        block: BlockState | None,
        anchor: tuple[float, float, float],
    ) -> EntityPlacement:
        path = block.path if block is not None else "skeleton_skull"
        wall = "_wall_" in path
        kind = path.replace("_wall_", "_").removesuffix("_skull").removesuffix("_head")
        layer_names = {
            "skeleton": "minecraft:skeleton_skull#main",
            "wither_skeleton": "minecraft:wither_skeleton_skull#main",
            "zombie": "minecraft:zombie_head#main",
            "player": "minecraft:player_head#main",
            "creeper": "minecraft:creeper_head#main",
            "dragon": "minecraft:dragon_skull#main",
            "piglin": "minecraft:piglin_head#main",
        }
        layer = layer_names.get(kind, "minecraft:skeleton_skull#main")
        if wall:
            facing = _prop(block, "facing", "north") or "north"
            step_x = 0
            step_z = 0
            if facing == "north":
                step_z = -1
            elif facing == "south":
                step_z = 1
            elif facing == "east":
                step_x = 1
            elif facing == "west":
                step_x = -1
            opposite = {"north": "south", "south": "north", "east": "west", "west": "east"}.get(
                facing, "north"
            )
            matrix = (
                _translate(0.5 - 0.25 * step_x, 0.25, 0.5 - 0.25 * step_z)
                @ _m4(rot_y(_Y_ROT.get(opposite, 180.0)))
                @ _scale(-1.0, -1.0, 1.0)
            )
        else:
            try:
                angle = float(_prop(block, "rotation", "0") or 0) * 22.5
            except ValueError:
                angle = 0.0
            matrix = _translate(0.5, 0.0, 0.5) @ _m4(rot_y(angle)) @ _scale(-1.0, -1.0, 1.0)
        templates = self._templates(layer, matrix=matrix)
        label = block or BlockState("minecraft:skeleton_skull")
        return EntityPlacement(anchor, self._state(label, templates), True)

    @staticmethod
    def _direction_rotation(direction: str) -> np.ndarray:
        """Rotación de ``Direction.getRotation()`` para cajas de shulker.

        En vanilla es un cuarto de vuelta que lleva la cara ``up`` del modelo hacia la
        dirección del bloque. Se expresa con las matrices del proyecto para no depender de
        cuaterniones.
        """
        # ``getRotation`` lleva +Y del modelo a la dirección; el modelo se dibuja después
        # con ``S(1,-1,-1)``, por lo que basta girar Y y luego X/Z.
        if direction == "up":
            return np.eye(4)
        if direction == "down":
            return _m4(rot_z(180.0))
        if direction == "north":
            return _m4(rot_x(90.0))
        if direction == "south":
            return _m4(rot_x(-90.0))
        if direction == "east":
            return _m4(rot_z(-90.0))
        return _m4(rot_z(90.0))

    # -- entidades --------------------------------------------------------- #

    def entity(self, entity: dict[str, Any]) -> list[EntityPlacement]:
        """Geometría de una entidad colocada en el mundo (vacío si no se reconoce)."""
        kind = _entity_id(entity)
        if not kind:
            return []
        if kind == "cushion":
            # ``mesh.MeshBuilder`` dibuja los cojines con su modelo y su color; aquí se
            # ignoran para no duplicarlos.
            return []
        if kind in ("item_frame", "glow_item_frame"):
            # Un marco invisible oculta el marco, pero no el objeto que muestra (es el
            # truco habitual para dejar objetos flotando).
            placement = self._item_frame(entity, kind == "glow_item_frame")
            return [placement] if placement is not None else []
        if entity.get("Invisible"):
            return []
        if kind == "painting":
            placement = self._painting(entity)
            return [placement] if placement is not None else []
        if kind == "armor_stand":
            return self._armor_stand(entity)
        if kind == "item":
            placement = self._dropped_item(entity)
            return [placement] if placement is not None else []
        if kind in ("arrow", "spectral_arrow", "wind_charge", "trident"):
            placement = self._projectile(entity, kind)
            return [placement] if placement is not None else []
        base = _ENTITY_LAYERS.get(kind, kind)
        if base.startswith(("boat/", "chest_boat/")):
            return self._boat(entity, base)
        if kind == "minecart" or kind.endswith("_minecart"):
            return self._minecart(entity, kind)
        # Todo lo que llega aquí es una criatura: botes, vagonetas, marcos, cuadros,
        # soportes de armadura, objetos y proyectiles ya se han resuelto antes. Las
        # criaturas están desactivadas por defecto para que el esquemático muestre sólo
        # la construcción salvo que se pidan explícitamente.
        if not self.mobs:
            return []
        return self._living(entity, kind)

    def _boat(self, entity: dict[str, Any], base: str) -> list[EntityPlacement]:
        """Bote o balsa con ``AbstractBoatRenderer``."""
        pos = _vec3(entity)
        if pos is None:
            return []
        yaw = _entity_yaw(entity)
        # T(0, 0.375, 0) · Ry(180 − yRot) · S(−1, −1, 1) · Ry(90).  En las matrices del
        # proyecto ``rot_y`` lleva el signo contrario al de ``Axis.YP`` de JOML.
        matrix = (
            _translate(0.0, 0.375, 0.0)
            @ _m4(rot_y(yaw - 180.0))
            @ _scale(-1.0, -1.0, 1.0)
            @ _m4(rot_y(-90.0))
        )
        main = f"minecraft:{base}#main"
        texture = _textures().get(main)
        templates = self._templates(main, texture, matrix=matrix)
        # La lámina de agua usa la textura del bote, no una propia.
        if not entity.get("isUnderWater"):
            templates += self._templates("minecraft:boat#water_patch", texture, matrix=matrix)
        if not templates:
            return []
        label = BlockState(f"minecraft:{_path(str(entity.get('id', 'boat')))}")
        return [EntityPlacement(pos, self._state(label, templates), False)]

    def _minecart(self, entity: dict[str, Any], kind: str) -> list[EntityPlacement]:
        """Vagoneta con la transformación de ``AbstractMinecartRenderer``."""
        pos = _vec3(entity)
        if pos is None:
            return []
        rotation = entity.get("Rotation")
        yaw = _entity_yaw(entity)
        pitch = 0.0
        if isinstance(rotation, (list, tuple, np.ndarray)) and len(rotation) > 1:
            pitch = float(rotation[1])
        # Ry(yRot) · Rz(−xRot) · T(0, 0.375, 0); ``rot_y`` invierte el signo de Y
        # respecto a JOML, ``rot_z`` lo conserva. El contenido se dibuja antes del
        # ``S(−1, −1, 1)`` que voltea el modelo de la vagoneta.
        pose = _m4(rot_y(-yaw)) @ _m4(rot_z(-pitch)) @ _translate(0.0, 0.375, 0.0)
        layer = f"minecraft:{kind}#main"
        templates = self._templates(
            layer,
            _textures().get(layer),
            matrix=pose @ _scale(-1.0, -1.0, 1.0),
        )

        content = _MINECART_CONTENTS.get(kind)
        if content is not None:
            offset = _MINECART_OFFSETS.get(kind, 6)
            # S(0.75) · Ry(90) · T(−0.5, (offset − 8)/16, −0.5).  El giro va antes de
            # centrar porque las plantillas de bloque van de 0 a 1, no centradas.
            content_matrix = (
                pose
                @ _scale(0.75, 0.75, 0.75)
                @ _m4(rot_y(-90.0))
                @ _translate(-0.5, (offset - 8) / 16.0, -0.5)
            )
            if content == "chest":
                # El modelo de bloque del cofre está vacío: lo dibuja ChestRenderer.
                templates += self._templates(
                    "minecraft:chest#main",
                    "entity/chest/normal",
                    matrix=content_matrix,
                )
            else:
                block = {
                    "hopper": BlockState(
                        "minecraft:hopper", (("enabled", "true"), ("facing", "down"))
                    ),
                    "furnace": BlockState(
                        "minecraft:furnace", (("facing", "north"), ("lit", "false"))
                    ),
                    "tnt": BlockState("minecraft:tnt", (("unstable", "false"),)),
                    "command_block": BlockState(
                        "minecraft:command_block", (("conditional", "false"), ("facing", "up"))
                    ),
                    "spawner": BlockState("minecraft:spawner"),
                }[content]
                templates += self._transformed(self.state_templates(block), content_matrix)
        if not templates:
            return []
        label = BlockState(f"minecraft:{kind}")
        return [EntityPlacement(pos, self._state(label, templates), False)]

    def _item_frame(
        self,
        entity: dict[str, Any],
        glow: bool,
    ) -> EntityPlacement | None:
        pos = _vec3(entity)
        if pos is None:
            return None
        try:
            facing_id = int(entity.get("Facing", 2))
        except TypeError, ValueError:
            facing_id = 2
        direction = _FACING_LEGACY[facing_id] if 0 <= facing_id < 6 else "north"
        step = {
            "down": (0, -1, 0),
            "up": (0, 1, 0),
            "north": (0, 0, -1),
            "south": (0, 0, 1),
            "west": (-1, 0, 0),
            "east": (1, 0, 0),
        }[direction]
        anchor = (
            pos[0] + step[0] * 0.46875,
            pos[1] + step[1] * 0.46875,
            pos[2] + step[2] * 0.46875,
        )
        if direction in ("down", "up"):
            rotation = _m4(rot_x(90.0 * step[1])) @ _m4(rot_y(180.0))
        else:
            rotation = _m4(rot_y(_Y_ROT.get(direction, 180.0) - 180.0))

        item = entity.get("Item")
        item_id = ""
        if isinstance(item, dict):
            item_id = str(item.get("id", ""))
        map_item = _path(item_id) in ("filled_map", "map")
        state = BlockState(
            "minecraft:glow_item_frame" if glow else "minecraft:item_frame",
            (("map", "true" if map_item else "false"),),
        )
        frame = self.state_templates(state)
        # El renderer traslada (-0.5, -0.5, -0.5) antes de dibujar el modelo del bloque.
        # La geometría queda centrada en el origen y se coloca con ``anchor`` al emitirla.
        frame_matrix = rotation @ _translate(-0.5, -0.5, -0.5)
        templates: list[_mesh.QuadTemplate] = []
        if not entity.get("Invisible"):
            for template in frame:
                positions = (
                    (frame_matrix[:3, :3] @ template.positions.T).T + frame_matrix[:3, 3]
                ).astype(np.float32)
                templates.append(
                    _mesh.QuadTemplate(
                        positions=positions,
                        normal=_mesh._winding_normal(positions),
                        st=template.st,
                        sprite=template.sprite,
                        cullface=None,
                        alpha_mode=template.alpha_mode,
                        tintindex=template.tintindex,
                    )
                )

        if item_id:
            item_model = self.items.render(item_id)
            if item_model is not None:
                turns = self._int(entity.get("ItemRotation"), 0)
                z = 0.5 if bool(entity.get("Invisible")) else 0.4375
                item_matrix = (
                    rotation
                    @ _translate(0.0, 0.0, z)
                    @ _m4(rot_z(turns * 45.0))
                    @ _scale(0.5, 0.5, 0.5)
                )
                for template in item_model.templates:
                    positions = (
                        (item_matrix[:3, :3] @ template.positions.T).T + item_matrix[:3, 3]
                    ).astype(np.float32)
                    templates.append(
                        _mesh.QuadTemplate(
                            positions=positions,
                            normal=_mesh._winding_normal(positions),
                            st=template.st,
                            sprite=template.sprite,
                            cullface=None,
                            alpha_mode=template.alpha_mode,
                            tintindex=template.tintindex,
                        )
                    )
        label = BlockState("minecraft:glow_item_frame" if glow else "minecraft:item_frame")
        return EntityPlacement(anchor, self._state(label, tuple(templates)), False)

    def _painting(self, entity: dict[str, Any]) -> EntityPlacement | None:
        pos = _vec3(entity)
        if pos is None:
            return None
        variant = _path(str(entity.get("variant", "kebab")))
        data = self.resolver.resources.read_json(f"data/minecraft/painting_variant/{variant}.json")
        if data is None:
            data = {"asset_id": "minecraft:kebab", "width": 1, "height": 1}
        asset = _path(str(data.get("asset_id", "kebab")))
        width = max(1, int(data.get("width", 1)))
        height = max(1, int(data.get("height", 1)))
        try:
            facing = int(entity.get("facing", 0))
        except TypeError, ValueError:
            facing = 0
        rotation = _m4(rot_y(facing * 90.0 - 180.0))
        half_w = width / 2.0
        half_h = height / 2.0
        depth = 1.0 / 32.0

        front_sprite = self._sprite(f"painting/{asset}")
        back_sprite = self._sprite("painting/back")
        st = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)
        front = np.array(
            [
                [-half_w, half_h, -depth],
                [half_w, half_h, -depth],
                [half_w, -half_h, -depth],
                [-half_w, -half_h, -depth],
            ],
            dtype=np.float32,
        )
        back = np.array(
            [
                [-half_w, -half_h, depth],
                [half_w, -half_h, depth],
                [half_w, half_h, depth],
                [-half_w, half_h, depth],
            ],
            dtype=np.float32,
        )
        templates: list[_mesh.QuadTemplate] = []
        for positions, sprite in ((front, front_sprite), (back, back_sprite)):
            moved = ((rotation[:3, :3] @ positions.T).T + rotation[:3, 3]).astype(np.float32)
            templates.append(
                _mesh.QuadTemplate(
                    positions=moved,
                    normal=_mesh._winding_normal(moved),
                    st=st,
                    sprite=sprite,
                    cullface=None,
                    alpha_mode=self.atlas.alpha_mode(sprite),
                    tintindex=None,
                )
            )
        # Cantos: cuatro tiras finas con la textura del reverso. Sin ellas un cuadro se ve
        # como una hoja sin grosor desde los lados.
        edge = 1.0 / 32.0
        edges = (
            np.array(
                [
                    [-half_w, -half_h, -depth],
                    [half_w, -half_h, -depth],
                    [half_w, -half_h, depth],
                    [-half_w, -half_h, depth],
                ]
            ),
            np.array(
                [
                    [half_w, half_h, -depth],
                    [-half_w, half_h, -depth],
                    [-half_w, half_h, depth],
                    [half_w, half_h, depth],
                ]
            ),
            np.array(
                [
                    [-half_w, half_h, -depth],
                    [-half_w, -half_h, -depth],
                    [-half_w, -half_h, depth],
                    [-half_w, half_h, depth],
                ]
            ),
            np.array(
                [
                    [half_w, -half_h, -depth],
                    [half_w, half_h, -depth],
                    [half_w, half_h, depth],
                    [half_w, -half_h, depth],
                ]
            ),
        )
        for positions in edges:
            moved = ((rotation[:3, :3] @ positions.T).T + rotation[:3, 3]).astype(np.float32)
            templates.append(
                _mesh.QuadTemplate(
                    positions=moved,
                    normal=_mesh._winding_normal(moved),
                    st=np.array(
                        [[0.0, 0.0], [1.0, 0.0], [1.0, edge], [0.0, edge]],
                        dtype=np.float32,
                    ),
                    sprite=back_sprite,
                    cullface=None,
                    alpha_mode=self.atlas.alpha_mode(back_sprite),
                    tintindex=None,
                )
            )
        label = BlockState("minecraft:painting")
        return EntityPlacement(pos, self._state(label, tuple(templates)), False)

    def _armor_stand(self, entity: dict[str, Any]) -> list[EntityPlacement]:
        pos = _vec3(entity)
        if pos is None:
            return []
        small = bool(entity.get("Small"))
        layer = "minecraft:armor_stand_small#main" if small else "minecraft:armor_stand#main"
        poses: dict[str, Any] = {}
        raw = entity.get("Pose")
        if isinstance(raw, dict):
            for name, value in raw.items():
                if isinstance(value, (list, tuple, np.ndarray)) and len(value) >= 6:
                    poses[_part_key(str(name))] = value
        skip: set[str] = set()
        if not entity.get("ShowArms"):
            skip |= {"rightarm", "leftarm"}
        if entity.get("NoBasePlate"):
            skip.add("baseplate")
        matrix = self._living_matrix(_entity_yaw(entity), scale=1.0)
        templates = self._templates(
            layer,
            "entity/armorstand/armorstand",
            poses=poses,
            skip=frozenset(skip),
            matrix=matrix,
        )
        label = BlockState("minecraft:armor_stand")
        return [EntityPlacement(pos, self._state(label, templates), False)]

    def _dropped_item(self, entity: dict[str, Any]) -> EntityPlacement | None:
        pos = _vec3(entity)
        if pos is None:
            return None
        item = entity.get("Item")
        if not isinstance(item, dict):
            return None
        item_id = str(item.get("id", ""))
        if not item_id:
            return None
        model = self.items.render(item_id)
        if model is None:
            return None
        # Un objeto caído se dibuja pequeño y girando; sin ``Age`` se usa un ángulo fijo.
        matrix = _translate(0.0, 0.25, 0.0) @ _m4(rot_y(45.0)) @ _scale(0.5, 0.5, 0.5)
        templates: list[_mesh.QuadTemplate] = []
        for template in model.templates:
            positions = ((matrix[:3, :3] @ template.positions.T).T + matrix[:3, 3]).astype(
                np.float32
            )
            templates.append(
                _mesh.QuadTemplate(
                    positions=positions,
                    normal=_mesh._winding_normal(positions),
                    st=template.st,
                    sprite=template.sprite,
                    cullface=None,
                    alpha_mode=template.alpha_mode,
                    tintindex=template.tintindex,
                )
            )
        label = BlockState(f"minecraft:{_path(item_id)}")
        return EntityPlacement(pos, self._state(label, tuple(templates)), False)

    def _projectile(self, entity: dict[str, Any], kind: str) -> EntityPlacement | None:
        pos = _vec3(entity)
        if pos is None:
            return None
        layer = {
            "arrow": "minecraft:arrow#main",
            "spectral_arrow": "minecraft:arrow#main",
            "wind_charge": "minecraft:wind_charge#main",
            "trident": "minecraft:trident#main",
        }[kind]
        texture = {
            "spectral_arrow": "entity/projectiles/arrow_spectral",
            "wind_charge": "entity/projectiles/wind_charge",
            "trident": "entity/trident/trident",
        }.get(kind)
        matrix = _translate(0.0, 0.5, 0.0)
        templates = self._templates(layer, texture, matrix=matrix)
        label = BlockState(f"minecraft:{kind}")
        return EntityPlacement(pos, self._state(label, templates), False)

    # -- criaturas --------------------------------------------------------- #

    @staticmethod
    def _living_matrix(yaw: float, scale: float = 1.0) -> np.ndarray:
        """Matriz de ``LivingEntityRenderer`` sin la traslación de la entidad.

        Vanilla hace ``Ry(180 - bodyRot)``, invierte X e Y, aplica la escala del renderer y
        baja el modelo ``1.501`` bloques para que los pies queden en ``y = 0``.
        """
        return (
            _m4(rot_y(yaw - 180.0)) @ _scale(-scale, -scale, scale) @ _translate(0.0, -1.501, 0.0)
        )

    def _living(self, entity: dict[str, Any], kind: str) -> list[EntityPlacement]:
        pos = _vec3(entity)
        if pos is None:
            return []
        base = _ENTITY_LAYERS.get(kind, kind)
        baby = _is_baby(entity)
        if kind == "tropical_fish":
            base = self._tropical_fish_layer(entity)
        if kind == "pufferfish":
            base = {
                "small": "pufferfish_small",
                "medium": "pufferfish_medium",
                "large": "pufferfish_big",
            }.get(str(entity.get("PuffState", 0)), "pufferfish_medium")
        layers = self._living_layers(base, baby)
        if not baby:
            layers = [layer for layer in layers if "_baby#main" not in layer]
        if not layers:
            return []

        # Sólo el renderer del avatar aplica 0.9375; las criaturas ya traen su escala
        # en la raíz del modelo (villager, witch, husk...) o se dibujan a 1.
        scale = 0.9375 if base in _LAYER_HUMANOID else 1.0
        matrix = self._living_matrix(_entity_yaw(entity), scale)
        label = BlockState(f"minecraft:{kind}")
        templates: list[_mesh.QuadTemplate] = []
        for layer in layers:
            texture, tint = self._living_texture(layer, entity, kind)
            templates += self._templates(
                layer,
                texture,
                tint=tint,
                matrix=matrix,
            )
        if not templates:
            return []
        return [EntityPlacement(pos, self._state(label, tuple(templates)), False)]

    def _living_layers(self, base: str, baby: bool) -> list[str]:
        """Capas que forman una criatura: la principal y sus superposiciones visibles."""
        suffix = "_baby" if baby else ""
        main = f"minecraft:{base}{suffix}#main"
        if main not in _models():
            main = f"minecraft:{base}#main"
        if main not in _models():
            return []
        layers = [main]
        variants = _models().keys()
        always = ("outer", "eyes", "armor", "wool", "tendrils", "heart", "bioluminescent")
        for layer in sorted(variants):
            stem, _, part = layer.partition("#")
            stem_name = stem.split(":", 1)[-1]
            expected = base + suffix
            if stem_name not in (expected, base):
                continue
            if part in always and layer != main:
                layers.append(layer)
        return layers

    def _living_texture(
        self,
        layer: str,
        entity: dict[str, Any],
        kind: str,
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        """Textura (y tinte) de una capa según la variante NBT de la criatura."""
        texture = _textures().get(layer)
        stem, _, part = layer.partition("#")
        baby = "_baby" in stem
        suffix = "_baby" if baby else ""

        if kind == "horse" and part == "main":
            color = self._horse_color(entity)
            texture = f"entity/horse/horse_{color}{suffix}"
        elif kind == "llama" and part == "main":
            color = _LLAMA_VARIANTS[self._int(entity.get("Variant"), 0) % len(_LLAMA_VARIANTS)]
            texture = f"entity/llama/llama_{color}{suffix}"
        elif kind == "trader_llama" and part == "main":
            texture = "entity/llama/llama_creamy" + suffix
        elif kind == "wolf" and part == "main":
            variant = _path(str(entity.get("variant", "pale")))
            if variant not in _WOLF_VARIANTS:
                variant = "pale"
            texture = f"entity/wolf/wolf_{variant}{suffix}"
            if entity.get("Tame"):
                texture = f"entity/wolf/wolf_{variant}_tame{suffix}"
        elif kind == "cat" and part == "main":
            variant = _path(str(entity.get("variant", "tabby")))
            if variant not in _CAT_VARIANTS:
                variant = "tabby"
            texture = f"entity/cat/cat_{variant}{suffix}"
        elif kind == "parrot" and part == "main":
            variant = _PARROT_VARIANTS[self._int(entity.get("Variant"), 0) % len(_PARROT_VARIANTS)]
            texture = f"entity/parrot/parrot_{variant}"
        elif kind == "rabbit" and part == "main":
            variant = _RABBIT_VARIANTS[
                self._int(entity.get("RabbitType", 0), 0) % len(_RABBIT_VARIANTS)
            ]
            texture = f"entity/rabbit/rabbit_{variant}{suffix}"
        elif kind == "axolotl" and part == "main":
            variant = _AXOLOTL_VARIANTS[
                self._int(entity.get("Variant"), 0) % len(_AXOLOTL_VARIANTS)
            ]
            texture = f"entity/axolotl/axolotl_{variant}{suffix}"
        elif kind == "fox" and part == "main":
            if _path(str(entity.get("Type", "red"))) == "snow":
                texture = f"entity/fox/fox_snow{suffix}"
        elif kind in ("cow", "mooshroom", "pig", "chicken") and part == "main":
            variant = _path(str(entity.get("variant", "")))
            if variant in ("cold", "warm"):
                stem_name = {"cow": "cow", "pig": "pig", "chicken": "chicken", "mooshroom": "cow"}[
                    kind
                ]
                if kind == "mooshroom":
                    texture = f"entity/cow/mooshroom_{variant}{suffix}"
                else:
                    texture = f"entity/{stem_name}/{stem_name}_{variant}{suffix}"
        elif kind == "panda" and part == "main":
            gene = _path(str(entity.get("MainGene", "normal")))
            if gene in ("aggressive", "brown", "lazy", "playful", "weak", "worried"):
                texture = f"entity/panda/panda_{gene}{suffix}"
        elif kind == "shulker" and part == "main":
            color = _dye(entity.get("Color", 16), "")
            texture = f"entity/shulker/shulker_{color}" if color else "entity/shulker/shulker"
        elif kind == "tropical_fish":
            pattern = self._int(entity.get("Variant"), 0)
            base_name = "tropical_a" if pattern & 1 == 0 else "tropical_b"
            if part == "pattern":
                texture = f"entity/fish/{base_name}_pattern_{(pattern // 2) % 6 + 1}"
            else:
                texture = f"entity/fish/{base_name}"

        tint = None
        color_name = self._sheep_color(entity) if kind == "sheep" and part == "wool" else None
        if color_name is not None:
            tint = _DYE_COLORS[_DYE_NAMES.index(color_name)]
        return texture, tint

    @staticmethod
    def _int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except TypeError, ValueError:
            return default

    @staticmethod
    def _horse_color(entity: dict[str, Any]) -> str:
        variant = EntityRenderer._int(entity.get("Variant"), 0)
        return _HORSE_COLORS[variant & 7 if variant & 7 < len(_HORSE_COLORS) else 0]

    @staticmethod
    def _sheep_color(entity: dict[str, Any]) -> str | None:
        value = entity.get("Color")
        if value is None:
            return None
        return _dye(value, "white")

    @staticmethod
    def _tropical_fish_layer(entity: dict[str, Any]) -> str:
        variant = EntityRenderer._int(entity.get("Variant"), 0)
        return "tropical_fish_small" if variant & 0x10000 else "tropical_fish_large"
