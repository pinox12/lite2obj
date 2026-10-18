"""Cobertura de resolución de modelos sobre los esquemáticos reales (F2).

Requiere a la vez un client jar y archivos ``.litematic`` en la máquina. Mide el
porcentaje de instancias de bloque para las que se puede generar geometría; el objetivo
del plan es superar el 99.9%.
"""

from __future__ import annotations

from collections import Counter

import pytest
from _jar import jar_resources

from lite2obj import litematic, paths
from lite2obj.models import BlockModelError, ModelResolver

SCHEMATICS = paths.find_schematics()

pytestmark = pytest.mark.skipif(
    not SCHEMATICS, reason="no se encontraron archivos .litematic en la máquina"
)


@pytest.fixture(scope="module")
def resolver() -> ModelResolver:
    return ModelResolver(jar_resources())


def _scan(resolver: ModelResolver):
    total = 0
    unsupported: Counter[str] = Counter()
    alias_count = 0
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
                    state = resolver.resolve(block)
                    if state.used_alias:
                        alias_count += int(count)
                    if not resolver.geometry(block):
                        unsupported[block.name] += int(count)
                except BlockModelError:
                    unsupported[block.name] += int(count)
    return total, unsupported, alias_count


def test_geometry_coverage_over_real_schematics(resolver):
    total, unsupported, _ = _scan(resolver)
    assert total > 0
    covered = total - sum(unsupported.values())
    ratio = covered / total
    assert ratio >= 0.999, (
        f"cobertura {ratio:.4%} insuficiente; sin geometría: {unsupported.most_common(10)}"
    )


def test_unsupported_blocks_are_unknown_mods(resolver):
    _, unsupported, _ = _scan(resolver)
    # Lo único que no se cubre debe ser de namespaces que no son vanilla.
    for name in unsupported:
        namespace, _, _ = name.partition(":")
        assert namespace != "minecraft", f"bloque vanilla sin resolver: {name}"


def test_alias_is_used_for_renamed_blocks(resolver):
    _, _, alias_count = _scan(resolver)
    assert alias_count > 0
