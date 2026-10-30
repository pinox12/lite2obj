#!/usr/bin/env bash
# Construye una AppImage autocontenida con PyInstaller + appimagetool.
#
# Uso:
#   ./packaging/build_appimage.sh
#
# Requisitos: Linux x86_64, uv, curl y FUSE (o APPIMAGE_EXTRACT_AND_RUN=1).
# appimagetool se descarga en build/tools si no está en PATH; también se puede
# indicar con APPIMAGETOOL=/ruta/a/appimagetool.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ "$(uname -m)" != "x86_64" ]; then
    echo "Este script genera una AppImage x86_64; arquitectura detectada: $(uname -m)" >&2
    exit 1
fi

APPDIR="$ROOT/build/appimage/Lite2obj.AppDir"
TOOLS="$ROOT/build/tools"
APPIMAGETOOL="${APPIMAGETOOL:-}"

echo "==> Preparando el archivo de assets para incluirlo en la AppImage"
uv run python packaging/prepare_assets.py

echo "==> Construyendo el ejecutable con PyInstaller"
rm -rf "$ROOT/dist/lite2obj"
LITE2OBJ_ONEFILE=0 uv run --group packaging pyinstaller \
    --noconfirm --clean \
    --distpath "$ROOT/dist" \
    --workpath "$ROOT/build/pyinstaller" \
    packaging/lite2obj.spec

echo "==> Preparando AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/lite2obj"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# El directorio onedir de PyInstaller se copia tal cual a usr/lib; el lanzador
# de usr/bin lo encuentra por ruta relativa.
cp -a "$ROOT/dist/lite2obj/." "$APPDIR/usr/lib/lite2obj/"

install -Dm755 "$ROOT/packaging/appimage/lite2obj" "$APPDIR/usr/bin/lite2obj"
install -Dm755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
install -Dm644 "$ROOT/packaging/appimage/lite2obj.desktop" \
    "$APPDIR/lite2obj.desktop"
install -Dm644 "$ROOT/packaging/appimage/lite2obj.desktop" \
    "$APPDIR/usr/share/applications/lite2obj.desktop"
install -Dm644 "$ROOT/packaging/lite2obj.png" "$APPDIR/lite2obj.png"
install -Dm644 "$ROOT/packaging/lite2obj.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/lite2obj.png"

if [ -z "$APPIMAGETOOL" ]; then
    if command -v appimagetool >/dev/null 2>&1; then
        APPIMAGETOOL="$(command -v appimagetool)"
    else
        mkdir -p "$TOOLS"
        APPIMAGETOOL="$TOOLS/appimagetool-x86_64.AppImage"
        if [ ! -x "$APPIMAGETOOL" ]; then
            echo "==> Descargando appimagetool"
            curl -fL --retry 3 \
                -o "$APPIMAGETOOL" \
                "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
            chmod +x "$APPIMAGETOOL"
        fi
    fi
fi

VERSION="$(uv run python -c 'import lite2obj; print(lite2obj.__version__)')"
OUTPUT="$ROOT/dist/lite2obj-${VERSION}-x86_64.AppImage"

echo "==> Generando $OUTPUT"
# APPIMAGE_EXTRACT_AND_RUN evita depender de FUSE en máquinas de compilación.
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" \
    --no-appstream "$APPDIR" "$OUTPUT"

echo "Listo: $OUTPUT"
