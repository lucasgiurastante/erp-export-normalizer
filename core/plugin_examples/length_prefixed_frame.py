"""Length-prefixed frame reader (example plugin).

Binary record layout::

    +--------+------------------+
    | 2 bytes|   payload        |
    | length |   (length bytes) |
    +--------+------------------+

`length` is a big-endian unsigned short (2 bytes). The header is stripped and
only the payload is yielded, so the schema's field `start`/`length` offsets
are relative to the payload — not the whole record.

Schema usage (note: no `record_length` required)::

    format: framed
    version: 1.0.0
    parser: length_prefixed_frame
    codepage: cp1252
    fields:
      - {name: id,   start: 0,  length: 4}
      - {name: date, start: 4,  length: 8, type: date, format: YYYYMMDD}
      - {name: amt,  start: 12, length: 8, type: decimal, scale: 2, align: right}
"""

from __future__ import annotations

import struct
from collections.abc import Iterator


class Reader:
    def __init__(self, schema, path):
        self.schema = schema
        self.path = path

    def records(self, skip_first: bool = False) -> Iterator[tuple[int, bytes]]:
        with open(self.path, "rb") as fh:
            lineno = 0
            while True:
                header = fh.read(2)
                if not header:
                    break
                if len(header) < 2:
                    raise ValueError(
                        f"truncated length header at offset {fh.tell() - 2}"
                    )
                (length,) = struct.unpack(">H", header)
                payload = fh.read(length)
                if len(payload) < length:
                    raise ValueError(
                        f"truncated frame: expected {length} bytes, got {len(payload)}"
                    )
                lineno += 1
                if skip_first and lineno == 1:
                    continue
                yield lineno, payload
