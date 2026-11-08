"""Interfaz de línea de comandos de lite2obj.

Uso::

    lite2obj info <archivo.litematic> [--blocks N]
    lite2obj list
    lite2obj assets build --jar JAR [--output ARCHIVO] [--force]
    lite2obj assets info [ARCHIVO]
    lite2obj export <archivo.litematic> [destino] [--packs RUTA] [--z-up] [--group-by ...]
    lite2obj gui [<archivo.litematic>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, assetpack, assets, i18n, litematic, paths
from .i18n import _

__all__ = ["main"]


def _human(n: int) -> str:
    text = f"{n:,}"
    return text.replace(",", ".") if i18n.current_language() == "es" else text


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
    print(_("  archivo      {path}").format(path=args.path))
    if doc.author:
        print(_("  autor        {author}").format(author=doc.author))
    if doc.description:
        print(_("  descripción  {description}").format(description=doc.description))
    print(
        _("  tamaño       {width} x {height} x {depth}").format(
            width=size[0], height=size[1], depth=size[2]
        )
    )
    print(_("  regiones     {count}").format(count=len(doc.regions)))
    print(_("  bloques      {count}").format(count=_human(doc.block_count)))
    print(_("  volumen      {volume}").format(volume=_human(size[0] * size[1] * size[2])))
    print(
        _("  data version {version} (formato v{major}.{minor})").format(
            version=doc.data_version, major=doc.version, minor=doc.sub_version
        )
    )

    for region in doc.regions:
        print("\n" + _("  región '{name}'").format(name=region.name))
        print(_("    origen    {origin}").format(origin=region.origin))
        print(
            _("    tamaño    {width} x {height} x {depth}").format(
                width=region.size[0], height=region.size[1], depth=region.size[2]
            )
        )
        print(_("    paleta    {count} estados").format(count=len(region.palette)))
        print(_("    entidades {count} tile entities").format(count=len(region.tile_entities)))

    counts = doc.block_counts()
    top = list(counts.items())[: args.blocks]
    print(
        "\n"
        + _("  {count} tipos de bloque distintos; los más frecuentes:").format(count=len(counts))
    )
    width = max((len(name) for name, _ in top), default=0)
    total = doc.block_count or 1
    for name, count in top:
        pct = count / total * 100
        print(f"    {name:<{width}}  {_human(count):>10}  {pct:5.1f}%")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    pack = paths.asset_pack()
    if pack is None:
        print(assets.missing_asset_pack_message(), file=sys.stderr)
        return 1
    manifest = assetpack.read_manifest(pack)
    if manifest is None:
        print(
            _("El archivo de assets no es válido: {path}").format(path=pack),
            file=sys.stderr,
        )
        return 1

    print(_("Archivo de assets: {path}").format(path=pack))
    for key in (
        "minecraft_version",
        "generator",
        "created",
        "source",
        "files",
        "uncompressed_bytes",
    ):
        if key in manifest:
            print(f"  {key:<18} {manifest[key]}")
    print("\n" + _("No se buscan esquemáticos ni resource packs automáticamente."))
    return 0


def cmd_assets_build(args: argparse.Namespace) -> int:
    jar = Path(args.jar).expanduser() if args.jar is not None else paths.explicit_client_jar()
    if jar is None:
        print(
            _("No se indicó ningún client jar. Usa --jar <ruta> o define LITE2OBJ_CLIENT_JAR."),
            file=sys.stderr,
        )
        return 1
    output = Path(args.output) if args.output is not None else paths.asset_pack_output()
    try:
        info = assetpack.build_asset_pack(jar, output, force=args.force)
    except (assetpack.AssetPackError, FileExistsError, OSError) as exc:
        print(_("Error: {error}").format(error=exc), file=sys.stderr)
        return 1

    print(_("Minecraft  {version}").format(version=info.minecraft_version))
    print(_("Archivo    {path}").format(path=info.path))
    print(
        _("Contenido  {files} archivos, {size}").format(
            files=info.files, size=_human_bytes(info.uncompressed_bytes)
        )
    )
    print(_("Tamaño     {size}").format(size=_human_bytes(info.compressed_bytes)))
    print("\n" + _("El programa usará este archivo si está en los datos del usuario."))
    return 0


def cmd_assets_info(args: argparse.Namespace) -> int:
    pack = Path(args.pack) if args.pack is not None else paths.asset_pack()
    if pack is None:
        print(
            _("No se encontró ningún archivo de assets. Genera uno con 'lite2obj assets build'."),
            file=sys.stderr,
        )
        return 1
    manifest = assetpack.read_manifest(pack)
    if manifest is None:
        print(
            _("El archivo no es válido: {path}").format(path=pack),
            file=sys.stderr,
        )
        return 1

    print(_("Archivo    {path}").format(path=pack))
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
    try:
        result = export(
            args.path,
            destination,
            options,
            name=args.name,
            up_axis="z" if args.z_up else "y",
            group_by=args.group_by,
            scale=args.scale,
        )
    except (FileNotFoundError, assets.InvalidAssetPackError) as exc:
        print(_("Error: {error}").format(error=exc), file=sys.stderr)
        return 1
    print(
        _("OBJ    {path}  ({size})").format(
            path=result.obj, size=_human_bytes(result.bytes_written)
        )
    )
    if result.mtl:
        print(_("MTL    {path}").format(path=result.mtl))
    if result.atlas:
        print(
            _("atlas  {path}  ({size} B)").format(
                path=result.atlas, size=result.atlas.stat().st_size
            )
        )
    print(
        "\n"
        + _("{faces} caras, {vertices} vértices, {uvs} UVs").format(
            faces=_human(result.faces),
            vertices=_human(result.vertices),
            uvs=_human(result.uvs),
        )
    )
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run

    run(args.path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lite2obj", description=_("Interfaz de línea de comandos de lite2obj.")
    )
    parser.add_argument("--version", action="version", version=f"lite2obj {__version__}")
    parser.add_argument(
        "--lang",
        choices=("en", "es"),
        default=None,
        help=_("idioma de la interfaz (en, es)"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help=_("muestra metadatos y recuento de bloques"))
    info.add_argument("path", type=Path, help=_("archivo .litematic"))
    info.add_argument(
        "--blocks",
        type=int,
        default=15,
        help=_("cuántos tipos de bloque listar (0 = todos)"),
    )
    info.set_defaults(func=cmd_info)

    listing = sub.add_parser("list", help=_("muestra el archivo de assets activo"))
    listing.set_defaults(func=cmd_list)

    asset_parser = sub.add_parser("assets", help=_("gestiona el archivo de assets de Minecraft"))
    asset_sub = asset_parser.add_subparsers(dest="assets_command", required=True)

    asset_build = asset_sub.add_parser(
        "build", help=_("extrae blockstates, modelos y texturas de un client jar")
    )
    asset_build.add_argument(
        "--jar",
        type=Path,
        default=None,
        help=_("client jar de Minecraft (obligatorio si no defines LITE2OBJ_CLIENT_JAR)"),
    )
    asset_build.add_argument(
        "--output",
        type=Path,
        default=None,
        help=_("archivo .l2oassets de salida (por defecto, en los datos del usuario)"),
    )
    asset_build.add_argument(
        "--force", action="store_true", help=_("sobrescribe el archivo si ya existe")
    )
    asset_build.set_defaults(func=cmd_assets_build)

    asset_info = asset_sub.add_parser(
        "info", help=_("muestra el manifiesto de un archivo de assets")
    )
    asset_info.add_argument(
        "pack",
        type=Path,
        nargs="?",
        default=None,
        help=_("archivo .l2oassets (por defecto, el que usa el programa)"),
    )
    asset_info.set_defaults(func=cmd_assets_info)

    export = sub.add_parser("export", help=_("exporta a OBJ + MTL + atlas"))
    export.add_argument("path", type=Path, help=_("archivo .litematic"))
    export.add_argument(
        "destination", type=Path, nargs="?", default=None, help=_("carpeta de salida")
    )
    export.add_argument("--name", default=None, help=_("nombre base de los archivos"))
    export.add_argument(
        "--packs",
        action="append",
        default=[],
        metavar="PACK",
        help=_("ruta a un resource pack (.zip o carpeta); repetible, el último tiene prioridad"),
    )
    export.add_argument(
        "--z-up", action="store_true", help=_("exporta con el eje Z arriba (Blender)")
    )
    export.add_argument(
        "--group-by",
        choices=["none", "block", "alpha"],
        default="none",
        help=_("agrupa las caras del OBJ por bloque o por modo de transparencia"),
    )
    export.add_argument("--no-cull", action="store_true", help=_("no descarta caras ocultas"))
    export.add_argument("--no-tint", action="store_true", help=_("desactiva los tintes de bioma"))
    export.add_argument(
        "--no-entities",
        action="store_true",
        help=_("no dibuja entidades ni *block entities* (cofres, marcos, cuadros...)"),
    )
    export.add_argument(
        "--mobs",
        action="store_true",
        help=_("dibuja criaturas (desactivado por defecto; requiere entidades)"),
    )
    export.add_argument(
        "--droped-items",
        action="store_true",
        help=_("dibuja items caídos (desactivado por defecto; requiere entidades)"),
    )
    export.add_argument(
        "--atlas-scale",
        type=int,
        choices=[1, 2, 4],
        default=1,
        help=_("multiplica la resolución de las texturas del atlas"),
    )
    export.add_argument(
        "--scale", type=float, default=1.0, help=_("escala de la geometría exportada")
    )
    export.set_defaults(func=cmd_export)

    gui = sub.add_parser("gui", help=_("abre la aplicación de escritorio"))
    gui.add_argument("path", type=Path, nargs="?", default=None, help=_("archivo .litematic"))
    gui.set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    arguments, language = i18n.extract_language_option(sys.argv[1:] if argv is None else list(argv))
    i18n.configure(language)
    parser = build_parser()
    args = parser.parse_args(arguments)
    blocks = getattr(args, "blocks", 15)
    if blocks <= 0:
        args.blocks = 10**9
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
