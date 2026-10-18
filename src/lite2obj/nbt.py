"""Lector NBT (Named Binary Tag) mínimo y sin dependencias externas.

Sólo se implementa lectura, que es lo único que lite2obj necesita para consumir
archivos ``.litematic``. La compresión (gzip / zlib / sin comprimir) se detecta
automáticamente.

Los tags de tipo array se devuelven como :class:`numpy.ndarray` para que el
despaquetado de ``BlockStates`` (que puede tener millones de entradas) sea rápido:

============== =================
Tag NBT        Tipo Python
============== =================
byte           ``int``
short          ``int``
int            ``int``
long           ``int``
float          ``float``
double         ``float``
byte_array     ``numpy.int8``
string         ``str``
list           ``list``
compound       ``dict[str, Any]``
int_array      ``numpy.int32``
long_array     ``numpy.int64``
============== =================
"""

from __future__ import annotations

import gzip
import struct
import zlib
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

__all__ = [
    "NBTError",
    "TAG_END",
    "TAG_BYTE",
    "TAG_SHORT",
    "TAG_INT",
    "TAG_LONG",
    "TAG_FLOAT",
    "TAG_DOUBLE",
    "TAG_BYTE_ARRAY",
    "TAG_STRING",
    "TAG_LIST",
    "TAG_COMPOUND",
    "TAG_INT_ARRAY",
    "TAG_LONG_ARRAY",
    "TAG_NAMES",
    "decompress",
    "detect_compression",
    "load",
    "loads",
]

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TAG_NAMES: dict[int, str] = {
    TAG_END: "TAG_End",
    TAG_BYTE: "TAG_Byte",
    TAG_SHORT: "TAG_Short",
    TAG_INT: "TAG_Int",
    TAG_LONG: "TAG_Long",
    TAG_FLOAT: "TAG_Float",
    TAG_DOUBLE: "TAG_Double",
    TAG_BYTE_ARRAY: "TAG_Byte_Array",
    TAG_STRING: "TAG_String",
    TAG_LIST: "TAG_List",
    TAG_COMPOUND: "TAG_Compound",
    TAG_INT_ARRAY: "TAG_Int_Array",
    TAG_LONG_ARRAY: "TAG_Long_Array",
}

_SCALARS: dict[int, struct.Struct] = {
    TAG_BYTE: struct.Struct(">b"),
    TAG_SHORT: struct.Struct(">h"),
    TAG_INT: struct.Struct(">i"),
    TAG_LONG: struct.Struct(">q"),
    TAG_FLOAT: struct.Struct(">f"),
    TAG_DOUBLE: struct.Struct(">d"),
}

_NUMPY_DTYPES: dict[int, Any] = {
    TAG_BYTE_ARRAY: np.int8,
    TAG_INT_ARRAY: np.int32,
    TAG_LONG_ARRAY: np.int64,
}

# NBT guarda los arrays en big-endian; se leen con el dtype big-endian y se
# convierten a nativo.
_NUMPY_BE: dict[int, Any] = {
    TAG_BYTE_ARRAY: np.dtype(">i1"),
    TAG_INT_ARRAY: np.dtype(">i4"),
    TAG_LONG_ARRAY: np.dtype(">i8"),
}

_GZIP_MAGIC = b"\x1f\x8b"
_ZLIB_HEADER = 0x78  # el byte bajo del header zlib siempre lleva el método deflate (8)


class NBTError(ValueError):
    """Error de formato NBT."""


def detect_compression(data: bytes) -> str | None:
    """Devuelve ``"gzip"``, ``"zlib"`` o ``None`` para datos sin comprimir."""
    if data[:2] == _GZIP_MAGIC:
        return "gzip"
    if data[:1] and data[0] == _ZLIB_HEADER:
        return "zlib"
    return None


def decompress(data: bytes, compression: str | None = None) -> bytes:
    """Descomprime ``data`` detectando el formato si no se indica ``compression``."""
    if compression is None:
        compression = detect_compression(data)
    if compression == "gzip":
        return gzip.decompress(data)
    if compression == "zlib":
        return zlib.decompress(data)
    if compression in (None, "none", "raw"):
        return data
    raise NBTError(f"Compresión desconocida: {compression!r}")


