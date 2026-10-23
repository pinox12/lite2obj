"""Modelos de objetos para marcos, cuadros y entidades que llevan un ``ItemStack``.

Minecraft 26.x ya no usa ``models/item/<id>.json`` como punto de entrada: cada objeto
declara su modelo en ``items/<id>.json`` con un árbol de tipos (``model``, ``select``,
``range_dispatch``, ``condition``, ``composite`` y ``special``). Este módulo resuelve ese
árbol a geometría reutilizando el mismo :class:`~lite2obj.models.ModelResolver` que los
bloques. Los objetos generados a mano (``builtin/generated``) se sintetizan como dos caras
planas, igual que hace el cliente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from . import mesh as _mesh
from .assets import items_model_path
from .models import FACE_DIRECTIONS, ModelResolver, ResolvedModel, rot_x, rot_y, rot_z
from .textures import TextureAtlas

#: Tipos de ``items/<id>.json`` que no aportan geometría propia (los dibuja un renderer
#: especial de entidad: cofres, calaveras, estandartes...).
_SPECIAL_TYPES = frozenset(
    {
        "minecraft:special",
        "minecraft:chest",
        "minecraft:shulker_box",
        "minecraft:banner",
        "minecraft:decorated_pot",
        "minecraft:player_head",
        "minecraft:skull",
        "minecraft:shield",
    }
)

#: Caras de un objeto generado. La frontal mira a ``+Z`` en el espacio del objeto (el marco
#: la gira después) y la trasera a ``-Z``.
_FRONT_Z = 8.5 / 16.0
_BACK_Z = 7.5 / 16.0


def _sprite(resolver: ModelResolver, model: ResolvedModel, key: str) -> str | None:
    """Textura de una capa concreta (``layer0``, ``layer1``...)."""
    return model.texture(model.textures.get(key))


@dataclass(slots=True)
class ItemModel:
    """Geometría de un objeto ya colocada en su espacio local (centrada en el origen)."""

    templates: tuple[_mesh.QuadTemplate, ...]
    model_id: str | None = None
    #: Transformación ``fixed`` del modelo (la del marco se aplica encima).
    display: dict[str, Any] | None = None


def _display_matrix(display: dict[str, Any] | None) -> np.ndarray:
    """Matriz de una entrada ``display`` (``fixed``) de modelo de objeto.

    Orden de vanilla (``ItemTransform``): trasladar, girar en XYZ, escalar y, por último,
    centrar el modelo (``translate(-0.5, -0.5, -0.5)``). La traslación de ``display`` va en
    dieciseisavos de bloque.
    """
    matrix = np.eye(4)
    if not display:
        return matrix
    rotation = display.get("rotation") or (0.0, 0.0, 0.0)
    translation = display.get("translation") or (0.0, 0.0, 0.0)
    scale = display.get("scale") or (1.0, 1.0, 1.0)
    rx, ry, rz = (float(v) for v in rotation)
    tx, ty, tz = (float(v) for v in translation)
    sx, sy, sz = (float(v) for v in scale)

    # Rz @ Ry @ Rx es el orden de ``Quaternionf.rotationXYZ`` de JOML; los signos de X e Y
    # se invierten al pasar a las matrices de Minecraft de este proyecto.
    rotate = rot_x(-rx) @ rot_y(-ry) @ rot_z(rz)
    step = np.eye(4)
    step[:3, :3] = rotate @ np.diag([sx, sy, sz])
    step[:3, 3] = (tx / 16.0, ty / 16.0, tz / 16.0)
    center = np.eye(4)
    center[:3, 3] = (-0.5, -0.5, -0.5)
    return step @ center


def _generated_item(
    resolver: ModelResolver,
    atlas: TextureAtlas,
    model: ResolvedModel,
) -> list[_mesh.QuadTemplate]:
    """Objeto plano de ``builtin/generated``: capas ``layer0..layerN`` en dos caras."""
    layers: list[str | None] = []
    index = 0
    while True:
        path = _sprite(resolver, model, f"layer{index}")
        if path is None:
            break
        layers.append(path)
        index += 1
    if not layers:
        return []

    sprite = None
    for layer in layers:
        sprite = atlas.combine(sprite, atlas.add(layer))
    alpha_mode = atlas.alpha_mode(sprite)
    # Orden CCW visto desde ``+Z`` para que la normal de la cara frontal apunte hacia
    # fuera. Invertirlo dejaba las dos caras del objeto mirando al mismo lado y el visor,
    # que descarta las traseras, no dibujaba el objeto del marco.
    st = np.array([[0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]], dtype=np.float32)

    front = np.array(
        [
            [0.0, 0.0, _FRONT_Z],
            [1.0, 0.0, _FRONT_Z],
            [1.0, 1.0, _FRONT_Z],
            [0.0, 1.0, _FRONT_Z],
        ],
        dtype=np.float32,
    )
    # La trasera se ve en espejo, como el reverso de un objeto plano.
    back = np.array(
        [
            [1.0, 0.0, _BACK_Z],
            [0.0, 0.0, _BACK_Z],
            [0.0, 1.0, _BACK_Z],
            [1.0, 1.0, _BACK_Z],
        ],
        dtype=np.float32,
    )
    return [
        _mesh.QuadTemplate(
            positions=front,
            normal=np.array([0.0, 0.0, 1.0], dtype=np.float32),
            st=st,
            sprite=sprite,
            cullface=None,
            alpha_mode=alpha_mode,
            tintindex=None,
        ),
        _mesh.QuadTemplate(
            positions=back,
            normal=np.array([0.0, 0.0, -1.0], dtype=np.float32),
            st=st,
            sprite=sprite,
            cullface=None,
            alpha_mode=alpha_mode,
            tintindex=None,
        ),
    ]


def _block_item(
    resolver: ModelResolver,
    atlas: TextureAtlas,
    model_id: str,
    model: ResolvedModel,
) -> list[_mesh.QuadTemplate]:
    """Geometría de un objeto que usa un modelo de bloque normal."""
    templates: list[_mesh.QuadTemplate] = []
    for element in model.elements:
        corners = element.corners() / _mesh._MODEL_UNITS
        for direction, face in element.faces.items():
            if direction not in FACE_DIRECTIONS:
                continue
            sprite = atlas.add(model.texture(face.texture))
            u0, v0, u1, v1 = face.uv_or_default(element.frm, element.to, direction)
            st = np.zeros((4, 2), dtype=np.float32)
            for slot, corner in enumerate(_mesh.FACE_UV_ORDER[direction]):
                s, t = _mesh.rotate_uv(*_mesh.UV_CORNERS[corner], face.rotation)
                st[slot] = ((u0 + s * (u1 - u0)) / 16.0, (v0 + t * (v1 - v0)) / 16.0)
            positions = corners[list(_mesh.FACE_CORNERS[direction])].astype(np.float32)
            normal = _mesh._winding_normal(positions)
            templates.append(
                _mesh.QuadTemplate(
                    positions=positions,
                    normal=normal,
                    st=st,
                    sprite=sprite,
                    cullface=None,
                    alpha_mode=atlas.alpha_mode(sprite),
                    tintindex=face.tintindex,
                )
            )
    return templates


class ItemRenderer:
    """Resuelve objetos a geometría, con caché por identificador."""

    def __init__(self, resolver: ModelResolver, atlas: TextureAtlas) -> None:
        self.resolver = resolver
        self.atlas = atlas
        self._cache: dict[str, ItemModel | None] = {}

    # -- resolución del árbol de modelos ---------------------------------- #

    def _pick_model(self, node: Any) -> str | None:
        if not isinstance(node, dict):
            return None
        kind = str(node.get("type", "minecraft:model"))
        if kind in _SPECIAL_TYPES:
            return None
        if kind == "minecraft:model":
            value = node.get("model")
            return str(value) if value else None
        if kind == "minecraft:composite":
            for child in node.get("models") or ():
                picked = self._pick_model(child)
                if picked:
                    return picked
            return None
        if kind == "minecraft:select":
            for case in node.get("cases") or ():
                picked = self._pick_model(case.get("model"))
                if picked:
                    return picked
            return self._pick_model(node.get("fallback"))
        if kind == "minecraft:condition":
            picked = self._pick_model(node.get("on_true"))
            return picked or self._pick_model(node.get("on_false"))
        if kind == "minecraft:range_dispatch":
            for entry in node.get("entries") or ():
                picked = self._pick_model(entry.get("model"))
                if picked:
                    return picked
            return self._pick_model(node.get("fallback"))
        return None

    def _model_for_item(self, item: str) -> str | None:
        data = self.resolver.resources.read_json(items_model_path(item))
        if data is not None and "model" in data:
            picked = self._pick_model(data["model"])
            if picked:
                return picked
        namespace, path = (item.split(":", 1) + [""])[:2] if ":" in item else ("minecraft", item)
        # Respaldo para packs antiguos que sólo traen ``models/item/<id>.json``.
        return f"{namespace}:item/{path}"

    # -- geometría --------------------------------------------------------- #

    def render(self, item: str, *, fixed: bool = True) -> ItemModel | None:
        """Geometría del objeto, ya transformada por su ``display.fixed``.

        ``fixed`` es el contexto que usa el marco de objeto; si es ``False`` se devuelve la
        geometría sin la transformación de display (útil para depuración).
        """
        key = item if fixed else f"{item}#raw"
        if key in self._cache:
            return self._cache[key]

        model_id = self._model_for_item(item)
        result: ItemModel | None = None
        if model_id:
            try:
                model = self.resolver.model(model_id)
            except Exception:
                model = None
            if model is not None:
                if model.elements:
                    templates = _block_item(self.resolver, self.atlas, model_id, model)
                else:
                    templates = _generated_item(self.resolver, self.atlas, model)
                if templates:
                    display = model.display.get("fixed") if fixed else None
                    matrix = _display_matrix(display)
                    positions = np.array([t.positions for t in templates], dtype=np.float32)
                    moved = (
                        (matrix[:3, :3] @ positions.reshape(-1, 3).T).T + matrix[:3, 3]
                    ).reshape(positions.shape)
                    transformed = []
                    for template, quad in zip(templates, moved, strict=True):
                        transformed.append(
                            _mesh.QuadTemplate(
                                positions=quad.astype(np.float32),
                                normal=_mesh._winding_normal(quad),
                                st=template.st,
                                sprite=template.sprite,
                                cullface=None,
                                alpha_mode=template.alpha_mode,
                                tintindex=template.tintindex,
                            )
                        )
                    result = ItemModel(tuple(transformed), model_id, display)
        self._cache[key] = result
        return result
