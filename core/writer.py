"""Output writers: JSON, CSV, NDJSON, SQL, Parquet, Excel.

Text writers stream into a file-like object. Binary writers (Parquet,
Excel) take a path and manage their own file. Writers buffer only what
the target format requires (Parquet batches, the Excel workbook).
"""
from __future__ import annotations

import csv
import decimal
import json
import re
from typing import TextIO

from .schema import Schema
from .validator import RecordResult

PARQUET_BATCH_SIZE = 1000
SQL_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _to_jsonable(value: object) -> object:
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


def _sql_quote_ident(name: str) -> str:
    if not SQL_IDENT_RE.fullmatch(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return f'"{name}"'


def _sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (decimal.Decimal, int, float)):
        return str(value)
    return f"'{str(value).replace(chr(39), chr(39) * 2)}'"


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


class NdjsonWriter:
    def __init__(self, schema: Schema, out: TextIO):
        self._out = out

    def write(self, result: RecordResult) -> None:
        row = {fv.name: _to_jsonable(fv.value) for fv in result.fields}
        self._out.write(json.dumps(row, ensure_ascii=False) + "\n")

    def finish(self) -> None:
        pass


class SqlWriter:
    def __init__(self, schema: Schema, out: TextIO):
        self._out = out
        table = schema.table or "export"
        self._table = _sql_quote_ident(table)
        self._columns = ", ".join(_sql_quote_ident(f.name) for f in schema.fields)

    def write(self, result: RecordResult) -> None:
        values = ", ".join(_sql_value(fv.value) for fv in result.fields)
        self._out.write(
            f"INSERT INTO {self._table} ({self._columns}) VALUES ({values});\n"
        )

    def finish(self) -> None:
        pass


class ParquetWriter:
    """Streams row batches to Parquet; exact decimal128 for financial values."""

    def __init__(self, schema: Schema, path: str):
        import pyarrow as pa
        import pyarrow.parquet as pq

        self._pa = pa
        types = [
            pa.string() if f.type in ("string", "date") else pa.decimal128(38, f.scale)
            for f in schema.fields
        ]
        arrow_schema = pa.schema(
            [pa.field(f.name, t) for f, t in zip(schema.fields, types)]
        )
        self._schema = arrow_schema
        self._writer = pq.ParquetWriter(path, arrow_schema)
        self._buffer: list[dict] = []

    def write(self, result: RecordResult) -> None:
        self._buffer.append({fv.name: fv.value for fv in result.fields})
        if len(self._buffer) >= PARQUET_BATCH_SIZE:
            self._flush()

    def _flush(self) -> None:
        names = list(self._buffer[0])
        arrays = [self._pa.array([row[n] for row in self._buffer]) for n in names]
        table = self._pa.Table.from_arrays(arrays, schema=self._schema)
        self._writer.write_table(table)
        self._buffer = []

    def finish(self) -> None:
        if self._buffer:
            self._flush()
        self._writer.close()


class ExcelWriter:
    """Write-only workbook: bounded memory for large exports."""

    def __init__(self, schema: Schema, path: str):
        from openpyxl import Workbook

        self._path = path
        self._wb = Workbook(write_only=True)
        self._ws = self._wb.create_sheet()
        self._ws.append([f.name for f in schema.fields])

    def write(self, result: RecordResult) -> None:
        self._ws.append([fv.value for fv in result.fields])

    def finish(self) -> None:
        self._wb.save(self._path)


class SingerWriter:
    """Singer tap output: SCHEMA / RECORD / STATE JSON lines.

    Deterministic by construction: no `time_extracted` timestamps, so the
    same input always produces the same stream. Compatible with Singer
    targets and Airbyte's CDK tap runners.
    """

    def __init__(self, schema: Schema, out: TextIO):
        self._out = out
        self._stream = schema.table or schema.format or "export"
        properties = {f.name: _singer_type(f) for f in schema.fields}
        message = {
            "type": "SCHEMA",
            "stream": self._stream,
            "schema": {"type": "object", "properties": properties},
            "key_properties": [],
        }
        self._out.write(json.dumps(message) + "\n")

    def write(self, result: RecordResult) -> None:
        row = {fv.name: _to_jsonable(fv.value) for fv in result.fields}
        self._out.write(
            json.dumps({"type": "RECORD", "stream": self._stream, "record": row})
            + "\n"
        )

    def finish(self) -> None:
        self._out.write(json.dumps({"type": "STATE", "value": {}}) + "\n")


def _singer_type(field) -> dict:
    if field.type == "decimal":
        return {"type": "number"}
    return {"type": "string"}


def make_writer(fmt: str, schema: Schema, out):
    if fmt == "json":
        return JsonWriter(schema, out)
    if fmt == "csv":
        return CsvWriter(schema, out)
    if fmt == "ndjson":
        return NdjsonWriter(schema, out)
    if fmt == "sql":
        return SqlWriter(schema, out)
    if fmt == "parquet":
        return ParquetWriter(schema, out)
    if fmt == "excel":
        return ExcelWriter(schema, out)
    if fmt == "singer":
        return SingerWriter(schema, out)
    raise ValueError(f"unsupported output format: {fmt!r}")