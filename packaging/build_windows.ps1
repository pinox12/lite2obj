# Construye lite2obj.exe portable (un solo archivo, sin instalador).
#
# Uso, desde PowerShell en Windows:
#   .\packaging\build_windows.ps1
#
# Requisitos: Windows x86_64 y uv. El resultado es dist\lite2obj.exe.
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Construyendo lite2obj.exe con PyInstaller"
Remove-Item -Recurse -Force "$Root\dist\lite2obj" -ErrorAction SilentlyContinue
Remove-Item -Force "$Root\dist\lite2obj.exe" -ErrorAction SilentlyContinue
$env:LITE2OBJ_ONEFILE = "1"
uv run --group packaging pyinstaller `
    --noconfirm --clean `
    --distpath "$Root\dist" `
    --workpath "$Root\build\pyinstaller" `
    packaging\lite2obj.spec

$Exe = Join-Path $Root "dist\lite2obj.exe"
if (-not (Test-Path $Exe)) {
    throw "PyInstaller no generó $Exe"
}

Write-Host "Listo: $Exe"
