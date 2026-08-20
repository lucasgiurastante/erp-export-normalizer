"""Errores acumulativos (no corta en el primero) + reporte con nº de línea."""
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
        errors: list[str] = []
        fields: list[FieldValue] = []
        length_ok = len(record) == self.schema.record_length
        if not length_ok:
            errors.append(
                f"longitud {len(record)} != record_length {self.schema.record_length}"
            )
        for f in self.schema.fields:
            raw = record[f.start:f.start + f.length]
            if len(raw) < f.length:
                errors.append(f"campo '{f.name}': fuera de rango (línea corta)")
                fields.append(FieldValue(name=f.name, value=None, raw=""))
                continue
            raw_text = raw.decode(self.schema.codepage, errors="replace")
            try:
                value = converters.convert_field(raw, f, self.schema.codepage)
            except converters.ConversionError as exc:
                errors.append(f"campo '{f.name}': {exc}")
                value = None
            fields.append(FieldValue(name=f.name, value=value, raw=raw_text))
        return RecordResult(line=line, ok=not errors, errors=errors, fields=fields)