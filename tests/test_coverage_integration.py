"""Cobertura de resolución de modelos sobre el esquemático de referencia (F2).

Requiere el archivo de assets y ``all_blocks_and_entities.litematic``. Mide el porcentaje
de instancias de bloque para las que se puede generar geometría; el objetivo del plan es
superar el 99.9%.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from _jar import jar_resources

from lite2obj import litematic
from lite2obj.models import BlockModelError, ModelResolver

SCHEMATICS = [Path(__file__).resolve().parents[1] / "all_blocks_and_entities.litematic"]

pytestmark = pytest.mark.skipif(
    not SCHEMATICS[0].is_file(), reason="no se encontró el esquemático de referencia"
)


@pytest.fixture(scope="module")
def resolver() -> ModelResolver:
    return ModelResolver(jar_resources())


def _scan(resolver: ModelResolver):
    total = 0
    unsupported: Counter[str] = Counter()
    for path in SCHEMATICS:
        doc = litematic.load(path)
        for region in doc.regions:
            for index, count in enumerate(region.palette_counts()):
                if count == 0:
                    continue
                block = region.palette[index]
                if block.is_air:
                    continue
                total += int(count)
                try:
                    if not resolver.geometry(block):
                        unsupported[block.name] += int(count)
                except BlockModelError:
                    unsupported[block.name] += int(count)
    return total, unsupported


def test_geometry_coverage_over_real_schematics(resolver):
    total, unsupported = _scan(resolver)
    assert total > 0
    covered = total - sum(unsupported.values())
    ratio = covered / total
    assert ratio >= 0.999, (
        f"cobertura {ratio:.4%} insuficiente; sin geometría: {unsupported.most_common(10)}"
    )


def test_unsupported_blocks_are_unknown_mods(resolver):
    _, unsupported = _scan(resolver)
    # Lo único que no se cubre debe ser de namespaces que no son vanilla.
    for name in unsupported:
        namespace, _, _ = name.partition(":")
        assert namespace != "minecraft", f"bloque vanilla sin resolver: {name}"


def test_alias_is_used_for_renamed_blocks(resolver):
    # El esquemático de referencia ya usa los identificadores actuales, así que aquí se
    # comprueba el alias directamente contra los assets reales: ``grass`` se renombró a
    # ``short_grass`` y debe seguir generando geometría.
    block = litematic.BlockState("minecraft:grass")
    state = resolver.resolve(block)
    assert state.used_alias
    assert state.model_name == "minecraft:short_grass"
    assert resolver.geometry(block)
