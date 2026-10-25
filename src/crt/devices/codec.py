"""Little-endian byte reader shared by the characteristic codecs."""

from __future__ import annotations

import struct


class CodecError(ValueError):
    """Raised when a characteristic value cannot be decoded or encoded."""


class Reader:
    """Sequential reader of little-endian fields, as used by all GATT characteristics."""

    def __init__(self, data: bytes | bytearray) -> None:
        self._data = bytes(data)
        self._pos = 0

    def _take(self, fmt: str) -> int:
        size = struct.calcsize(fmt)
        if self._pos + size > len(self._data):
            raise CodecError(
                f"value too short: need {self._pos + size} bytes, got {len(self._data)}"
                f" ({self._data.hex()})"
            )
        (value,) = struct.unpack_from(fmt, self._data, self._pos)
        self._pos += size
        return value

    def u8(self) -> int:
        return self._take("<B")

    def s16(self) -> int:
        return self._take("<h")

    def u16(self) -> int:
        return self._take("<H")

    def u24(self) -> int:
        low = self._take("<H")
        return low | (self._take("<B") << 16)

    def u32(self) -> int:
        return self._take("<I")

    def rest(self) -> bytes:
        tail = self._data[self._pos :]
        self._pos = len(self._data)
        return tail

    @property
    def remaining(self) -> int:
        return len(self._data) - self._pos
