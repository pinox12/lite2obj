"""Genera el icono de la app (PNG e ICO) sin depender de herramientas externas.

El resultado se versiona en ``packaging/lite2obj.png`` y ``packaging/lite2obj.ico``.
Se ejecuta a mano cuando cambie el diseño:

    uv run python packaging/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
BACKGROUND = (24, 28, 38, 255)
TOP = (111, 181, 255, 255)
LEFT = (54, 112, 202, 255)
RIGHT = (30, 74, 148, 255)
EDGE = (170, 214, 255, 255)


def _cube(draw: ImageDraw.ImageDraw) -> None:
    """Cubo isométrico centrado, con las tres caras visibles."""
    center_x = SIZE / 2
    top_y = 52
    half_w = 82
    half_h = 47
    height = 78

    top = [
        (center_x, top_y),
        (center_x + half_w, top_y + half_h),
        (center_x, top_y + half_h * 2),
        (center_x - half_w, top_y + half_h),
    ]
    left = [
        (center_x - half_w, top_y + half_h),
        (center_x, top_y + half_h * 2),
        (center_x, top_y + half_h * 2 + height),
        (center_x - half_w, top_y + half_h + height),
    ]
    right = [
        (center_x, top_y + half_h * 2),
        (center_x + half_w, top_y + half_h),
        (center_x + half_w, top_y + half_h + height),
        (center_x, top_y + half_h * 2 + height),
    ]

    draw.polygon(top, fill=TOP)
    draw.polygon(left, fill=LEFT)
    draw.polygon(right, fill=RIGHT)
    for face in (top, left, right):
        draw.line([*face, face[0]], fill=EDGE, width=4, joint="curve")


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=48, fill=BACKGROUND)
    _cube(draw)

    directory = Path(__file__).resolve().parent
    png = directory / "lite2obj.png"
    ico = directory / "lite2obj.ico"
    image.save(png, format="PNG", optimize=True)
    image.save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"Generado {png}")
    print(f"Generado {ico}")


if __name__ == "__main__":
    main()
