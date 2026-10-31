"""Helper para tests que necesitan los assets reales de Minecraft.

Usa el archivo ``.l2oassets``; no busca client jars ni instalaciones. Se omiten
(``skip``) si no hay archivo, de modo que la batería de tests unitarios sigue funcionando
en cualquier máquina.
"""

from __future__ import annotations

from functools import lru_cache

import pytest

from lite2obj import paths
from lite2obj.assets import ResourceManager

__all__ = ["jar_resources"]


@lru_cache(maxsize=1)
def _resources() -> ResourceManager | None:
    try:
        return ResourceManager.discover()
    except FileNotFoundError:
        return None


@lru_cache(maxsize=1)
def _available() -> bool:
    return paths.asset_pack() is not None


def jar_resources() -> ResourceManager:
    """ResourceManager con el archivo de assets, o ``pytest.skip`` si no hay."""
    if not _available():
        pytest.skip("no se encontró ningún archivo de assets de Minecraft")
    resources = _resources()
    assert resources is not None
    return resources
