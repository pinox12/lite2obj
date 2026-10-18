"""Interfaz de línea de comandos de lite2obj.

Uso::

    lite2obj info <archivo.litematic> [--blocks N]
    lite2obj list

Los comandos de exportación (``export``) y de servidor (``view``) se añadirán en las
fases siguientes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, litematic, paths

__all__ = ["main"]


def _human(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def cmd_info(args: argparse.Namespace) -> int:
    doc = litematic.load(args.path)
    size = doc.enclosing_size

    print(f"{doc.name or args.path.stem}")
    print(f"  archivo      {args.path}")
    if doc.author:
        print(f"  autor        {doc.author}")
    if doc.description:
        print(f"  descripción  {doc.description}")
    print(f"  tamaño       {size[0]} x {size[1]} x {size[2]}")
    print(f"  regiones     {len(doc.regions)}")
    print(f"  bloques      {_human(doc.block_count)}")
    print(f"  volumen      {_human(size[0] * size[1] * size[2])}")
    print(f"  data version {doc.data_version} (formato v{doc.version}.{doc.sub_version})")

    for region in doc.regions:
        print(f"\n  región '{region.name}'")
        print(f"    origen    {region.origin}")
        print(f"    tamaño    {region.size[0]} x {region.size[1]} x {region.size[2]}")
        print(f"    paleta    {len(region.palette)} estados")
        print(f"    entidades {len(region.tile_entities)} tile entities")

    counts = doc.block_counts()
    top = list(counts.items())[: args.blocks]
    print(f"\n  {len(counts)} tipos de bloque distintos; los más frecuentes:")
    width = max((len(name) for name, _ in top), default=0)
    total = doc.block_count or 1
    for name, count in top:
        pct = count / total * 100
        print(f"    {name:<{width}}  {_human(count):>10}  {pct:5.1f}%")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    found = paths.find_schematics()
    if not found:
        print("No se encontraron archivos .litematic.")
        print(f"Directorios buscados: {[str(p) for p in paths.schematic_dirs()]}")
        return 1
    for path in found:
        print(path)
    print(f"\n{len(found)} archivos")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lite2obj", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"lite2obj {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="muestra metadatos y recuento de bloques")
    info.add_argument("path", type=Path, help="archivo .litematic")
    info.add_argument(
        "--blocks", type=int, default=15, help="cuántos tipos de bloque listar (0 = todos)"
    )
    info.set_defaults(func=cmd_info)

    listing = sub.add_parser("list", help="lista los .litematic encontrados en la máquina")
    listing.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    blocks = getattr(args, "blocks", 15)
    if blocks <= 0:
        args.blocks = 10**9
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
