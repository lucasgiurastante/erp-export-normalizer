"""Fixed-width field slicing, streaming line by line.

Fields are sliced by BYTE offsets (not characters): a fixed-width column is
positional, and each field is decoded separately so that multibyte codepages
do not break the alignment.
"""
from __future__ import annotations

from collections.abc import Iterator

from .schema import Schema


class FixedWidthReader:
    def __init__(self, schema: Schema, path: str):
        self.schema = schema
        self.path = path

    def records(self) -> Iterator[tuple[int, bytes]]:
        """Yield (line number, record bytes). Skips empty lines."""
        with open(self.path, "rb") as fh:
            for lineno, raw in enumerate(fh, start=1):
                record = raw.rstrip(b"\r\n")
                if not record:
                    continue
                yield lineno, record

    def parse_record(self, record: bytes) -> list[bytes]:
        return [record[f.start:f.start + f.length] for f in self.schema.fields]