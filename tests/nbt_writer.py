"""Escritor NBT mínimo usado sólo por los tests.

Permite construir archivos NBT sintéticos (y ``.litematic``) en memoria para probar
el lector sin depender de fixtures binarios.
"""

from __future__ import annotations

import struct

import numpy as np

from lite2obj import nbt


def payload(tag: int, value) -> bytes:
    if tag == nbt.TAG_BYTE:
        return struct.pack(">b", value)
    if tag == nbt.TAG_SHORT:
        return struct.pack(">h", value)
    if tag == nbt.TAG_INT:
        return struct.pack(">i", value)
    if tag == nbt.TAG_LONG:
        return struct.pack(">q", value)
    if tag == nbt.TAG_FLOAT:
        return struct.pack(">f", value)
    if tag == nbt.TAG_DOUBLE:
        return struct.pack(">d", value)
    if tag == nbt.TAG_BYTE_ARRAY:
        return struct.pack(">i", len(value)) + bytes((int(v) & 0xFF) for v in value)
    if tag == nbt.TAG_STRING:
        raw = value.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw
    if tag == nbt.TAG_LIST:
        item_tag, items = value
        return struct.pack(">bi", item_tag, len(items)) + b"".join(
            payload(item_tag, item) for item in items
        )
    if tag == nbt.TAG_COMPOUND:
        out = bytearray()
        for name, (child_tag, child_value) in value.items():
            out += struct.pack(">b", child_tag)
            out += payload(nbt.TAG_STRING, name)
            out += payload(child_tag, child_value)
        out += struct.pack(">b", nbt.TAG_END)
        return bytes(out)
    if tag == nbt.TAG_INT_ARRAY:
        return struct.pack(">i", len(value)) + b"".join(struct.pack(">i", int(v)) for v in value)
    if tag == nbt.TAG_LONG_ARRAY:
        return struct.pack(">i", len(value)) + b"".join(struct.pack(">q", int(v)) for v in value)
    raise AssertionError(f"tag no soportado en el escritor de tests: {tag}")


def write_nbt(root: dict, name: str = "") -> bytes:
    """Serializa un compuesto raíz a bytes NBT (sin comprimir)."""
    return (
        struct.pack(">b", nbt.TAG_COMPOUND)
        + payload(nbt.TAG_STRING, name)
        + payload(nbt.TAG_COMPOUND, root)
    )


def pack_bits(values, bits: int) -> list[int]:
    """Empaqueta ``values`` con el bit-packing contiguo de Litematica/Minecraft.

    Es el inverso exacto de :func:`lite2obj.litematic.unpack_bits` y se usa para
    generar fixtures verificables.
    """
    if bits <= 0:
        return []
    total_bits = len(values) * bits
    n_longs = (total_bits + 63) // 64
    words = np.zeros(n_longs, dtype=np.uint64)
    mask = (1 << bits) - 1
    for i, value in enumerate(values):
        value = int(value) & mask
        start = i * bits
        long_index, offset = divmod(start, 64)
        words[long_index] |= np.uint64((value << offset) & 0xFFFFFFFFFFFFFFFF)
        if offset + bits > 64:
            spilled = value >> (64 - offset)
            words[long_index + 1] |= np.uint64(spilled & 0xFFFFFFFFFFFFFFFF)
    # Convierte uint64 -> int64 con la misma representación de bits.
    return words.astype(np.int64).view(np.int64).tolist()
