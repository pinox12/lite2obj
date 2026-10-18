"""Tests del atlas de texturas (F3)."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lite2obj.assets import MemorySource, ResourceManager, texture_path
from lite2obj.textures import (
    DEFAULT_TINTS,
    TextureAtlas,
    TintResolver,
    _is_binary_alpha,
    next_power_of_two,
)


def make_png(width: int, height: int, color=(255, 0, 0, 255), *, alpha_variation=False) -> bytes:
    image = Image.new("RGBA", (width, height), color)
    if alpha_variation:
        for x in range(width):
            image.putpixel((x, 0), (255, 0, 0, 128))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_atlas(entries: dict[str, bytes], **kwargs) -> TextureAtlas:
    manager = ResourceManager([MemorySource(entries)])
    return TextureAtlas(manager, **kwargs)


# --------------------------------------------------------------------------- #


def test_next_power_of_two():
    assert next_power_of_two(0) == 1
    assert next_power_of_two(1) == 1
    assert next_power_of_two(3) == 4
    assert next_power_of_two(16) == 16
    assert next_power_of_two(17) == 32


def test_single_sprite_is_packed_at_origin_with_padding():
    atlas = make_atlas({texture_path("test:block/a"): make_png(16, 16)}, padding=2)
    sprite = atlas.add("test:block/a")
    assert sprite is not None
    atlas.build()
    assert sprite.x == 2 and sprite.y == 2
    assert sprite.width == 16 and sprite.height == 16
    # El atlas siempre es potencia de dos.
    width, height = atlas.size
    assert width == height == next_power_of_two(width)
    assert atlas.sprite_count == 1


def test_sprites_do_not_overlap():
    entries = {texture_path(f"test:block/t{i}"): make_png(16, 16) for i in range(6)}
    atlas = make_atlas(entries, padding=1)
    sprites = [atlas.add(f"test:block/t{i}") for i in range(6)]
    atlas.build()
    rects = [sprite.rect for sprite in sprites if sprite]
    assert len(rects) == 6
    for i, first in enumerate(rects):
        for second in rects[i + 1 :]:
            assert not _overlaps(first, second), f"{first} se solapa con {second}"


def _overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_normalized_uv_is_within_unit_square():
    atlas = make_atlas({texture_path("test:block/a"): make_png(16, 16)})
    atlas.add("test:block/a")
    atlas.build()
    width, height = atlas.size
    sprite = atlas.sprite("test:block/a")
    assert sprite is not None
    assert (width, height) == atlas.size
    u0, v0, u1, v1 = sprite.uv(width)
    assert 0.0 <= u0 < u1 <= 1.0
    assert 0.0 <= v0 < v1 <= 1.0


def test_animated_texture_is_cropped_to_first_frame():
    atlas = make_atlas({texture_path("test:block/anim"): make_png(16, 64)})
    sprite = atlas.add("test:block/anim")
    assert sprite is not None
    assert sprite.width == 16 and sprite.height == 16


def test_tinted_and_untinted_are_separate_sprites():
    atlas = make_atlas({texture_path("test:block/grass"): make_png(16, 16, (255, 255, 255, 255))})
    plain = atlas.add("test:block/grass")
    tinted = atlas.add("test:block/grass", (0x91, 0xBD, 0x59))
    assert plain is not tinted
    atlas.build()
    assert atlas.sprite_count == 2
    assert atlas.sprite("test:block/grass", (0x91, 0xBD, 0x59)) is tinted
    # El sprite teñido multiplica el color original por el tinte.
    assert atlas.image.crop(tinted.box).getpixel((0, 0))[:3] == (0x91, 0xBD, 0x59)
    assert atlas.image.crop(plain.box).getpixel((0, 0))[:3] == (255, 255, 255)


def test_missing_texture_returns_none_and_is_recorded():
    atlas = make_atlas({})
    assert atlas.add("test:block/nope") is None
    atlas.add("test:block/nope2")
    atlas.build()
    assert atlas.missing == {"test:block/nope", "test:block/nope2"}
    assert atlas.sprite_count == 0
    assert atlas.size == (1, 1)


def test_scale_option_upscales_sprites():
    atlas = make_atlas({texture_path("test:block/a"): make_png(16, 16)}, scale=2, padding=0)
    sprite = atlas.add("test:block/a")
    assert sprite is not None
    assert (sprite.width, sprite.height) == (32, 32)


def test_alpha_mode_classification():
    atlas = make_atlas(
        {
            texture_path("test:block/opaque"): make_png(16, 16, (255, 0, 0, 255)),
            texture_path("test:block/translucent"): make_png(16, 16, (255, 0, 0, 128)),
        }
    )
    opaque = atlas.add("test:block/opaque")
    translucent = atlas.add("test:block/translucent")
    assert atlas.alpha_mode(opaque) == 0
    assert atlas.alpha_mode(translucent) == 2
    assert atlas.is_opaque(opaque)
    assert not atlas.is_opaque(translucent)


def test_cutout_alpha_detection():
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    image.putpixel((0, 0), (0, 0, 0, 0))
    assert _is_binary_alpha(image)
    image.putpixel((1, 1), (0, 0, 0, 100))
    assert not _is_binary_alpha(image)


def test_png_is_really_png():
    atlas = make_atlas({texture_path("test:block/a"): make_png(16, 16)})
    atlas.add("test:block/a")
    data = atlas.to_png()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(data)) as image:
        assert image.size == atlas.size


# --------------------------------------------------------------------------- #
# Tintes
# --------------------------------------------------------------------------- #


def test_tint_resolver_default_colors():
    resolver = TintResolver()
    assert resolver.tint_for("minecraft:grass_block", 0) == DEFAULT_TINTS["grass"]
    assert resolver.tint_for("minecraft:oak_leaves", 0) == DEFAULT_TINTS["foliage"]
    assert resolver.tint_for("minecraft:water", 0) == DEFAULT_TINTS["water"]
    assert resolver.tint_for("minecraft:water", 2) == DEFAULT_TINTS["water"]
    # Sin tintindex no hay tinte.
    assert resolver.tint_for("minecraft:stone", None) is None


def test_tint_resolver_fixed_leaf_colors():
    resolver = TintResolver()
    assert resolver.tint_for("minecraft:spruce_leaves", 0) == (0x61, 0x99, 0x61)
    assert resolver.tint_for("minecraft:birch_leaves", 0) == (0x80, 0xA7, 0x55)


def test_tint_resolver_can_be_disabled():
    resolver = TintResolver(enabled=False)
    assert resolver.tint_for("minecraft:grass_block", 0) is None


def test_atlas_packing_of_many_sprites_stays_power_of_two():
    entries = {texture_path(f"test:block/s{i}"): make_png(16, 16) for i in range(100)}
    atlas = make_atlas(entries, padding=2)
    for i in range(100):
        atlas.add(f"test:block/s{i}")
    atlas.build()
    width, height = atlas.size
    assert width == next_power_of_two(width)
    assert height == next_power_of_two(height)
    assert atlas.sprite_count == 100


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
