"""Deja el archivo de assets en la raíz para incluirlo en los portables.

Los scripts de empaquetado lo llaman antes de PyInstaller. Si ya existe en
``build/assets`` se respeta; si no, copia el que usa lite2obj (datos del usuario o
``LITE2OBJ_ASSET_PACK``); y si tampoco existe, lo genera desde el client jar indicado con
``--jar`` o ``LITE2OBJ_CLIENT_JAR``.

Con ``--force`` ignora el archivo existente y lo regenera desde el client jar, útil
después de actualizar Minecraft.

Con ``--skip`` (o ``LITE2OBJ_SKIP_ASSETS=1``) no prepara nada: sirve para las builds de
CI/CD que se publican **sin** texturas de Minecraft. Con ``--optional`` (o
``LITE2OBJ_ASSETS_OPTIONAL=1``) tampoco falla si no hay pack ni client jar; sólo avisa.

El archivo queda ignorado por git (``*.l2oassets``) porque contiene assets de Minecraft.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from lite2obj import assetpack, paths

ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = ROOT / "build" / "assets"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenera el archivo desde el client jar aunque ya exista",
    )
    parser.add_argument(
        "--jar",
        type=Path,
        default=None,
        help="client jar de Minecraft (por defecto, LITE2OBJ_CLIENT_JAR)",
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        default=os.environ.get("LITE2OBJ_SKIP_ASSETS", "0") == "1",
        help="no incluye el archivo de assets (builds de CI/CD sin texturas)",
    )
    parser.add_argument(
        "--optional",
        action="store_true",
        default=os.environ.get("LITE2OBJ_ASSETS_OPTIONAL", "0") == "1",
        help="no falla si faltan el archivo de assets y el client jar",
    )
    args = parser.parse_args(argv)

    if args.skip:
        print("Build sin archivo de assets: se generará al abrir la app.")
        return 0

    target = TARGET_DIR / paths.ASSET_PACK_NAME
    if target.is_file() and not args.force:
        manifest = assetpack.read_manifest(target) or {}
        version = manifest.get("minecraft_version", "?")
        print(f"Archivo de assets: {target.name} (Minecraft {version})")
        return 0

    existing = None if args.force else paths.asset_pack()
    if existing is not None:
        shutil.copy2(existing, target)
        manifest = assetpack.read_manifest(target) or {}
        version = manifest.get("minecraft_version", "?")
        print(f"Copiado {existing} -> {target}")
        print(f"Minecraft {version}")
        return 0

    jar = args.jar.expanduser() if args.jar is not None else paths.explicit_client_jar()
    if jar is None:
        message = (
            "No hay archivo de assets ni client jar de Minecraft. "
            "Usa --jar <ruta> o define LITE2OBJ_CLIENT_JAR."
        )
        if args.optional:
            print(message, file=sys.stderr)
            print("Build sin archivo de assets: se generará al abrir la app.", file=sys.stderr)
            return 0
        print(message, file=sys.stderr)
        return 1

    info = assetpack.build_asset_pack(jar, target, force=True)
    print(f"Generado {target} desde {jar.name} (Minecraft {info.minecraft_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
