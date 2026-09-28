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


MAX_RAW_DISPLAY = 50


def _truncate_raw(raw: str, limit: int = MAX_RAW_DISPLAY) -> str:
    """Truncate raw value for error display (single-line, max `limit` chars)."""
    cleaned = raw.replace("\r", " ").replace("\n", " ")
    return cleaned[:limit]


def _field_error(field_name: str, message: str, raw: str) -> str:
    """Format field error with raw value (truncated) for UX (P1-3)."""
    return f"field '{field_name}': {message} | raw='{_truncate_raw(raw)}'"


@dataclasses.dataclass
class RecordResult:
    line: int
    ok: bool
    errors: list[str]
    fields: list[FieldValue]
    details: list[dict] = dataclasses.field(default_factory=list)


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
            self.error_lines.append(
                {
                    "line": result.line,
                    "errors": result.errors,
                    "details": list(getattr(result, "details", [])),
                }
            )

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
        if self.schema.parser is not None or self.schema.record_length is not None:
            return self._validate_fixed(line, record)
        return self._validate_delimited(line, record)

    def _validate_fixed(self, line: int, record: bytes) -> RecordResult:
        errors: list[str] = []
        fields: list[FieldValue] = []
        details: list[dict] = []
        length_ok = (
            self.schema.record_length is None
            or len(record) == self.schema.record_length
        )
        if not length_ok:
            msg = (
                f"length {len(record)} != "
                f"record_length {self.schema.record_length}"
            )
            errors.append(msg)
            line_raw = record.decode(
                converters.codec_for(self.schema.codepage), errors="replace"
            )
            details.append(
                {"field": None, "message": msg, "raw": _truncate_raw(line_raw)}
            )
        for f in self.schema.fields:
            start = f.start
            length = f.length
            assert start is not None and length is not None  # fixed-width invariant
            raw = record[start : start + length]
            if len(raw) < length:
                msg = "out of range (short line)"
                errors.append(_field_error(f.name, msg, ""))
                details.append({"field": f.name, "message": msg, "raw": ""})
                fields.append(FieldValue(name=f.name, value=None, raw=""))
                continue
            raw_text = raw.decode(
                converters.codec_for(self.schema.codepage), errors="replace"
            )
            try:
                value = converters.convert_field(raw, f, self.schema.codepage)
            except converters.ConversionError as exc:
                msg = str(exc)
                errors.append(_field_error(f.name, msg, raw_text))
                details.append(
                    {
                        "field": f.name,
                        "message": msg,
                        "raw": _truncate_raw(raw_text),
                    }
                )
                value = None
            fields.append(FieldValue(name=f.name, value=value, raw=raw_text))
        return RecordResult(
            line=line, ok=not errors, errors=errors, fields=fields, details=details
        )

    def _validate_delimited(self, line: int, record: bytes) -> RecordResult:
        errors: list[str] = []
        fields: list[FieldValue] = []
        details: list[dict] = []
        text = record.decode(
            converters.codec_for(self.schema.codepage), errors="replace"
        )
        parts = text.split(self.schema.delimiter)
        for i, f in enumerate(self.schema.fields):
            if i >= len(parts):
                msg = f"missing column ({len(parts)} columns in line)"
                errors.append(_field_error(f.name, msg, ""))
                details.append({"field": f.name, "message": msg, "raw": ""})
                fields.append(FieldValue(name=f.name, value=None, raw=""))
                continue
            raw_text = parts[i].strip()
            try:
                value = converters.convert_text(raw_text, f)
            except converters.ConversionError as exc:
                msg = str(exc)
                errors.append(_field_error(f.name, msg, raw_text))
                details.append(
                    {
                        "field": f.name,
                        "message": msg,
                        "raw": _truncate_raw(raw_text),
                    }
                )
                value = None
            fields.append(FieldValue(name=f.name, value=value, raw=raw_text))
        if len(parts) > len(self.schema.fields):
            msg = (
                f"{len(parts)} columns in line, "
                f"schema expects {len(self.schema.fields)}"
            )
            errors.append(msg)
            details.append(
                {"field": None, "message": msg, "raw": _truncate_raw(text)}
            )
        return RecordResult(
            line=line, ok=not errors, errors=errors, fields=fields, details=details
        )
