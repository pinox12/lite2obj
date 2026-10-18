"""Helper para tests que necesitan el client jar real de Minecraft.

Se omiten (``skip``) si no hay jar disponible, de modo que la batería de tests unitarios
sigue funcionando en cualquier máquina.
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
    return paths.client_jar() is not None


def jar_resources() -> ResourceManager:
    """ResourceManager con el jar vanilla, o ``pytest.skip`` si no hay jar."""
    if not _available():
        pytest.skip("no se encontró ningún client jar de Minecraft")
    resources = _resources()
    assert resources is not None
    return resources
