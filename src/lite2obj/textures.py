"""Atlas de texturas.

Reúne todas las texturas que necesita un esquemático en una sola imagen, con dos objetivos:

* **OBJ/MTL**: un único material con ``map_Kd atlas.png`` (Blender importa un solo material).
* **Visor web**: una sola textura, lo que permite dibujar todo el modelo en muy pocas
  llamadas de dibujo.

Tintes
------
Minecraft no guarda la textura teñida, sino que multiplica la textura por un color según
``tintindex`` (césped, hojas, agua...). Como un esquemático de Litematica no tiene datos de
bioma, se usan los colores por defecto del bioma "plains" y se generan **sprites teñidos**
dentro del atlas. Así el color viaja en la propia textura y el OBJ no necesita nada extra.

Las texturas animadas (tiras verticales) se recortan al primer fotograma.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:  # pragma: no cover
    from .assets import ResourceManager

__all__ = [
    "DEFAULT_TINTS",
    "Sprite",
    "TextureAtlas",
    "TintResolver",
    "next_power_of_two",
]


#: Colores de tinte por defecto (bioma "plains", los mismos que usa el juego sin bioma).
DEFAULT_TINTS: dict[str, tuple[int, int, int]] = {
    "grass": (0x91, 0xBD, 0x59),
    "foliage": (0x59, 0xAE, 0x30),
    "water": (0x3F, 0x76, 0xE4),
    "dry_foliage": (0xA6, 0x6B, 0x37),
}

#: Hojas cuyo color el juego fija (no dependen del bioma).
FIXED_BLOCK_TINTS: dict[str, tuple[int, int, int]] = {
    "minecraft:spruce_leaves": (0x61, 0x99, 0x61),
    "minecraft:birch_leaves": (0x80, 0xA7, 0x55),
}

#: Bloques en los que ``tintindex == 0`` es follaje en vez de césped.
_FOLIAGE_BLOCKS = frozenset(
    {
        "vine",
        "lily_pad",
        "mangrove_roots",
        "hanging_roots",
        "weeping_vines",
        "twisting_vines",
        "cave_vines",
        "glow_lichen",
        "nether_sprouts",
        "azalea",
        "flowering_azalea",
    }
)

_WATER_HINTS = ("water",)


def _is_foliage(path: str) -> bool:
    return path.endswith("_leaves") or path in _FOLIAGE_BLOCKS


def next_power_of_two(value: int) -> int:
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


@dataclass(slots=True)
class Sprite:
    """Una textura colocada en el atlas."""

    key: tuple[str | None, str | None]
    x: int
    y: int
    width: int
    height: int
    texture: str | None
    index: int = -1

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """``(x, y, ancho, alto)`` en píxeles del atlas."""
        return self.x, self.y, self.width, self.height

    @property
    def box(self) -> tuple[int, int, int, int]:
        """``(izquierda, arriba, derecha, abajo)``, listo para ``PIL.Image.crop``."""
        return self.x, self.y, self.x + self.width, self.y + self.height

    def uv(self, normalized: float = 1.0) -> tuple[float, float, float, float]:
        """Rectángulo UV normalizado ``(u0, v0, u1, v1)`` con v hacia abajo."""
        return (
            self.x / normalized,
            self.y / normalized,
            (self.x + self.width) / normalized,
            (self.y + self.height) / normalized,
        )


@dataclass(slots=True)
class TintResolver:
    """Decide qué color de tinte aplicar a una cara con ``tintindex``."""

    enabled: bool = True
    tints: dict[str, tuple[int, int, int]] = field(default_factory=lambda: dict(DEFAULT_TINTS))
    fixed: dict[str, tuple[int, int, int]] = field(default_factory=lambda: dict(FIXED_BLOCK_TINTS))

    def tint_for(self, block_name: str, tintindex: int | None) -> tuple[int, int, int] | None:
        if not self.enabled or tintindex is None:
            return None
        path = block_name.split(":", 1)[-1]
        fixed = self.fixed.get(block_name)
        if fixed is not None:
            return fixed
        if tintindex == 2:
            return self.tints.get("water")
        if tintindex == 1:
            return self.tints.get("foliage")
        # tintindex 0: césped o follaje según el bloque.
        if any(hint in path for hint in _WATER_HINTS):
            return self.tints.get("water")
        if _is_foliage(path):
            return self.tints.get("foliage")
        return self.tints.get("grass")


class TextureAtlas:
    """Empaca texturas (y sus versiones teñidas) en una sola imagen.

    El atlas se construye de forma incremental: se pide un sprite por
    ``(textura, tinte)`` y al final se llama a :meth:`build`.
    """

    def __init__(self, resources: ResourceManager, *, padding: int = 2, scale: int = 1) -> None:
        self.resources = resources
        self.padding = max(int(padding), 0)
        self.scale = max(int(scale), 1)
        self._sprites: dict[tuple[str | None, str | None], Sprite | None] = {}
        self._images: list[tuple[Sprite, Image.Image]] = []
        self._opaque: dict[int, bool] = {}
        self._alpha_mode: dict[int, int] = {}
        self._image: Image.Image | None = None
        self._size = 0
        self.missing: set[str] = set()

    # -- construcción ------------------------------------------------------ #

    @staticmethod
    def _tint_key(tint: tuple[int, int, int] | None) -> str | None:
        return None if tint is None else "#{:02x}{:02x}{:02x}".format(*tint)

    def add(self, texture: str | None, tint: tuple[int, int, int] | None = None) -> Sprite | None:
        """Añade una textura (opcionalmente teñida) y devuelve su sprite."""
        key = (texture, self._tint_key(tint))
        if key in self._sprites:
            return self._sprites[key]
        sprite: Sprite | None = None
        if texture:
            image = self.resources.read_texture(texture)
            if image is None:
                self.missing.add(texture)
            else:
                image = self._prepare(image)
                if tint is not None:
                    image = _apply_tint(image, tint)
                sprite = Sprite(
                    key=key,
                    x=0,
                    y=0,
                    width=image.width,
                    height=image.height,
                    texture=texture,
                    index=len(self._images),
                )
                self._images.append((sprite, image))
                alpha_min, alpha_max = image.getchannel("A").getextrema()
                self._opaque[sprite.index] = alpha_min == 255
                if alpha_min == 255:
                    self._alpha_mode[sprite.index] = 0  # opaco
                elif alpha_min == 0 and _is_binary_alpha(image):
                    self._alpha_mode[sprite.index] = 1  # cutout (alfa 0 o 255)
                else:
                    self._alpha_mode[sprite.index] = 2  # translúcido
        self._sprites[key] = sprite
        return sprite

    def alpha_mode(self, sprite: Sprite | None) -> int:
        """0 = opaco, 1 = cutout (alfa 0/255), 2 = translúcido."""
        if sprite is None:
            return 2
        return self._alpha_mode.get(sprite.index, 2)

    def is_opaque(self, sprite: Sprite | None) -> bool:
        """Cierto si la textura no tiene ningún píxel transparente."""
        if sprite is None:
            return False
        return self._opaque.get(sprite.index, False)

    def _prepare(self, image: Image.Image) -> Image.Image:
        """Recorta el primer fotograma de las texturas animadas y escala si se pidió."""
        width, height = image.size
        if height > width and height % width == 0:
            image = image.crop((0, 0, width, width))
        if self.scale != 1:
            image = image.resize(
                (image.width * self.scale, image.height * self.scale), Image.Resampling.NEAREST
            )
        return image.convert("RGBA")

    def build(
        self, images: Iterable[tuple[str | None, tuple[int, int, int] | None]] = ()
    ) -> Image.Image:
        """Empaca las imágenes y devuelve el atlas. Es idempotente."""
        for texture, tint in images:
            self.add(texture, tint)
        if self._image is not None:
            return self._image
        if not self._images:
            self._image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            self._size = 1
            return self._image

        pad = self.padding
        # Se ordena para empacar mejor; el índice de cada sprite ya está asignado.
        self._images.sort(key=lambda item: (-item[0].height, -item[0].width, item[0].texture or ""))
        sizes = [(sprite.width + 2 * pad, sprite.height + 2 * pad) for sprite, _ in self._images]
        width, height, slots = _shelf_pack(sizes)

        atlas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        for (sprite, image), (slot_x, slot_y) in zip(self._images, slots, strict=True):
            sprite.x = slot_x + pad
            sprite.y = slot_y + pad
            atlas.paste(_pad_tile(image, pad), (slot_x, slot_y))
        self._image = atlas
        self._size = width
        return atlas

    # -- consulta ---------------------------------------------------------- #

    @property
    def image(self) -> Image.Image:
        return self.build()

    @property
    def size(self) -> tuple[int, int]:
        self.build()
        return self._image.size if self._image else (1, 1)

    @property
    def sprite_count(self) -> int:
        return len(self._images)

    def sprite(
        self, texture: str | None, tint: tuple[int, int, int] | None = None
    ) -> Sprite | None:
        return self._sprites.get((texture, self._tint_key(tint)))

    def to_png(self) -> bytes:
        buffer = BytesIO()
        self.image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def _apply_tint(image: Image.Image, tint: tuple[int, int, int]) -> Image.Image:
    """Multiplica los canales RGB por el color de tinte (como hace Minecraft)."""
    red, green, blue, alpha = image.split()
    red = red.point(lambda value: value * tint[0] // 255)
    green = green.point(lambda value: value * tint[1] // 255)
    blue = blue.point(lambda value: value * tint[2] // 255)
    return Image.merge("RGBA", (red, green, blue, alpha))


def _is_binary_alpha(image: Image.Image) -> bool:
    """Cierto si el canal alfa sólo toma valores 0 o 255 (material *cutout*)."""
    histogram = image.getchannel("A").histogram()
    return sum(histogram[1:255]) == 0


def _pad_tile(image: Image.Image, pad: int) -> Image.Image:
    """Crea una baldosa con el borde replicado, para evitar *bleeding* entre sprites."""
    if pad <= 0:
        return image
    width, height = image.size
    tile = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    tile.paste(image, (pad, pad))
    left = image.crop((0, 0, 1, height))
    right = image.crop((width - 1, 0, width, height))
    top = image.crop((0, 0, width, 1))
    bottom = image.crop((0, height - 1, width, height))
    tile.paste(top.resize((width, pad), Image.Resampling.NEAREST), (pad, 0))
    tile.paste(bottom.resize((width, pad), Image.Resampling.NEAREST), (pad, height + pad))
    tile.paste(left.resize((pad, height), Image.Resampling.NEAREST), (0, pad))
    tile.paste(right.resize((pad, height), Image.Resampling.NEAREST), (width + pad, pad))
    corner = image.crop((0, 0, 1, 1)).resize((pad, pad), Image.Resampling.NEAREST)
    tile.paste(corner, (0, 0))
    tile.paste(
        image.crop((width - 1, 0, width, 1)).resize((pad, pad), Image.Resampling.NEAREST),
        (width + pad, 0),
    )
    tile.paste(
        image.crop((0, height - 1, 1, height)).resize((pad, pad), Image.Resampling.NEAREST),
        (0, height + pad),
    )
    tile.paste(
        image.crop((width - 1, height - 1, width, height)).resize(
            (pad, pad), Image.Resampling.NEAREST
        ),
        (width + pad, height + pad),
    )
    return tile


def _shelf_pack(
    sizes: list[tuple[int, int]], max_side: int = 8192
) -> tuple[int, int, list[tuple[int, int]]]:
    """Empaca rectángulos con un algoritmo de estanterías, devolviendo el tamaño del atlas.

    Devuelve ``(ancho, alto, posiciones)`` donde cada posición es la esquina superior
    izquierda de la baldosa (incluyendo el relleno).
    """
    if not sizes:
        return 1, 1, []
    area = sum(w * h for w, h in sizes)
    widest = max(w for w, _ in sizes)
    width = next_power_of_two(max(widest, int(math.sqrt(area) * 1.15)))

    while width <= max_side:
        positions: list[tuple[int, int]] = []
        x = y = row_height = 0
        fits = True
        for width_i, height_i in sizes:
            if x + width_i > width:
                y += row_height
                x = 0
                row_height = 0
            if x + width_i > width or y + height_i > max_side:
                fits = False
                break
            positions.append((x, y))
            x += width_i
            row_height = max(row_height, height_i)
        if fits:
            total_height = y + row_height
            return width, next_power_of_two(total_height), positions
        width *= 2
    raise ValueError("Las texturas no caben en un atlas de 8192x8192")
