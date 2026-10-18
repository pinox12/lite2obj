"""Tests del parser de ``.litematic``.

Cubre el desempaquetado de bits contra una implementación de referencia
(``LitematicaBitArray``), archivos sintéticos con tamaños negativos y multi-región,
y el fixture real de 42 bloques.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from nbt_writer import pack_bits, write_nbt

from lite2obj import litematic, nbt

FIXTURE = Path(__file__).parent / "fixtures" / "store_1x4x4.litematic"


# --------------------------------------------------------------------------- #
# Implementación de referencia (port de LitematicaBitArray.getAt)
# --------------------------------------------------------------------------- #


def reference_get(longs: list[int], bits: int, index: int) -> int:
    # Los longs se tratan como sin signo (shift lógico), igual que en Java.
    u = [v & 0xFFFFFFFFFFFFFFFF for v in longs]
    start = index * bits
    start_long = start >> 6
    end_long = ((index + 1) * bits - 1) >> 6
    offset = start & 0x3F
    mask = (1 << bits) - 1
    if start_long == end_long:
        return (u[start_long] >> offset) & mask
    spill = 64 - offset
    return ((u[start_long] >> offset) | (u[end_long] << spill)) & mask


# --------------------------------------------------------------------------- #
# Empaquetado de bits
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bits", [1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16])
def test_unpack_bits_matches_reference(bits):
    rng = np.random.default_rng(bits)
    count = 2000
    values = rng.integers(0, 1 << bits, size=count, dtype=np.int64)
    longs = pack_bits(values.tolist(), bits)

    got = litematic.unpack_bits(np.asarray(longs, dtype=np.int64), bits, count)
    expected = [reference_get(longs, bits, i) for i in range(count)]

    assert got.tolist() == expected
    assert got.dtype == np.int32


def test_unpack_bits_values_cross_long_boundaries():
    # bits=5 -> 12 valores por long y 4 bits de holgura: siempre cruza el límite.
    values = list(range(31))
    longs = pack_bits(values, 5)
    got = litematic.unpack_bits(np.asarray(longs, dtype=np.int64), 5, len(values))
    assert got.tolist() == values


def test_unpack_bits_edge_cases():
    assert litematic.unpack_bits(np.zeros(0, np.int64), 4, 0).tolist() == []
    # bits=0 (paleta de un solo elemento) devuelve ceros.
    assert litematic.unpack_bits(np.zeros(0, np.int64), 0, 7).tolist() == [0] * 7
    # Un array demasiado pequeño es un error explícito.
    with pytest.raises(litematic.LitematicError):
        litematic.unpack_bits(np.zeros(1, np.int64), 8, 64)
    with pytest.raises(litematic.LitematicError):
        litematic.unpack_bits(np.zeros(4, np.int64), 33, 1)


# --------------------------------------------------------------------------- #
# Construcción de archivos sintéticos
# --------------------------------------------------------------------------- #


def _palette(*names: str):
    return (
        nbt.TAG_LIST,
        (
            nbt.TAG_COMPOUND,
            [{"Name": (nbt.TAG_STRING, name)} for name in names],
        ),
    )


def make_region(
    name: str,
    position: tuple[int, int, int],
    size: tuple[int, int, int],
    palette: list[str],
    grid_yzx: list[int],
    tile_entities: list[dict] | None = None,
):
    """``grid_yzx`` son los índices de paleta en orden YZX (x más rápido)."""
    bits = (len(palette) - 1).bit_length()
    longs = pack_bits(grid_yzx, bits)
    return (
        name,
        (
            nbt.TAG_COMPOUND,
            {
                "Position": (
                    nbt.TAG_COMPOUND,
                    {k: (nbt.TAG_INT, v) for k, v in zip("xyz", position, strict=True)},
                ),
                "Size": (
                    nbt.TAG_COMPOUND,
                    {k: (nbt.TAG_INT, v) for k, v in zip("xyz", size, strict=True)},
                ),
                "BlockStatePalette": _palette(*palette),
                "BlockStates": (nbt.TAG_LONG_ARRAY, longs),
                "TileEntities": (nbt.TAG_LIST, (nbt.TAG_COMPOUND, list(tile_entities or []))),
                "Entities": (nbt.TAG_LIST, (nbt.TAG_END, [])),
                "PendingBlockTicks": (nbt.TAG_LIST, (nbt.TAG_END, [])),
                "PendingFluidTicks": (nbt.TAG_LIST, (nbt.TAG_END, [])),
            },
        ),
    )


def make_litematic(regions, name="synthetic", author="tester", total_blocks=0) -> bytes:
    return write_nbt(
        {
            "Version": (nbt.TAG_INT, 7),
            "SubVersion": (nbt.TAG_INT, 1),
            "MinecraftDataVersion": (nbt.TAG_INT, 4790),
            "Metadata": (
                nbt.TAG_COMPOUND,
                {
                    "Name": (nbt.TAG_STRING, name),
                    "Author": (nbt.TAG_STRING, author),
                    "Description": (nbt.TAG_STRING, "prueba"),
                    "TotalBlocks": (nbt.TAG_INT, total_blocks),
                    "TotalVolume": (nbt.TAG_INT, 0),
                    "TimeCreated": (nbt.TAG_LONG, 0),
                    "TimeModified": (nbt.TAG_LONG, 0),
                },
            ),
            "Regions": (nbt.TAG_COMPOUND, dict(regions)),
        }
    )


def test_positive_size_region_roundtrip():
    # 2x1x2: eje x más rápido (YZX).
    grid = [1, 2, 3, 4]
    doc = litematic.loads(
        make_litematic(
            [
                make_region(
                    "r",
                    position=(0, 0, 0),
                    size=(2, 1, 2),
                    palette=[
                        "minecraft:air",
                        "minecraft:stone",
                        "minecraft:dirt",
                        "minecraft:diamond_block",
                        "minecraft:oak_planks",
                    ],
                    grid_yzx=grid,
                )
            ]
        )
    )
    region = doc.regions[0]
    assert region.size == (2, 1, 2)
    assert region.origin == (0, 0, 0)
    # Orden YZX con x más rápido: (x0,z0)=1 (x1,z0)=2 (x0,z1)=3 (x1,z1)=4
    assert region.grid.tolist() == [[[1, 2], [3, 4]]]
    assert region.block_at(0, 0, 0).path == "stone"
    assert region.block_at(1, 0, 0).path == "dirt"
    assert region.block_at(0, 0, 1).path == "diamond_block"
    assert region.block_at(1, 0, 1).path == "oak_planks"
    assert region.volume == 4


def test_negative_size_is_mirrored_into_global_coordinates():
    # size.x = -2 con posición x=5 -> el índice 0 está en x=5 y el 1 en x=4.
    doc = litematic.loads(
        make_litematic(
            [
                make_region(
                    "r",
                    position=(5, 0, 0),
                    size=(-2, 1, 1),
                    palette=["minecraft:air", "minecraft:gold_block", "minecraft:iron_block"],
                    grid_yzx=[1, 2],  # índice 0 -> gold, índice 1 -> iron
                )
            ]
        )
    )
    region = doc.regions[0]
    assert region.size == (2, 1, 1)
    assert region.origin == (4, 0, 0)
    # Tras normalizar, el índice 0 corresponde a x=4 (iron).
    assert region.block_at(0, 0, 0).path == "iron_block"
    assert region.block_at(1, 0, 0).path == "gold_block"
    assert sorted(doc.iter_blocks()) == [
        (4, 0, 0, litematic.BlockState("minecraft:iron_block")),
        (5, 0, 0, litematic.BlockState("minecraft:gold_block")),
    ]


def test_multi_region_offsets_and_enclosing_size():
    doc = litematic.loads(
        make_litematic(
            [
                make_region(
                    "a",
                    position=(0, 0, 0),
                    size=(1, 1, 1),
                    palette=["minecraft:air", "minecraft:stone"],
                    grid_yzx=[1],
                ),
                make_region(
                    "b",
                    position=(4, 2, 0),
                    size=(2, 1, 1),
                    palette=["minecraft:air", "minecraft:stone"],
                    grid_yzx=[1, 1],
                ),
            ]
        )
    )
    assert [r.name for r in doc.regions] == ["a", "b"]
    assert doc.origin == (0, 0, 0)
    assert doc.enclosing_size == (6, 3, 1)
    assert doc.block_count == 3
    assert doc.block_counts() == {"minecraft:stone": 3}


def test_blockstate_properties_are_preserved():
    entry = {
        "Name": "minecraft:oak_stairs",
        "Properties": {"facing": "north", "half": "top", "waterlogged": "false"},
    }
    block = litematic.BlockState.from_nbt(entry)
    assert block.path == "oak_stairs"
    assert block.variant_key() == "facing=north,half=top,waterlogged=false"
    assert block.properties_dict()["half"] == "top"
    assert not block.is_air


def test_missing_regions_raises():
    with pytest.raises(litematic.LitematicError):
        litematic.loads(write_nbt({"Version": (nbt.TAG_INT, 7)}))


def test_cave_air_is_not_counted_as_a_block():
    # Litematica no cuenta ningún estado de aire (cave_air / void_air incluidos).
    doc = litematic.loads(
        make_litematic(
            [
                make_region(
                    "r",
                    position=(0, 0, 0),
                    size=(3, 1, 1),
                    palette=[
                        "minecraft:air",
                        "minecraft:cave_air",
                        "minecraft:stone",
                        "minecraft:void_air",
                    ],
                    grid_yzx=[1, 2, 3],
                )
            ]
        )
    )
    assert doc.block_count == 1
    assert doc.block_counts() == {"minecraft:stone": 1}
    assert [b.path for _, _, _, b in doc.iter_blocks()] == ["stone"]
    assert len(list(doc.iter_blocks(include_air=True))) == 3


# --------------------------------------------------------------------------- #
# Fixture real
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def store():
    return litematic.load(FIXTURE)


def test_real_fixture_metadata(store):
    assert store.name == "almacen bonito 1x4x4"
    assert store.author == "pinox12"
    assert store.version == 7
    assert store.sub_version == 1
    assert store.data_version == 4790


def test_real_fixture_matches_metadata_block_count(store):
    # El valor que Litematica guardó en Metadata.TotalBlocks debe coincidir con
    # el recuento calculado desde la rejilla de voxels.
    assert store.total_blocks == 42
    assert store.block_count == 42
    assert len(store.regions) == 1
    assert store.regions[0].size == (2, 4, 8)
    assert store.enclosing_size == (2, 4, 8)


def test_real_fixture_blocks_and_tile_entities(store):
    blocks = [b for _, _, _, b in store.iter_blocks()]
    assert len(blocks) == 42

    names = store.block_counts()
    assert names["minecraft:spruce_planks"] > 0
    assert "minecraft:chest" in names

    tile_entities = store.regions[0].tile_entities
    assert len(tile_entities) == 18
    assert {t["id"] for t in tile_entities} == {
        "minecraft:chest",
        "minecraft:decorated_pot",
        "minecraft:shelf",
    }
    assert all({"x", "y", "z", "id"} <= set(t) for t in tile_entities)


def test_synthetic_tile_entities_roundtrip():
    doc = litematic.loads(
        make_litematic(
            [
                make_region(
                    "r",
                    position=(0, 0, 0),
                    size=(1, 1, 1),
                    palette=["minecraft:air", "minecraft:chest"],
                    grid_yzx=[1],
                    tile_entities=[
                        {
                            "id": (nbt.TAG_STRING, "minecraft:chest"),
                            "x": (nbt.TAG_INT, 0),
                            "y": (nbt.TAG_INT, 0),
                            "z": (nbt.TAG_INT, 0),
                            "Items": (nbt.TAG_LIST, (nbt.TAG_END, [])),
                        }
                    ],
                )
            ]
        )
    )
    entities = doc.regions[0].tile_entities
    assert len(entities) == 1
    assert entities[0]["id"] == "minecraft:chest"
    assert entities[0]["Items"] == []


def test_real_fixture_no_air_in_iter_blocks(store):
    assert all(not block.is_air for _, _, _, block in store.iter_blocks())
