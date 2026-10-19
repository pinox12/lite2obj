"""Aplicación de escritorio de lite2obj (PySide6 + OpenGL de Qt)."""

from __future__ import annotations

__all__ = ["MainWindow", "main", "run"]


def run(path=None):
    """Arranca la ventana principal. Importa Qt de forma perezosa."""
    from .app import run as _run

    return _run(path)


def main() -> int:
    from .app import main as _main

    return _main()
