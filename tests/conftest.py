"""Asegura que el directorio ``tests/`` esté en ``sys.path`` para los helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from lite2obj import i18n

_TESTS_DIR = Path(__file__).parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


@pytest.fixture(autouse=True)
def _default_language():
    """Fija el español para que los tests de textos no dependan del sistema."""
    previous = os.environ.get("LITE2OBJ_LANG")
    os.environ["LITE2OBJ_LANG"] = "es"
    i18n.set_language("es")
    yield
    if previous is None:
        os.environ.pop("LITE2OBJ_LANG", None)
    else:
        os.environ["LITE2OBJ_LANG"] = previous
    i18n.set_language(None)
