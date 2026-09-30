"""Fixed-width field slicing, streaming line by line.

Fields are sliced by BYTE offsets (not characters): a fixed-width column is
positional, and each field is decoded separately so that multibyte codepages
do not break the alignment.
"""

from __future__ import annotations

from collections.abc import Iterator

from . import compression
from .schema import Schema


class FixedWidthReader:
    def __init__(self, schema: Schema, path: str):
        self.schema = schema
        self.path = path

    def records(self, skip_first: bool = False) -> Iterator[tuple[int, bytes]]:
        """Yield (line number, record bytes). Skips empty lines and, when
        requested, the first non-empty line (e.g. a delimited header row).

        The file is decompressed on the way in if it is a gzip or bzip2
        container, so a compressed transfer behaves exactly like a plain one.
        """
        with compression.open_binary(self.path) as fh:
            for lineno, record in compression.iter_lines(fh):
                if skip_first:
                    skip_first = False
                    continue
                yield lineno, record

    def parse_record(self, record: bytes) -> list[bytes]:
        result: list[bytes] = []
        for f in self.schema.fields:
            start = f.start
            length = f.length
            assert start is not None and length is not None  # fixed-width invariant
            result.append(record[start : start + length])
        return result
