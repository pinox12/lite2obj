"""Tests del lector NBT.

El escritor NBT (sólo para tests) vive en ``nbt_writer.py`` y permite verificar el
lector contra datos construidos a mano, incluyendo arrays big-endian y listas vacías.
"""

from __future__ import annotations

import gzip
import struct
import zlib

import numpy as np
import pytest
from nbt_writer import write_nbt

from lite2obj import nbt


def test_scalars_and_string():
    data = write_nbt(
        {
            "b": (nbt.TAG_BYTE, -5),
            "s": (nbt.TAG_SHORT, -300),
            "i": (nbt.TAG_INT, 70000),
            "l": (nbt.TAG_LONG, 9_000_000_000),
            "f": (nbt.TAG_FLOAT, 1.5),
            "d": (nbt.TAG_DOUBLE, -2.25),
            "str": (nbt.TAG_STRING, "hola mundo"),
        }
    )
    root = nbt.loads(data)
    assert root["b"] == -5
    assert root["s"] == -300
    assert root["i"] == 70000
    assert root["l"] == 9_000_000_000
    assert root["f"] == pytest.approx(1.5)
    assert root["d"] == pytest.approx(-2.25)
    assert root["str"] == "hola mundo"


def test_arrays_are_numpy_with_expected_values():
    root = nbt.loads(
        write_nbt(
            {
                "bytes": (nbt.TAG_BYTE_ARRAY, [-128, -1, 0, 1, 127]),
                "ints": (nbt.TAG_INT_ARRAY, [-1, 0, 2**31 - 1]),
                "longs": (nbt.TAG_LONG_ARRAY, [-1, 2**63 - 1]),
            }
        )
    )
    assert isinstance(root["bytes"], np.ndarray)
    assert root["bytes"].dtype == np.int8
    assert root["bytes"].tolist() == [-128, -1, 0, 1, 127]

    assert root["ints"].dtype == np.int32
    assert root["ints"].tolist() == [-1, 0, 2**31 - 1]

    assert root["longs"].dtype == np.int64
    assert root["longs"].tolist() == [-1, 2**63 - 1]


def test_nested_compounds_and_lists():
    root = nbt.loads(
        write_nbt(
            {
                "outer": (
                    nbt.TAG_COMPOUND,
                    {"inner": (nbt.TAG_INT, 42), "deep": (nbt.TAG_COMPOUND, {})},
                ),
                "list": (nbt.TAG_LIST, (nbt.TAG_STRING, ["a", "b", "c"])),
                "empty": (nbt.TAG_LIST, (nbt.TAG_END, [])),
            }
        )
    )
    assert root["outer"]["inner"] == 42
    assert root["outer"]["deep"] == {}
    assert root["list"] == ["a", "b", "c"]
    assert root["empty"] == []


def test_empty_file_returns_none():
    assert nbt.loads(struct.pack(">b", nbt.TAG_END)) is None


def test_root_must_be_compound():
    with pytest.raises(nbt.NBTError):
        nbt.loads(struct.pack(">b", nbt.TAG_INT))


def test_truncated_data_raises():
    data = write_nbt({"i": (nbt.TAG_INT, 7)})
    with pytest.raises(nbt.NBTError):
        nbt.loads(data[:-2])


def test_detect_and_decompress_gzip_and_zlib():
    raw = write_nbt({"i": (nbt.TAG_INT, 3)})

    assert nbt.detect_compression(gzip.compress(raw)) == "gzip"
    assert nbt.detect_compression(zlib.compress(raw)) == "zlib"
    assert nbt.detect_compression(raw) is None

    assert nbt.loads(gzip.compress(raw))["i"] == 3
    assert nbt.loads(zlib.compress(raw))["i"] == 3
    assert nbt.loads(raw)["i"] == 3
    assert nbt.load(raw)["i"] == 3


def test_load_from_path(tmp_path):
    path = tmp_path / "sample.nbt"
    path.write_bytes(gzip.compress(write_nbt({"i": (nbt.TAG_INT, 11)})))
    assert nbt.load(path)["i"] == 11
