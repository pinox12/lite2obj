"""Deja el archivo de assets en la raíz para incluirlo en los portables.

Los scripts de empaquetado lo llaman antes de PyInstaller. Si ya existe en
``build/assets`` se respeta; si no, copia el que usa lite2obj (datos del usuario o
``LITE2OBJ_ASSET_PACK``); y si tampoco existe, lo genera desde el client jar detectado.

Con ``--force`` ignora el archivo existente y lo regenera desde el client jar, útil
después de actualizar Minecraft.

El archivo queda ignorado por git (``*.l2oassets``) porque contiene assets de Minecraft.
"""

from __future__ import annotations

import argparse
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
    args = parser.parse_args(argv)

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

    jar = paths.client_jar()
    if jar is None:
        print(
            "No hay archivo de assets ni client jar de Minecraft. "
            "Genera uno con 'uv run lite2obj assets build' o define LITE2OBJ_ASSET_PACK.",
            file=sys.stderr,
        )
        return 1

    info = assetpack.build_asset_pack(jar, target, force=True)
    print(f"Generado {target} desde {jar.name} (Minecraft {info.minecraft_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
