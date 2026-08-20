"""Schema loading and validation.

A schema describes a flat file. The parser is generic:
the knowledge lives in the YAML, not in the code.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any

import yaml

SUPPORTED_TYPES = {"string", "date", "decimal"}
SUPPORTED_ALIGNS = {"left", "right"}
SUPPORTED_CODEPAGES = {"utf-8", "cp850", "cp1252", "latin-1", "ebcdic-cp037"}
TABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class SchemaError(ValueError):
    """Invalid schema: cannot be processed."""


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
    table: str | None = None
    source_path: str | None = None


def load_schema(path: str) -> Schema:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: schema root must be a mapping")
    return build_schema(data, source_path=path)


def build_schema(data: dict[str, Any], source_path: str | None = None) -> Schema:
    errors: list[str] = []

    fmt = data.get("format")
    record_length = data.get("record_length")
    codepage = data.get("codepage", "utf-8")
    version = data.get("version")
    description = data.get("description")
    table = data.get("table")
    fields_raw = data.get("fields")

    if fmt is None:
        errors.append("missing required field 'format'")
    if record_length is None:
        errors.append("missing required field 'record_length'")
    if version is None:
        errors.append("missing required field 'version'")

    if table is not None and not TABLE_NAME_RE.fullmatch(str(table)):
        errors.append(f"invalid SQL table name: {table!r}")

    if not isinstance(record_length, int) or record_length <= 0:
        errors.append(f"record_length must be int > 0 (got {record_length!r})")
    if codepage not in SUPPORTED_CODEPAGES:
        errors.append(f"unsupported codepage: {codepage!r}")
    if not isinstance(fields_raw, list) or not fields_raw:
        errors.append("fields must be a non-empty list")

    fields: list[Field] = []
    seen: set[str] = set()
    for i, raw in enumerate(fields_raw if isinstance(fields_raw, list) else []):
        if not isinstance(raw, dict):
            errors.append(f"fields[{i}]: must be a mapping")
            continue
        name = raw.get("name")
        start = raw.get("start")
        length = raw.get("length")
        if not isinstance(name, str) or not name:
            errors.append(f"fields[{i}]: missing 'name'")
            name = f"field_{i}"
        if not isinstance(start, int) or start < 0:
            errors.append(f"fields[{i}] '{name}': start must be int >= 0")
            start = -1
        if not isinstance(length, int) or length <= 0:
            errors.append(f"fields[{i}] '{name}': length must be int > 0")
            length = 0
        if name in seen:
            errors.append(f"fields[{i}] '{name}': duplicate name")
        seen.add(name)

        ftype = raw.get("type", "string")
        if ftype not in SUPPORTED_TYPES:
            errors.append(f"fields[{i}] '{name}': unsupported type {ftype!r}")
        align = raw.get("align", "left")
        if align not in SUPPORTED_ALIGNS:
            errors.append(f"fields[{i}] '{name}': unsupported align {align!r}")
        scale = raw.get("scale", 0)
        if not isinstance(scale, int) or scale < 0:
            errors.append(f"fields[{i}] '{name}': scale must be int >= 0")
        if ftype == "date" and raw.get("format") is None:
            errors.append(f"fields[{i}] '{name}': type date requires 'format'")
        if start >= 0 and length > 0 and start + length > record_length:
            errors.append(
                f"fields[{i}] '{name}': overflows record_length "
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
                errors.append(f"fields '{a.name}' and '{b.name}' overlap")

    if errors:
        raise SchemaError("; ".join(errors))

    return Schema(
        format=fmt,
        record_length=record_length,
        codepage=codepage,
        version=version,
        fields=tuple(fields),
        description=description,
        table=table,
        source_path=source_path,
    )