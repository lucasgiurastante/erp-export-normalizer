"""Output writers: JSON / CSV. Streaming: never buffers the whole file."""
from __future__ import annotations

import csv
import decimal
import json
from typing import TextIO

from .schema import Schema
from .validator import RecordResult

# Decimal to JSON: float (machine consumers). Full precision: CSV/SQL (phases 1+).
def _to_jsonable(value: object) -> object:
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


class JsonWriter:
    def __init__(self, schema: Schema, out: TextIO):
        self._rows: list[dict] = []
        self._out = out

    def write(self, result: RecordResult) -> None:
        self._rows.append({fv.name: _to_jsonable(fv.value) for fv in result.fields})

    def finish(self) -> None:
        json.dump(self._rows, self._out, ensure_ascii=False, indent=2)
        self._out.write("\n")


class CsvWriter:
    def __init__(self, schema: Schema, out: TextIO):
        self._writer = csv.DictWriter(
            out,
            fieldnames=[f.name for f in schema.fields],
            lineterminator="\n",
        )
        self._writer.writeheader()

    def write(self, result: RecordResult) -> None:
        self._writer.writerow({fv.name: _to_jsonable(fv.value) for fv in result.fields})

    def finish(self) -> None:
        pass


def make_writer(fmt: str, schema: Schema, out: TextIO):
    if fmt == "json":
        return JsonWriter(schema, out)
    if fmt == "csv":
        return CsvWriter(schema, out)
    raise ValueError(f"unsupported output format: {fmt!r}")