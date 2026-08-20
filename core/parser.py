"""Corte de campos fixed-width, streaming línea a línea.

Los campos se cortan por offset de BYTES (no caracteres): la columna
fixed-width es posicional; cada campo se decodifica por separado para que
codepages multibyte no rompan la alineación.
"""
from __future__ import annotations

from collections.abc import Iterator

from .schema import Schema


class FixedWidthReader:
    def __init__(self, schema: Schema, path: str):
        self.schema = schema
        self.path = path

    def records(self) -> Iterator[tuple[int, bytes]]:
        """Yields (nº de línea, bytes del registro). Salta líneas vacías."""
        with open(self.path, "rb") as fh:
            for lineno, raw in enumerate(fh, start=1):
                record = raw.rstrip(b"\r\n")
                if not record:
                    continue
                yield lineno, record

    def parse_record(self, record: bytes) -> list[bytes]:
        return [record[f.start:f.start + f.length] for f in self.schema.fields]