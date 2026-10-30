"""Interfaz de línea de comandos de lite2obj.

Uso::

    lite2obj info <archivo.litematic> [--blocks N]
    lite2obj list
    lite2obj assets build [--jar JAR] [--output ARCHIVO] [--force]
    lite2obj assets info [ARCHIVO]
    lite2obj export <archivo.litematic> [destino] [--packs ...] [--z-up] [--group-by ...]
    lite2obj gui [<archivo.litematic>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, assetpack, assets, litematic, paths

__all__ = ["main"]


def _human(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _human_bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover


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

    if args.packs:
        print("\nResource packs disponibles:")
        for resource_pack in assets.list_packs():
            print(f"  {resource_pack.name}  ({resource_pack.kind})")

    pack = paths.asset_pack()
    if pack is None:
        print("\nArchivo de assets: no generado; se usará el client jar si existe.")
    else:
        manifest = assetpack.read_manifest(pack) or {}
        version = manifest.get("minecraft_version", "?")
        print(f"\nArchivo de assets: {pack} (Minecraft {version})")
    return 0


def cmd_assets_build(args: argparse.Namespace) -> int:
    jar = Path(args.jar) if args.jar is not None else paths.client_jar()
    if jar is None:
        print(
            "No se encontró ningún client jar de Minecraft. "
            "Usa --jar o define LITE2OBJ_CLIENT_JAR.",
            file=sys.stderr,
        )
        return 1
    output = Path(args.output) if args.output is not None else paths.asset_pack_output()
    try:
        info = assetpack.build_asset_pack(jar, output, force=args.force)
    except (assetpack.AssetPackError, FileExistsError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Minecraft  {info.minecraft_version}")
    print(f"Archivo    {info.path}")
    print(f"Contenido  {info.files} archivos, {_human_bytes(info.uncompressed_bytes)}")
    print(f"Tamaño     {_human_bytes(info.compressed_bytes)}")
    print("\nEl programa usará este archivo automáticamente.")
    return 0


def cmd_assets_info(args: argparse.Namespace) -> int:
    pack = Path(args.pack) if args.pack is not None else paths.asset_pack()
    if pack is None:
        print(
            "No se encontró ningún archivo de assets. Genera uno con 'lite2obj assets build'.",
            file=sys.stderr,
        )
        return 1
    manifest = assetpack.read_manifest(pack)
    if manifest is None:
        print(f"El archivo no es válido: {pack}", file=sys.stderr)
        return 1

    print(f"Archivo    {pack}")
    for key in (
        "minecraft_version",
        "generator",
        "created",
        "source",
        "files",
        "uncompressed_bytes",
    ):
        if key in manifest:
            print(f"{key:<18} {manifest[key]}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .pipeline import RenderOptions, export

    destination = args.destination or (args.path.parent / f"{args.path.stem}_obj")
    options = RenderOptions(
        packs=args.packs,
        cull=not args.no_cull,
        tint=not args.no_tint,
        atlas_scale=args.atlas_scale,
        entities=not args.no_entities,
        mobs=args.mobs,
        droped_items=args.droped_items,
    )
    result = export(
        args.path,
        destination,
        options,
        name=args.name,
        up_axis="z" if args.z_up else "y",
        group_by=args.group_by,
        scale=args.scale,
    )
    print(f"OBJ    {result.obj}  ({_human_bytes(result.bytes_written)})")
    if result.mtl:
        print(f"MTL    {result.mtl}")
    if result.atlas:
        print(f"atlas  {result.atlas}  ({result.atlas.stat().st_size} B)")
    print(
        f"\n{_human(result.faces)} caras, {_human(result.vertices)} vértices, "
        f"{_human(result.uvs)} UVs"
    )
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run

    run(args.path)
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
    listing.add_argument("--packs", action="store_true", help="lista también los resource packs")
    listing.set_defaults(func=cmd_list)

    asset_parser = sub.add_parser("assets", help="gestiona el archivo de assets de Minecraft")
    asset_sub = asset_parser.add_subparsers(dest="assets_command", required=True)

    asset_build = asset_sub.add_parser(
        "build", help="extrae blockstates, modelos y texturas de un client jar"
    )
    asset_build.add_argument(
        "--jar",
        type=Path,
        default=None,
        help="client jar de Minecraft (por defecto, el detectado más reciente)",
    )
    asset_build.add_argument(
        "--output",
        type=Path,
        default=None,
        help="archivo .l2oassets de salida (por defecto, en los datos del usuario)",
    )
    asset_build.add_argument(
        "--force", action="store_true", help="sobrescribe el archivo si ya existe"
    )
    asset_build.set_defaults(func=cmd_assets_build)

    asset_info = asset_sub.add_parser("info", help="muestra el manifiesto de un archivo de assets")
    asset_info.add_argument(
        "pack",
        type=Path,
        nargs="?",
        default=None,
        help="archivo .l2oassets (por defecto, el que usa el programa)",
    )
    asset_info.set_defaults(func=cmd_assets_info)

    export = sub.add_parser("export", help="exporta a OBJ + MTL + atlas")
    export.add_argument("path", type=Path, help="archivo .litematic")
    export.add_argument("destination", type=Path, nargs="?", default=None, help="carpeta de salida")
    export.add_argument("--name", default=None, help="nombre base de los archivos")
    export.add_argument(
        "--packs",
        action="append",
        default=[],
        metavar="PACK",
        help="resource pack (nombre o ruta); repetible, el último tiene prioridad",
    )
    export.add_argument("--z-up", action="store_true", help="exporta con el eje Z arriba (Blender)")
    export.add_argument(
        "--group-by",
        choices=["none", "block", "alpha"],
        default="none",
        help="agrupa las caras del OBJ por bloque o por modo de transparencia",
    )
    export.add_argument("--no-cull", action="store_true", help="no descarta caras ocultas")
    export.add_argument("--no-tint", action="store_true", help="desactiva los tintes de bioma")
    export.add_argument(
        "--no-entities",
        action="store_true",
        help="no dibuja entidades ni *block entities* (cofres, marcos, cuadros...)",
    )
    export.add_argument(
        "--mobs",
        action="store_true",
        help="dibuja criaturas (desactivado por defecto; requiere entidades)",
    )
    export.add_argument(
        "--droped-items",
        action="store_true",
        help="dibuja items caídos (desactivado por defecto; requiere entidades)",
    )
    export.add_argument(
        "--atlas-scale",
        type=int,
        choices=[1, 2, 4],
        default=1,
        help="multiplica la resolución de las texturas del atlas",
    )
    export.add_argument("--scale", type=float, default=1.0, help="escala de la geometría exportada")
    export.set_defaults(func=cmd_export)

    gui = sub.add_parser("gui", help="abre la aplicación de escritorio")
    gui.add_argument("path", type=Path, nargs="?", default=None, help="archivo .litematic")
    gui.set_defaults(func=cmd_gui)

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
