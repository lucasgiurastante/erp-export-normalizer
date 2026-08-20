"""Carga y validación de schema YAML.

Un schema describe el archivo plano. El parser es genérico:
el conocimiento vive en el YAML, no en el código.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import yaml

SUPPORTED_TYPES = {"string", "date", "decimal"}
SUPPORTED_ALIGNS = {"left", "right"}
SUPPORTED_CODEPAGES = {"utf-8", "cp850", "cp1252", "latin-1", "ebcdic-cp037"}


class SchemaError(ValueError):
    """Schema inválido: no se puede procesar."""


@dataclasses.dataclass(frozen=True)
class Field:
    name: str
    start: int
    length: int
    type: str = "string"
    format: str | None = None
    scale: int = 0
    align: str = "left"
    codepage: str | None = None


@dataclasses.dataclass(frozen=True)
class Schema:
    format: str
    record_length: int
    codepage: str
    version: str
    fields: tuple[Field, ...]
    description: str | None = None
    source_path: str | None = None


def load_schema(path: str) -> Schema:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: raíz del schema debe ser un mapping")
    return build_schema(data, source_path=path)


def build_schema(data: dict[str, Any], source_path: str | None = None) -> Schema:
    errors: list[str] = []

    fmt = data.get("format")
    record_length = data.get("record_length")
    codepage = data.get("codepage", "utf-8")
    version = data.get("version")
    description = data.get("description")
    fields_raw = data.get("fields")

    if fmt is None:
        errors.append("falta campo requerido 'format'")
    if record_length is None:
        errors.append("falta campo requerido 'record_length'")
    if version is None:
        errors.append("falta campo requerido 'version'")

    if not isinstance(record_length, int) or record_length <= 0:
        errors.append(f"record_length debe ser int > 0 (got {record_length!r})")
    if codepage not in SUPPORTED_CODEPAGES:
        errors.append(f"codepage no soportado: {codepage!r}")
    if not isinstance(fields_raw, list) or not fields_raw:
        errors.append("fields debe ser lista no vacía")

    fields: list[Field] = []
    seen: set[str] = set()
    for i, raw in enumerate(fields_raw if isinstance(fields_raw, list) else []):
        if not isinstance(raw, dict):
            errors.append(f"fields[{i}]: debe ser mapping")
            continue
        name = raw.get("name")
        start = raw.get("start")
        length = raw.get("length")
        if not isinstance(name, str) or not name:
            errors.append(f"fields[{i}]: falta 'name'")
            name = f"field_{i}"
        if not isinstance(start, int) or start < 0:
            errors.append(f"fields[{i}] '{name}': start debe ser int >= 0")
            start = -1
        if not isinstance(length, int) or length <= 0:
            errors.append(f"fields[{i}] '{name}': length debe ser int > 0")
            length = 0
        if name in seen:
            errors.append(f"fields[{i}] '{name}': nombre duplicado")
        seen.add(name)

        ftype = raw.get("type", "string")
        if ftype not in SUPPORTED_TYPES:
            errors.append(f"fields[{i}] '{name}': type no soportado {ftype!r}")
        align = raw.get("align", "left")
        if align not in SUPPORTED_ALIGNS:
            errors.append(f"fields[{i}] '{name}': align no soportado {align!r}")
        scale = raw.get("scale", 0)
        if not isinstance(scale, int) or scale < 0:
            errors.append(f"fields[{i}] '{name}': scale debe ser int >= 0")
        if ftype == "date" and raw.get("format") is None:
            errors.append(f"fields[{i}] '{name}': type date requiere 'format'")
        if start >= 0 and length > 0 and start + length > record_length:
            errors.append(
                f"fields[{i}] '{name}': desborda record_length "
                f"({start}+{length}>{record_length})"
            )

        fields.append(
            Field(
                name=name,
                start=start,
                length=length,
                type=ftype,
                format=raw.get("format"),
                scale=scale,
                align=align,
                codepage=raw.get("codepage"),
            )
        )

    for a_i, a in enumerate(fields):
        if a.start < 0 or a.length <= 0:
            continue
        for b in fields[a_i + 1:]:
            if b.start < 0 or b.length <= 0:
                continue
            if a.start < b.start + b.length and b.start < a.start + a.length:
                errors.append(f"overlap entre campos '{a.name}' y '{b.name}'")

    if errors:
        raise SchemaError("; ".join(errors))

    return Schema(
        format=fmt,
        record_length=record_length,
        codepage=codepage,
        version=version,
        fields=tuple(fields),
        description=description,
        source_path=source_path,
    )