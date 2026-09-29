"""Output writers: JSON, CSV, NDJSON, SQL, Parquet, Excel.

Text writers stream into a file-like object. Binary writers (Parquet,
Excel) take a path and manage their own file. Writers buffer only what
the target format requires (Parquet batches, the Excel workbook).
"""

from __future__ import annotations

import csv
import datetime
import decimal
import json
import re
from typing import Protocol, TextIO, cast

from .schema import Schema
from .validator import RecordResult


class Writer(Protocol):
    def write(self, result: RecordResult) -> None: ...
    def finish(self) -> None: ...


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


def _iso_to_date(value: str):
    """ISO `YYYY-MM-DD` -> `datetime.date`, or the value unchanged.

    A masked or otherwise non-ISO string stays a string rather than raising:
    redaction must not turn into a hard failure.
    """
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return value


def _sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (decimal.Decimal, int, float)):
        return str(value)
    return f"'{str(value).replace(chr(39), chr(39) * 2)}'"


class JsonWriter:
    def __init__(self, schema: Schema, out: TextIO):
        self._out = out
        self._first = True
        self._out.write("[")

    def write(self, result: RecordResult) -> None:
        row = {fv.name: _to_jsonable(fv.value) for fv in result.fields}
        if self._first:
            self._out.write("\n")
            self._first = False
        else:
            self._out.write(",\n")
        json.dump(row, self._out, ensure_ascii=False, indent=2)

    def finish(self) -> None:
        if self._first:
            self._out.write("]\n")
        else:
            self._out.write("\n]\n")


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
            [pa.field(f.name, t) for f, t in zip(schema.fields, types, strict=True)]
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
    """Write-only workbook: bounded memory for large exports.

    Cells carry native Excel types. A date arrives as `datetime.date` and a
    financial amount as a number, so the recipient can sort, sum and pivot
    without retyping the column. Writing them as text would look fine in a
    screenshot and break every formula in the workbook.

    `excel_float_decimals` is the one honest compromise: Excel has a single
    numeric type, so exact `decimal.Decimal` amounts are written as the
    closest float. Set it to 0 for a faithful representation at the cost of
    arithmetic. The Parquet writer is the one that keeps `decimal128` exact.
    """

    DEFAULT_FLOAT_DECIMALS = 2

    def __init__(
        self,
        schema: Schema,
        path: str,
        float_decimals: int | None = None,
    ):
        from openpyxl import Workbook

        self._path = path
        self._float_decimals = (
            float_decimals
            if float_decimals is not None
            else self.DEFAULT_FLOAT_DECIMALS
        )
        if self._float_decimals < 0:
            raise ValueError("excel_float_decimals must be >= 0")
        self._wb = Workbook(write_only=True)
        self._ws = self._wb.create_sheet()
        self._ws.append([f.name for f in schema.fields])
        self._field_types = {f.name: f.type for f in schema.fields}

    def _cell(self, name: str, value: object) -> object:
        if value is None:
            # a blank cell, not the string "None" or an empty string
            return None
        ftype = self._field_types.get(name)
        if ftype in ("decimal", "packed"):
            if isinstance(value, (decimal.Decimal, int, float)):
                return round(float(value), self._float_decimals)
            return value
        if ftype == "date" and isinstance(value, str):
            return _iso_to_date(value)
        return value

    def write(self, result: RecordResult) -> None:
        self._ws.append([self._cell(fv.name, fv.value) for fv in result.fields])

    def finish(self) -> None:
        self._wb.save(self._path)


class SingerWriter:
    """Singer tap output: SCHEMA / RECORD / STATE JSON lines.

    Deterministic by construction: no `time_extracted` timestamps, so the
    same input always produces the same stream. Compatible with Singer
    targets and Airbyte's CDK tap runners.

    The STATE message carries a real bookmark (the source line of the last
    record actually emitted), written every `state_every` records and again
    at `finish()`. A target can therefore resume from the last STATE it
    received instead of restarting a multi-hour extract.

    Resuming is byte-exact: `emit_schema=False` suppresses the SCHEMA
    message (the target already has it), so

        first 5000 lines  ++  resume from the STATE at line 5000
        ==  one uninterrupted run of the whole file

    `resume_line()` reads a STATE file back and returns the bookmark.
    """

    DEFAULT_STATE_EVERY = 1000

    def __init__(
        self,
        schema: Schema,
        out: TextIO,
        state_path: str | None = None,
        state_every: int | None = None,
        emit_schema: bool = True,
    ):
        self._out = out
        self._stream = schema.table or schema.format or "export"
        self._state_path = state_path
        self._state_every = state_every or self.DEFAULT_STATE_EVERY
        self._emitted = 0
        self._last_line = 0
        if emit_schema:
            self._write_schema(schema)

    def _write_schema(self, schema: Schema) -> None:
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
            json.dumps({"type": "RECORD", "stream": self._stream, "record": row}) + "\n"
        )
        self._emitted += 1
        self._last_line = result.line
        if self._state_every and self._emitted % self._state_every == 0:
            self._emit_state()

    def _state_value(self) -> dict:
        # Only the source line of the last emitted record. A cumulative
        # record counter would restart on resume and break byte-equality
        # between an interrupted run and a single uninterrupted one.
        return {
            "stream": self._stream,
            "line": self._last_line,
        }

    def _emit_state(self) -> None:
        message = {"type": "STATE", "value": self._state_value()}
        self._out.write(json.dumps(message) + "\n")
        if self._state_path:
            with open(self._state_path, "w", encoding="utf-8") as fh:
                json.dump(message, fh)
                fh.write("\n")

    def finish(self) -> None:
        self._emit_state()


def resume_line(state_path: str) -> int:
    """Read the last STATE bookmark from `state_path`; 0 when unusable.

    A missing, empty or malformed state file means "start from the top"
    rather than an error: a target that lost its state should still make
    progress instead of failing the run.
    """
    try:
        with open(state_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return 0
    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return 0
    line = value.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 0:
        return 0
    return line


def _singer_type(field) -> dict:
    if field.type == "decimal":
        return {"type": "number"}
    return {"type": "string"}


def make_writer(
    fmt: str,
    schema: Schema,
    out: TextIO | str,
    *,
    state_path: str | None = None,
    state_every: int | None = None,
    emit_schema: bool = True,
    float_decimals: int | None = None,
) -> Writer:
    if fmt == "json":
        return JsonWriter(schema, cast(TextIO, out))
    if fmt == "csv":
        return CsvWriter(schema, cast(TextIO, out))
    if fmt == "ndjson":
        return NdjsonWriter(schema, cast(TextIO, out))
    if fmt == "sql":
        return SqlWriter(schema, cast(TextIO, out))
    if fmt == "parquet":
        return ParquetWriter(schema, cast(str, out))
    if fmt == "excel":
        return ExcelWriter(schema, cast(str, out), float_decimals=float_decimals)
    if fmt == "singer":
        return SingerWriter(
            schema,
            cast(TextIO, out),
            state_path=state_path,
            state_every=state_every,
            emit_schema=emit_schema,
        )
    raise ValueError(f"unsupported output format: {fmt!r}")
