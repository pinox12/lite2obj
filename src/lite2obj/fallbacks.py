"""Geometría de respaldo para bloques sin modelo de bloque.

Algunos bloques no tienen geometría en ``models/block`` porque Minecraft los dibuja con
otro sistema:

* **Fluidos** (``water``, ``lava``): los dibuja el renderer de fluidos.
* **Bloques de entidad** (cofres, banderas, macetas decoradas, camas, carteles, shulkers,
  campanas...): su modelo sólo declara una textura ``particle`` y el resto lo dibuja un
  ``BlockEntityRenderer``.

Para no dejar huecos en el render, esta fase genera geometría sustituta:

``cube`` (por defecto)
    Un cubo completo con la textura ``particle`` del modelo (o la primera textura
    disponible). Un cofre queda como un cubo de madera, una bandera como un cubo, etc.
    Es una aproximación honesta: se ve el bloque y su textura, sin detalle.
``none``
    Se omiten; se reportan como no soportados.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import FACE_DIRECTIONS, Element, ElementFace

__all__ = ["FallbackResolver", "fluid_cube", "fluid_height", "full_cube"]

#: Textura de cada fluido.
FLUID_TEXTURES: dict[str, str] = {
    "water": "minecraft:block/water_still",
    "lava": "minecraft:block/lava_still",
}

#: Bloques que se consideran fluidos aunque el nombre lleve prefijo.
_FLUID_SUFFIXES = ("_water", "_lava")


def full_cube(
    texture: str,
    *,
    cullface: bool = True,
    tintindex: int | None = None,
    y_from: float = 0.0,
    y_to: float = 16.0,
) -> Element:
    """Cubo completo con la misma textura en las 6 caras."""
    faces: dict[str, ElementFace] = {}
    for direction in FACE_DIRECTIONS:
        faces[direction] = ElementFace(
            texture=texture,
            uv=(0.0, 0.0, 16.0, 16.0),
            cullface=direction if cullface else None,
            tintindex=tintindex,
        )
    return Element(frm=(0.0, y_from, 0.0), to=(16.0, y_to, 16.0), faces=faces)


def fluid_height(level: int, *, fluid_above: bool = False) -> float:
    """Altura de la superficie de un fluido, en unidades de modelo (0..16).

    Minecraft no llena el bloque: un bloque fuente (``level=0``) llega a ``8/9`` y cada
    nivel de flujo baja otro noveno. Si encima hay el mismo fluido, la columna es continua
    y ocupa el bloque entero; sin esto, las paredes de una columna de agua dejarían una
    rendija de ``1/9`` entre bloque y bloque.
    """
    if fluid_above:
        return 16.0
    if level <= 0:
        return 16.0 * 8.0 / 9.0
    if level < 8:
        return 16.0 * (8 - level) / 9.0
    # ``level`` 8..15 es agua cayendo: la superficie la marca el bloque de encima.
    return 16.0 * 8.0 / 9.0


def fluid_cube(fluid: str, level: int = 0, *, fluid_above: bool = False) -> Element:
    """Cubo de fluido con la superficie a la altura de Minecraft.

    El agua se tiñe (su textura es gris); la lava ya trae color y no lleva ``tintindex``.
    Las caras llevan ``cullface`` para que el descarte normal las oculte contra bloques
    opacos, y el builder añade la regla de fluido contra fluido.
    """
    texture = FLUID_TEXTURES.get(fluid, f"minecraft:block/{fluid}_still")
    tintindex = 0 if fluid == "water" else None
    return full_cube(
        texture,
        cullface=True,
        tintindex=tintindex,
        y_to=fluid_height(level, fluid_above=fluid_above),
    )


@dataclass(slots=True)
class FallbackResolver:
    """Genera geometría sustituta para bloques sin elementos."""

    mode: str = "cube"

    #: Texturas de fluido conocidas.
    fluids: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.fluids is None:
            self.fluids = dict(FLUID_TEXTURES)

    def is_fluid(self, path: str) -> str | None:
        """Devuelve el nombre del fluido si el bloque lo es."""
        if path.startswith("flowing_"):
            # Identificadores antiguos (``flowing_water``) que aún aparecen en esquemáticos.
            path = path[len("flowing_") :]
        if path in self.fluids:
            return path
        for suffix in _FLUID_SUFFIXES:
            if path.endswith(suffix) and path[: -len(suffix)] in self.fluids:
                return path[: -len(suffix)]
        return None

    def elements_for(
        self,
        block_path: str,
        properties: dict[str, str],
        particle_texture: str | None,
        *,
        fluid_above: bool = False,
    ) -> tuple[Element, ...]:
        """Geometría de respaldo para un bloque sin elementos del modelo."""
        fluid = self.is_fluid(block_path)
        if fluid is not None:
            level = int(properties.get("level", "0"))
            return (fluid_cube(fluid, level, fluid_above=fluid_above),)
        if self.mode == "none" or not particle_texture:
            return ()
        return (full_cube(particle_texture),)
