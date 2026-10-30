"""Lanzador de la app de escritorio para PyInstaller.

PyInstaller necesita un script concreto como punto de entrada; este módulo sólo
importa la función ``main`` del paquete y devuelve su código de salida.
"""

from __future__ import annotations

from lite2obj.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
