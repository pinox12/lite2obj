"""Resolución de blockstates y modelos de bloque.

Convierte un :class:`~lite2obj.litematic.BlockState` en la geometría del modelo real
(escaleras, losas, vallas, multipart, ...), aplicando las reglas de Minecraft:

* ``variants`` (coincidencia por propiedades, con fallback por subconjunto) y ``multipart``
  (condiciones ``when`` con ``OR``/``AND`` anidados).
* Cadena de ``parent`` con merge de ``textures`` (el hijo gana) y ``elements`` (el hijo
  reemplaza al padre).
* Resolución de referencias ``#alias`` de texturas a ids de textura reales.

Convenciones de rotación (verificadas contra modelos reales del jar 26.3):

* El orden es **primero X y luego Y**: ``M = Ry(y) @ Rx(x)``.
* Los ángulos de Minecraft giran en sentido **horario visto desde el eje positivo**,
  equivalente a la matriz estándar con el ángulo negado.
* La rotación del variant se aplica **alrededor del centro del bloque** ``(8, 8, 8)``
  en espacio de modelo (0..16).
* La ``rotation`` de un elemento gira alrededor de su ``origin`` con el ángulo indicado
  (regla de la mano derecha) y opcionalmente se reescala.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .aliases import normalize_block_name, rename_properties
from .assets import (
    ResourceManager,
    blockstate_path,
    model_path,
    parse_resource_id,
)
from .i18n import _
from .litematic import BlockState

__all__ = [
    "BlockModelError",
    "BlockStateDefinition",
    "Element",
    "ElementFace",
    "ElementRotation",
    "ModelResolver",
    "ResolvedState",
    "Variant",
    "rot_x",
    "rot_y",
    "rot_axis",
    "variant_matrix",
    "BLOCK_CENTER",
]

BLOCK_CENTER = np.full(3, 8.0)
FACE_DIRECTIONS = ("down", "up", "north", "south", "west", "east")


class BlockModelError(LookupError):
    """No se pudo resolver el blockstate o el modelo."""


# --------------------------------------------------------------------------- #
# Matemática de rotación
# --------------------------------------------------------------------------- #


def rot_x(degrees: float) -> np.ndarray:
    """Rotación ``x`` de Minecraft (horaria vista desde +X)."""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]])


def rot_y(degrees: float) -> np.ndarray:
    """Rotación ``y`` de Minecraft (horaria vista desde arriba)."""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


def rot_z(degrees: float) -> np.ndarray:
    """Rotación estándar (mano derecha) alrededor de Z."""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_axis(axis: str, degrees: float) -> np.ndarray:
    """Matriz de la ``rotation`` de un elemento (regla de la mano derecha).

    A diferencia de las rotaciones de variant, que Minecraft invierte para ``x`` e ``y``
    (ver :func:`rot_x` y :func:`rot_y`), la rotación de un elemento usa directamente la
    matriz de JOML (``BlockElementRotation``). Por eso aquí se niega el ángulo al reutilizar
    esas dos funciones.
    """
    if axis == "x":
        return rot_x(-degrees)
    if axis == "y":
        return rot_y(-degrees)
    if axis == "z":
        return rot_z(degrees)
    raise BlockModelError(_("Eje de rotación inválido: {axis!r}").format(axis=axis))


def variant_matrix(x: int, y: int) -> np.ndarray:
    """Matriz del variant aplicando primero X y luego Y."""
    if not x and not y:
        return np.eye(3)
    matrix = np.eye(3)
    if y:
        matrix = matrix @ rot_y(y)
    if x:
        matrix = matrix @ rot_x(x)
    return matrix


def apply_about(point: np.ndarray, matrix: np.ndarray, origin: np.ndarray) -> np.ndarray:
    """Aplica ``matrix`` a ``point`` alrededor de ``origin``."""
    return matrix @ (point - origin) + origin


# --------------------------------------------------------------------------- #
# Condiciones de multipart
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Condition:
    """Condición de un ``when`` de multipart."""

    properties: tuple[tuple[str, tuple[str, ...]], ...] = ()
    any_of: tuple[Condition, ...] = ()
    all_of: tuple[Condition, ...] = ()

    def matches(self, props: Mapping[str, str]) -> bool:
        for key, allowed in self.properties:
            value = props.get(key)
            if value is None or value not in allowed:
                return False
        if self.all_of and not all(c.matches(props) for c in self.all_of):
            return False
        return not (self.any_of and not any(c.matches(props) for c in self.any_of))


def _values(raw: Any) -> tuple[str, ...]:
    return tuple(str(raw).split("|"))


def parse_condition(raw: Mapping[str, Any] | None) -> Condition:
    if not raw:
        return Condition()
    props: list[tuple[str, tuple[str, ...]]] = []
    any_of: list[Condition] = []
    all_of: list[Condition] = []
    for key, value in raw.items():
        if key == "OR":
            any_of = [parse_condition(item) for item in value]
        elif key == "AND":
            all_of = [parse_condition(item) for item in value]
        else:
            props.append((key, _values(value)))
    return Condition(tuple(props), tuple(any_of), tuple(all_of))


# --------------------------------------------------------------------------- #
# Blockstate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Variant:
    """Un ``apply``/variant: modelo más rotación."""

    model: str
    x: int = 0
    y: int = 0
    uvlock: bool = False
    weight: int = 1


@dataclass(frozen=True, slots=True)
class MultipartPart:
    when: Condition
    variants: tuple[Variant, ...]


@dataclass(slots=True)
class BlockStateDefinition:
    """Contenido de ``blockstates/<bloque>.json``."""

    block: str
    variants: dict[str, tuple[Variant, ...]] | None = None
    multipart: tuple[MultipartPart, ...] | None = None

    def select_groups(self, props: Mapping[str, str]) -> tuple[tuple[Variant, ...], ...]:
        """Grupos de variants candidatos para estas propiedades.

        Cada grupo es una lista de variants entre los que hay que elegir **uno**: Minecraft lo
        sortea por posición, y ahí está el truco de los bloques de terreno (``stone``,
        ``dirt``...) que traen cuatro rotaciones para que el suelo no se vea repetitivo.
        Un blockstate de ``variants`` produce un grupo; un ``multipart`` produce uno por cada
        parte que encaje.
        """
        try:
            return self._select_groups(props)
        except BlockModelError:
            # Esquemáticos antiguos pueden traer propiedades que ya no existen:
            # se reintenta sólo con las propiedades que usa esta definición.
            used = self.used_properties()
            filtered = {k: v for k, v in props.items() if k in used}
            if filtered == dict(props):
                raise
            return self._select_groups(filtered)

    def _select_groups(self, props: Mapping[str, str]) -> tuple[tuple[Variant, ...], ...]:
        if self.variants is not None:
            best: tuple[Variant, ...] | None = None
            best_score = -1
            for key, variants in self.variants.items():
                score = _score_key(key, props)
                if score is not None and score > best_score:
                    best, best_score = variants, score
            if best is None:
                # Último recurso: coincidencia parcial, tratando las propiedades
                # ausentes como comodín (entradas malformadas o incompletas).
                for key, variants in self.variants.items():
                    score = _score_key(key, props, missing_ok=True)
                    if score is not None and score > best_score:
                        best, best_score = variants, score
            if best is None:
                raise BlockModelError(
                    _("{block}: ninguna variante coincide con {props}").format(
                        block=self.block, props=dict(props)
                    )
                )
            return (best,)

        parts = self.multipart or ()
        groups = tuple(part.variants for part in parts if part.when.matches(props))
        if not groups:
            raise BlockModelError(
                _("{block}: ningún multipart coincide con {props}").format(
                    block=self.block, props=dict(props)
                )
            )
        return groups

    def used_properties(self) -> set[str]:
        """Nombres de propiedades que referencia esta definición."""
        names: set[str] = set()
        for key in self.variants or {}:
            for pair in key.split(","):
                if "=" in pair:
                    names.add(pair.split("=", 1)[0].strip())
        for part in self.multipart or ():
            names |= _condition_properties(part.when)
        return names


def _condition_properties(condition: Condition) -> set[str]:
    names = {key for key, _ in condition.properties}
    for child in condition.any_of + condition.all_of:
        names |= _condition_properties(child)
    return names


def _score_key(key: str, props: Mapping[str, str], *, missing_ok: bool = False) -> int | None:
    """Puntuación de coincidencia de una clave de variant (o ``None`` si no encaja).

    Con ``missing_ok`` las propiedades ausentes se tratan como comodín.
    """
    if not key:
        return 0
    score = 0
    for pair in key.split(","):
        name, _, raw = pair.partition("=")
        name = name.strip()
        value = props.get(name)
        if value is None:
            if missing_ok:
                score += 1
                continue
            return None
        if value not in _values(raw):
            return None
        score += 1
    return score


def _weighted_pick(variants: Sequence[Variant], seed: int) -> Variant:
    if len(variants) == 1:
        return variants[0]
    total = sum(max(v.weight, 1) for v in variants)
    target = (seed % total) if total else 0
    for variant in variants:
        weight = max(variant.weight, 1)
        if target < weight:
            return variant
        target -= weight
    return variants[-1]


#: Constante de mezcla para dar un seed distinto a cada grupo de multipart.
_GOLDEN = 0x9E3779B1


def pick_variants(groups: Sequence[Sequence[Variant]], seed: int) -> tuple[Variant, ...]:
    """Elige un variant por grupo usando ``seed`` (uno por posición, como Minecraft).

    Los grupos se decorrelacionan avanzando el seed, de modo que dos partes de un
    ``multipart`` no eligen siempre a la vez.
    """
    if len(groups) == 1:
        return (_weighted_pick(groups[0], seed),)
    return tuple(
        _weighted_pick(group, seed + index * _GOLDEN) for index, group in enumerate(groups)
    )


def position_seed(position: tuple[int, int, int]) -> int:
    """Seed estable a partir de la posición del bloque.

    Minecraft usa su propio generador sembrado con la posición; no se replica bit a bit
    porque sólo hace falta que dos bloques vecinos escojan variantes distintas.
    """
    x, y, z = position
    value = (x * 0x9E3779B1) ^ (y * 0x85EBCA77) ^ (z * 0xC2B2AE3D)
    value &= 0xFFFFFFFF
    value ^= value >> 16
    value = (value * 0x7FEB352D) & 0xFFFFFFFF
    value ^= value >> 15
    value = (value * 0x846CA68B) & 0xFFFFFFFF
    return value ^ (value >> 16)


def parse_blockstate(block: str, data: Mapping[str, Any]) -> BlockStateDefinition:
    variants_raw = data.get("variants")
    multipart_raw = data.get("multipart")
    definition = BlockStateDefinition(block=block)
    if isinstance(variants_raw, Mapping):
        parsed_variants: dict[str, tuple[Variant, ...]] = {}
        for key, value in variants_raw.items():
            parsed_variants[str(key)] = tuple(_parse_apply(value))
        definition.variants = parsed_variants
    elif isinstance(multipart_raw, Sequence):
        parts: list[MultipartPart] = []
        for part in multipart_raw:
            when = parse_condition(part.get("when"))
            parts.append(MultipartPart(when=when, variants=tuple(_parse_apply(part.get("apply")))))
        definition.multipart = tuple(parts)
    else:
        raise BlockModelError(_("{block}: sin 'variants' ni 'multipart'").format(block=block))
    return definition


def _parse_apply(raw: Any) -> list[Variant]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    result: list[Variant] = []
    for item in items:
        model = item.get("model")
        if model is None:
            continue
        result.append(
            Variant(
                model=_normalize_model(str(model)),
                x=int(item.get("x", 0)),
                y=int(item.get("y", 0)),
                uvlock=bool(item.get("uvlock", False)),
                weight=int(item.get("weight", 1)),
            )
        )
    return result


def _normalize_model(value: str) -> str:
    """Normaliza ``block/x`` a ``minecraft:block/x`` y quita el sufijo ``.json``."""
    if value.endswith(".json"):
        value = value[: -len(".json")]
    namespace, path = parse_resource_id(value)
    return f"{namespace}:{path}"


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ElementRotation:
    origin: tuple[float, float, float]
    axis: str
    angle: float
    rescale: bool = False

    def matrix_for(self, frm: np.ndarray, to: np.ndarray) -> np.ndarray:
        """Matriz de rotación; si ``rescale``, escala para conservar el tamaño."""
        rotation = rot_axis(self.axis, self.angle)
        if not self.rescale:
            return rotation
        size = to - frm
        box = np.array(
            [
                [0, 0, 0],
                [size[0], 0, 0],
                [size[0], size[1], 0],
                [0, size[1], 0],
                [0, 0, size[2]],
                [size[0], 0, size[2]],
                [size[0], size[1], size[2]],
                [0, size[1], size[2]],
            ],
            dtype=float,
        )
        rotated = (rotation @ box.T).T
        rotated_size = rotated.max(axis=0) - rotated.min(axis=0)
        scale = np.where(rotated_size > 1e-9, size / rotated_size, 1.0)
        return np.diag(scale) @ rotation


@dataclass(frozen=True, slots=True)
class ElementFace:
    """Una cara de un elemento con su textura ya resuelta."""

    texture: str | None
    uv: tuple[float, float, float, float] | None = None
    cullface: str | None = None
    rotation: int = 0
    tintindex: int | None = None

    def uv_or_default(
        self, frm: Sequence[float], to: Sequence[float], direction: str
    ) -> tuple[float, float, float, float]:
        """UV explícita o derivada de la caja del elemento."""
        if self.uv is not None:
            return self.uv
        return default_uv(frm, to, direction)


def default_uv(
    frm: Sequence[float], to: Sequence[float], direction: str
) -> tuple[float, float, float, float]:
    """UV por defecto de una cara según el estándar de modelos de Minecraft."""
    x0, y0, z0 = (float(v) for v in frm)
    x1, y1, z1 = (float(v) for v in to)
    if direction == "down":
        return (x0, 16.0 - z1, x1, 16.0 - z0)
    if direction == "up":
        return (x0, z0, x1, z1)
    if direction == "north":
        return (16.0 - x1, 16.0 - y1, 16.0 - x0, 16.0 - y0)
    if direction == "south":
        return (x0, 16.0 - y1, x1, 16.0 - y0)
    if direction == "west":
        return (z0, 16.0 - y1, z1, 16.0 - y0)
    if direction == "east":
        return (16.0 - z1, 16.0 - y1, 16.0 - z0, 16.0 - y0)
    raise BlockModelError(
        _("Dirección de cara inválida: {direction!r}").format(direction=direction)
    )


@dataclass(frozen=True, slots=True)
class Element:
    """Un cuboide del modelo, en espacio de modelo (0..16)."""

    frm: tuple[float, float, float]
    to: tuple[float, float, float]
    faces: Mapping[str, ElementFace]
    rotation: ElementRotation | None = None
    shade: bool = True

    def box(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array(self.frm, dtype=float), np.array(self.to, dtype=float)

    def corners(self) -> np.ndarray:
        """Las 8 esquinas, con la rotación del elemento aplicada."""
        frm, to = self.box()
        base = np.array(
            [
                [frm[0], frm[1], frm[2]],
                [to[0], frm[1], frm[2]],
                [to[0], to[1], frm[2]],
                [frm[0], to[1], frm[2]],
                [frm[0], frm[1], to[2]],
                [to[0], frm[1], to[2]],
                [to[0], to[1], to[2]],
                [frm[0], to[1], to[2]],
            ]
        )
        if self.rotation is None:
            return base
        origin = np.array(self.rotation.origin, dtype=float)
        matrix = self.rotation.matrix_for(frm, to)
        return (matrix @ (base - origin).T).T + origin


@dataclass(slots=True)
class ResolvedModel:
    """Modelo con la cadena de ``parent`` ya aplanada."""

    id: str
    elements: tuple[Element, ...]
    textures: dict[str, Any]
    ambient_occlusion: bool = True
    gui_light: str = "side"
    display: dict[str, Any] = field(default_factory=dict)
    parent: str | None = None

    def texture(self, ref: str | None) -> str | None:
        """Resuelve una referencia ``#alias`` o un id de textura directo.

        Soporta el formato nuevo en el que el valor es un objeto
        (``{"sprite": "minecraft:block/glass"}``).
        """
        seen: set[str] = set()
        value: Any = ref
        while value is not None:
            if isinstance(value, Mapping):
                value = value.get("sprite")
                continue
            if not isinstance(value, str):
                return None
            if value.startswith("#"):
                key = value[1:]
                if key in seen:
                    return None
                seen.add(key)
                value = self.textures.get(key)
                continue
            if not value:
                return None
            namespace, path = parse_resource_id(value)
            return f"{namespace}:{path}"
        return None

    def particle_texture(self) -> str | None:
        """Textura ``particle`` del modelo, usada como respaldo para cubos."""
        return self.texture("#particle")


@dataclass(frozen=True, slots=True)
class ResolvedState:
    """Resultado de resolver un estado de bloque: sus grupos de variants candidatos."""

    block: BlockState
    groups: tuple[tuple[Variant, ...], ...]
    model_name: str = ""
    seed: int = 0

    @property
    def variants(self) -> tuple[Variant, ...]:
        """Variants elegidos con el seed estable del estado (sin posición)."""
        return pick_variants(self.groups, self.seed)

    @property
    def is_empty(self) -> bool:
        return not self.groups

    @property
    def used_alias(self) -> bool:
        return bool(self.model_name) and self.model_name != self.block.name


class ModelResolver:
    """Resuelve blockstates y modelos usando un :class:`ResourceManager`."""

    def __init__(self, resources: ResourceManager, fallback_mode: str = "cube") -> None:
        from .fallbacks import FallbackResolver

        self.resources = resources
        self.fallbacks = FallbackResolver(mode=fallback_mode)
        self._blockstates: dict[str, BlockStateDefinition] = {}
        self._raw_models: dict[str, ResolvedModel] = {}
        self._states: dict[tuple[str, str], ResolvedState] = {}

    # -- blockstates ------------------------------------------------------- #

    def blockstate(self, block: str) -> BlockStateDefinition:
        namespace, path = parse_resource_id(block)
        key = f"{namespace}:{path}"
        cached = self._blockstates.get(key)
        if cached is not None:
            return cached
        data = self.resources.read_json(blockstate_path(key))
        if data is None:
            raise BlockModelError(_("No hay blockstate para {block!r}").format(block=key))
        definition = parse_blockstate(key, data)
        self._blockstates[key] = definition
        return definition

    def resolve(self, block: BlockState) -> ResolvedState:
        """Resuelve un estado de bloque a sus grupos de variants (cacheado por estado).

        La elección concreta entre los variants de un grupo no se hace aquí: depende de la
        posición (ver :func:`pick_variants`), así que se guarda el seed estable del estado
        para quien no tenga posición.
        """
        target = normalize_block_name(block.name)
        props = rename_properties(block.name, block.properties_dict())
        variant_key = f"{target}[{','.join(f'{k}={v}' for k, v in sorted(props.items()))}]"
        # La caché distingue el nombre original: dos bloques que son alias entre sí
        # comparten geometría, pero ``ResolvedState.block`` debe seguir siendo el que se
        # pidió para que ``used_alias`` y el culling con nombres antiguos sean correctos.
        key = (block.name, variant_key)
        cached = self._states.get(key)
        if cached is not None:
            return cached

        definition = self.blockstate(target)
        resolved = ResolvedState(
            block=block,
            groups=definition.select_groups(props),
            model_name=target,
            seed=zlib.crc32(variant_key.encode("utf-8")) & 0x7FFFFFFF,
        )
        self._states[key] = resolved
        return resolved

    # -- modelos ----------------------------------------------------------- #

    def model(self, model_id: str) -> ResolvedModel:
        key = _normalize_model(model_id)
        cached = self._raw_models.get(key)
        if cached is not None:
            return cached
        resolved = self._load_model(key)
        self._raw_models[key] = resolved
        return resolved

    def _load_model(self, model_id: str) -> ResolvedModel:
        data = self.resources.read_json(model_path(model_id))
        if data is None:
            raise BlockModelError(_("No se encontró el modelo {model!r}").format(model=model_id))

        parent_id = data.get("parent")
        # ``builtin/generated`` y ``builtin/entity`` no son ficheros: son modelos que el
        # cliente construye en código (objetos planos y entidades). Se tratan como si no
        # tuvieran padre para poder reutilizar la cadena de texturas y ``display``.
        parent = (
            self.model(str(parent_id))
            if parent_id and not str(parent_id).startswith("builtin/")
            else None
        )

        textures: dict[str, Any] = dict(parent.textures) if parent else {}
        for name, value in (data.get("textures") or {}).items():
            textures[str(name)] = value

        elements = self._parse_elements(data.get("elements"))
        if elements is None and parent is not None:
            elements = parent.elements
        if elements is None:
            elements = ()

        return ResolvedModel(
            id=model_id,
            elements=elements,
            textures=textures,
            ambient_occlusion=bool(
                data.get("ambientocclusion", parent.ambient_occlusion if parent else True)
            ),
            gui_light=str(data.get("gui_light", parent.gui_light if parent else "side")),
            display={**(parent.display if parent else {}), **(data.get("display") or {})},
            parent=parent.id if parent else None,
        )

    def _parse_elements(self, raw: Any) -> tuple[Element, ...] | None:
        if raw is None:
            return None
        elements: list[Element] = []
        for item in raw:
            frm = item.get("from")
            to = item.get("to")
            if frm is None or to is None:
                continue
            rotation = None
            if item.get("rotation"):
                rot = item["rotation"]
                rotation = ElementRotation(
                    origin=tuple(float(v) for v in rot.get("origin", (0, 0, 0))),  # type: ignore[arg-type]
                    axis=str(rot.get("axis", "y")),
                    angle=float(rot.get("angle", 0.0)),
                    rescale=bool(rot.get("rescale", False)),
                )
            faces: dict[str, ElementFace] = {}
            for direction, face in (item.get("faces") or {}).items():
                if direction not in FACE_DIRECTIONS:
                    continue
                uv_raw = face.get("uv")
                faces[direction] = ElementFace(
                    texture=str(face.get("texture", "")),
                    uv=tuple(float(v) for v in uv_raw) if uv_raw else None,  # type: ignore[arg-type]
                    cullface=face.get("cullface"),
                    rotation=int(face.get("rotation", 0)),
                    tintindex=int(face["tintindex"]) if "tintindex" in face else None,
                )
            elements.append(
                Element(
                    frm=tuple(float(v) for v in frm),  # type: ignore[arg-type]
                    to=tuple(float(v) for v in to),  # type: ignore[arg-type]
                    faces=faces,
                    rotation=rotation,
                    shade=bool(item.get("shade", True)),
                )
            )
        return tuple(elements)

    # -- conveniencia ------------------------------------------------------ #

    def geometry(
        self,
        block: BlockState,
        position: tuple[int, int, int] | None = None,
        *,
        allow_fallback: bool = True,
        fluid_above: bool = False,
    ) -> list[tuple[Element, Variant]]:
        """Elementos a dibujar con su variant (para aplicar rotaciones después).

        Con ``position`` la elección entre variants candidatos se hace con el seed de esa
        posición, igual que Minecraft. Si el bloque no tiene geometría propia (fluidos,
        bloques de entidad) y ``allow_fallback`` es ``True``, se genera geometría sustituta.
        """
        try:
            state = self.resolve(block)
        except BlockModelError:
            # Bloques sin blockstate (los fluidos son el caso típico).
            fallback = (
                self.fallbacks.elements_for(
                    block.path,
                    block.properties_dict(),
                    None,
                    fluid_above=fluid_above,
                )
                if allow_fallback
                else ()
            )
            if not fallback:
                raise
            return [(element, Variant(model=block.name)) for element in fallback]

        seed = position_seed(position) if position is not None else state.seed
        variants = pick_variants(state.groups, seed)
        return self.elements_for(
            state, variants, allow_fallback=allow_fallback, fluid_above=fluid_above
        )

    def elements_for(
        self,
        state: ResolvedState,
        variants: Sequence[Variant],
        *,
        allow_fallback: bool = True,
        fluid_above: bool = False,
    ) -> list[tuple[Element, Variant]]:
        """Elementos de un estado ya resuelto, con los variants concretos ya elegidos."""
        result: list[tuple[Element, Variant]] = []
        for variant in variants:
            model = self.model(variant.model)
            result.extend((element, variant) for element in model.elements)
        if result or not allow_fallback:
            return result

        particle = None
        for variant in variants:
            particle = self.model(variant.model).particle_texture()
            if particle:
                break
        fallback = self.fallbacks.elements_for(
            state.block.path,
            state.block.properties_dict(),
            particle,
            fluid_above=fluid_above,
        )
        if not fallback:
            return []
        synthetic = Variant(model=variants[0].model if variants else state.model_name)
        return [(element, synthetic) for element in fallback]

    def supports(self, block: BlockState, position: tuple[int, int, int] | None = None) -> bool:
        """Indica si el bloque tiene geometría (propia o de respaldo)."""
        try:
            return bool(self.geometry(block, position))
        except BlockModelError:
            return False
