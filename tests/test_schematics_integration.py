"""Integración: valida el parser contra el esquemático de referencia.

Comprueba que el archivo parsea sin excepciones, que el recuento de bloques coincide con
``Metadata.TotalBlocks`` y que las dimensiones son coherentes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lite2obj import litematic

SCHEMATICS = [Path(__file__).resolve().parents[1] / "all_blocks_and_entities.litematic"]

pytestmark = pytest.mark.skipif(
    not SCHEMATICS[0].is_file(), reason="no se encontró el esquemático de referencia"
)


@pytest.mark.parametrize("path", SCHEMATICS, ids=[p.name for p in SCHEMATICS])
def test_schematic_parses(path):
    doc = litematic.load(path)

    assert doc.regions, f"{path.name}: sin regiones"
    assert doc.enclosing_size[0] > 0
    assert doc.enclosing_size[1] > 0
    assert doc.enclosing_size[2] > 0

    # El recuento calculado desde los voxels debe coincidir con el que Litematica
    # guardó en Metadata.TotalBlocks.
    assert doc.block_count == doc.total_blocks, (
        f"{path.name}: calculado {doc.block_count} != metadata {doc.total_blocks}"
    )

    # Los índices de la rejilla deben ser siempre válidos dentro de la paleta.
    for region in doc.regions:
        assert region.grid.min() >= 0
        assert region.grid.max() < len(region.palette)
        assert len(region.air_mask) == len(region.palette)