class _Reader:
    """Cursor sobre un buffer de bytes con lecturas big-endian."""

    __slots__ = ("buf", "pos", "size")

    def __init__(self, buf: bytes | bytearray | memoryview) -> None:
        self.buf = buf
        self.pos = 0
        self.size = len(buf)

    def read(self, n: int) -> bytes:
        end = self.pos + n
        if end > self.size:
            raise NBTError(f"Fin de buffer inesperado en {self.pos} (pedían {n} bytes)")
        chunk = bytes(self.buf[self.pos : end])
        self.pos = end
        return chunk

    def unpack(self, tag: int) -> Any:
        st = _SCALARS[tag]
        end = self.pos + st.size
        if end > self.size:
            raise NBTError(f"Fin de buffer inesperado en {self.pos}")
        value = st.unpack_from(self.buf, self.pos)[0]
        self.pos = end
        return value

    def read_string(self) -> str:
        (length,) = struct.unpack_from(">H", self.buf, self.pos)
        self.pos += 2
        return self.read(length).decode("utf-8", errors="replace")

    def read_array(self, tag: int) -> np.ndarray:
        (length,) = struct.unpack_from(">i", self.buf, self.pos)
        self.pos += 4
        native = _NUMPY_DTYPES[tag]
        be = _NUMPY_BE[tag]
        if length < 0:
            raise NBTError(f"Longitud de array negativa: {length}")
        nbytes = length * be.itemsize
        end = self.pos + nbytes
        if end > self.size:
            raise NBTError("Fin de buffer inesperado leyendo array")
        view = np.frombuffer(self.buf, dtype=be, count=length, offset=self.pos)
        self.pos = end
        return view.astype(native, copy=False) if be.isnative else view.astype(native)

    def read_payload(self, tag: int, depth: int = 0) -> Any:
        if depth > 512:
            raise NBTError("Anidamiento NBT demasiado profundo (posible archivo corrupto)")
        if tag in _SCALARS:
            return self.unpack(tag)
        if tag == TAG_STRING:
            return self.read_string()
        if tag in _NUMPY_DTYPES:
            return self.read_array(tag)
        if tag == TAG_LIST:
            return self._read_list(depth)
        if tag == TAG_COMPOUND:
            return self._read_compound(depth)
        raise NBTError(f"Tag desconocido: {tag}")

    def _read_list(self, depth: int) -> list[Any]:
        (item_tag,) = struct.unpack_from(">b", self.buf, self.pos)
        (length,) = struct.unpack_from(">i", self.buf, self.pos + 1)
        self.pos += 5
        if length < 0:
            raise NBTError(f"Longitud de lista negativa: {length}")
        if item_tag == TAG_END:
            # Una lista vacía puede declarar TAG_End como tipo de elemento.
            return []
        return [self.read_payload(item_tag, depth + 1) for _ in range(length)]

    def _read_compound(self, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while True:
            (tag,) = struct.unpack_from(">b", self.buf, self.pos)
            self.pos += 1
            if tag == TAG_END:
                return result
            name = self.read_string()
            result[name] = self.read_payload(tag, depth + 1)


def loads(data: bytes, *, compression: str | None = None) -> Any:
    """Parsea ``data`` (bytes comprimidos o no) y devuelve el valor raíz."""
    raw = decompress(data, compression)
    reader = _Reader(raw)
    (tag,) = struct.unpack_from(">b", reader.buf, reader.pos)
    reader.pos += 1
    if tag == TAG_END:
        return None
    if tag != TAG_COMPOUND:
        raise NBTError(f"La raíz NBT debería ser TAG_Compound, es {TAG_NAMES.get(tag, tag)}")
    reader.read_string()  # nombre del compuesto raíz (normalmente "")
    return reader._read_compound(0)


def load(source: str | Path | BinaryIO | bytes, *, compression: str | None = None) -> Any:
    """Lee un archivo (o file-like / bytes) y devuelve el valor raíz NBT."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return loads(bytes(source), compression=compression)
    if hasattr(source, "read"):
        return loads(source.read(), compression=compression)  # type: ignore[union-attr]
    return loads(Path(source).read_bytes(), compression=compression)
