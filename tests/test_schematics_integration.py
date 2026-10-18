"""Integración: valida el parser contra todos los ``.litematic`` de la máquina.

Se omite automáticamente si no se encuentra ninguno. Comprueba que cada archivo parsea
sin excepciones, que el recuento de bloques coincide con ``Metadata.TotalBlocks`` y que
las dimensiones son coherentes.
"""

from __future__ import annotations

import pytest

from lite2obj import litematic, paths

SCHEMATICS = paths.find_schematics()

pytestmark = pytest.mark.skipif(
    not SCHEMATICS, reason="no se encontraron archivos .litematic en la máquina"
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
