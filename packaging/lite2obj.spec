# -*- mode: python ; coding: utf-8 -*-
"""Especificación de PyInstaller para lite2obj.

Linux genera un directorio (``dist/lite2obj``) que el script de AppImage copia a
``AppDir``. Windows genera un único ``dist/lite2obj.exe`` portable. La variable
``LITE2OBJ_ONEFILE=1`` fuerza el modo de un solo archivo en cualquier plataforma.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent
ONEFILE = os.environ.get("LITE2OBJ_ONEFILE", "0") == "1"
ICON = ROOT / "packaging" / ("lite2obj.ico" if sys.platform == "win32" else "lite2obj.png")

# Los JSON de entidades viven dentro del paquete; el esquemático de prueba se
# deja en la raíz del bundle para que ``_demo_schematic_path`` lo encuentre.
datas = collect_data_files("lite2obj")
datas.append((str(ROOT / "all_blocks_and_entities.litematic"), "."))

# ``gui/__init__.py`` importa estos módulos de forma perezosa.
hiddenimports = [
    "lite2obj.gui.app",
    "lite2obj.gui.camera",
    "lite2obj.gui.viewport",
]

a = Analysis(
    [str(ROOT / "packaging" / "lite2obj_gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if ONEFILE else [],
    a.datas if ONEFILE else [],
    [],
    name="lite2obj",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    exclude_binaries=not ONEFILE,
    icon=str(ICON) if ICON.is_file() and sys.platform == "win32" else None,
)

if not ONEFILE:
    COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="lite2obj",
    )
