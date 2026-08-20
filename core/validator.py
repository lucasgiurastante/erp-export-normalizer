"""Cumulative error collection (does not stop at the first) + line-numbered report."""

from __future__ import annotations

import dataclasses

from . import converters
from .schema import Schema


@dataclasses.dataclass(frozen=True)
class FieldValue:
    name: str
    value: object
    raw: str


@dataclasses.dataclass
class RecordResult:
    line: int
    ok: bool
    errors: list[str]
    fields: list[FieldValue]


@dataclasses.dataclass
class Stats:
    total: int = 0
    ok: int = 0
    errors: int = 0
    error_lines: list[dict] = dataclasses.field(default_factory=list)

    def add(self, result: RecordResult) -> None:
        self.total += 1
        if result.ok:
            self.ok += 1
        else:
            self.errors += 1
            self.error_lines.append({"line": result.line, "errors": result.errors})

    def report(self) -> dict:
        return {
            "total": self.total,
            "ok": self.ok,
            "errors": self.errors,
            "error_lines": self.error_lines,
        }


class Validator:
    def __init__(self, schema: Schema):
        self.schema = schema

    def validate_record(self, line: int, record: bytes) -> RecordResult:
        if self.schema.record_length is not None:
            return self._validate_fixed(line, record)
        return self._validate_delimited(line, record)

    def _validate_fixed(self, line: int, record: bytes) -> RecordResult:
        errors: list[str] = []
        fields: list[FieldValue] = []
        length_ok = len(record) == self.schema.record_length
        if not length_ok:
            errors.append(
                f"length {len(record)} != record_length {self.schema.record_length}"
            )
        for f in self.schema.fields:
            start = f.start
            length = f.length
            assert start is not None and length is not None  # fixed-width invariant
            raw = record[start : start + length]
            if len(raw) < length:
                errors.append(f"field '{f.name}': out of range (short line)")
                fields.append(FieldValue(name=f.name, value=None, raw=""))
                continue
            raw_text = raw.decode(
                converters.codec_for(self.schema.codepage), errors="replace"
            )
            try:
                value = converters.convert_field(raw, f, self.schema.codepage)
            except converters.ConversionError as exc:
                errors.append(f"field '{f.name}': {exc}")
                value = None
            fields.append(FieldValue(name=f.name, value=value, raw=raw_text))
        return RecordResult(line=line, ok=not errors, errors=errors, fields=fields)

    def _validate_delimited(self, line: int, record: bytes) -> RecordResult:
        errors: list[str] = []
        fields: list[FieldValue] = []
        text = record.decode(
            converters.codec_for(self.schema.codepage), errors="replace"
        )
        parts = text.split(self.schema.delimiter)
        for i, f in enumerate(self.schema.fields):
            if i >= len(parts):
                errors.append(
                    f"field '{f.name}': missing column ({len(parts)} columns in line)"
                )
                fields.append(FieldValue(name=f.name, value=None, raw=""))
                continue
            raw_text = parts[i].strip()
            try:
                value = converters.convert_text(raw_text, f)
            except converters.ConversionError as exc:
                errors.append(f"field '{f.name}': {exc}")
                value = None
            fields.append(FieldValue(name=f.name, value=value, raw=raw_text))
        if len(parts) > len(self.schema.fields):
            errors.append(
                f"{len(parts)} columns in line, "
                f"schema expects {len(self.schema.fields)}"
            )
        return RecordResult(line=line, ok=not errors, errors=errors, fields=fields)
