"""Schema loading and validation.

A schema describes a flat file. The parser is generic:
the knowledge lives in the YAML, not in the code.

Formats:
- fixed-width (any `format` other than "delimited"): fields are sliced by
  byte offset; `record_length` is required.
- delimited (`format: delimited`): fields are split on `delimiter`;
  `record_length` is not used.
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
DELIMITED_FORMAT = "delimited"


class SchemaError(ValueError):
    """Invalid schema: cannot be processed."""


@dataclasses.dataclass(frozen=True)
class Field:
    name: str
    start: int | None = None
    length: int | None = None
    type: str = "string"
    format: str | None = None
    scale: int = 0
    align: str = "left"
    codepage: str | None = None


@dataclasses.dataclass(frozen=True)
class Schema:
    format: str
    version: str
    fields: tuple[Field, ...]
    record_length: int | None = None
    codepage: str = "utf-8"
    delimiter: str | None = None
    has_header: bool | None = None
    description: str | None = None
    table: str | None = None
    rules: tuple[dict, ...] | None = None
    source_path: str | None = None


def load_schema(path: str) -> Schema:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: schema root must be a mapping")
    return build_schema(data, source_path=path)


def _validate_rules(rules_raw: Any, errors: list[str]) -> tuple[dict, ...] | None:
    if rules_raw is None:
        return None
    if not isinstance(rules_raw, list):
        errors.append("rules must be a list")
        return None
    out: list[dict] = []
    for i, r in enumerate(rules_raw):
        if not isinstance(r, dict):
            errors.append(f"rules[{i}]: must be a mapping")
            continue
        rtype = r.get("type")
        if rtype == "sum":
            for key in ("field", "expected"):
                if key not in r:
                    errors.append(f"rules[{i}]: 'sum' rule requires '{key}'")
        elif rtype == "balance":
            for key in ("positive", "negative"):
                if key not in r:
                    errors.append(f"rules[{i}]: 'balance' rule requires '{key}'")
        else:
            errors.append(f"rules[{i}]: unsupported rule type {rtype!r}")
        out.append(r)
    return tuple(out)


def build_schema(data: dict[str, Any], source_path: str | None = None) -> Schema:
    errors: list[str] = []

    fmt = data.get("format")
    version = data.get("version")
    codepage = data.get("codepage", "utf-8")
    description = data.get("description")
    table = data.get("table")
    fields_raw = data.get("fields")
    rules = _validate_rules(data.get("rules"), errors)
    is_delimited = fmt == DELIMITED_FORMAT

    if fmt is None:
        errors.append("missing required field 'format'")
    if version is None:
        errors.append("missing required field 'version'")

    if is_delimited:
        delimiter = data.get("delimiter", ",")
        if not isinstance(delimiter, str) or delimiter == "":
            errors.append("delimiter must be a non-empty string")
        has_header = data.get("has_header", False)
        if not isinstance(has_header, bool):
            errors.append("has_header must be a boolean")
        record_length = None
        if data.get("record_length") is not None:
            errors.append("record_length is not allowed for delimited format")
    else:
        delimiter = None
        has_header = None
        record_length = data.get("record_length")
        if record_length is None:
            errors.append("missing required field 'record_length'")
        elif not isinstance(record_length, int) or record_length <= 0:
            errors.append(f"record_length must be int > 0 (got {record_length!r})")

    if codepage not in SUPPORTED_CODEPAGES:
        errors.append(f"unsupported codepage: {codepage!r}")
    if not isinstance(fields_raw, list) or not fields_raw:
        errors.append("fields must be a non-empty list")
    if table is not None and not TABLE_NAME_RE.fullmatch(str(table)):
        errors.append(f"invalid SQL table name: {table!r}")

    fields: list[Field] = []
    seen: set[str] = set()
    for i, raw in enumerate(fields_raw if isinstance(fields_raw, list) else []):
        if not isinstance(raw, dict):
            errors.append(f"fields[{i}]: must be a mapping")
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"fields[{i}]: missing 'name'")
            name = f"field_{i}"
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

        if is_delimited:
            if "start" in raw or "length" in raw:
                errors.append(
                    f"fields[{i}] '{name}': start/length not allowed "
                    "for delimited format"
                )
            start = length = None
        else:
            start = raw.get("start")
            length = raw.get("length")
            if not isinstance(start, int) or start < 0:
                errors.append(f"fields[{i}] '{name}': start must be int >= 0")
                start = -1
            if not isinstance(length, int) or length <= 0:
                errors.append(f"fields[{i}] '{name}': length must be int > 0")
                length = 0
            if (
                start >= 0
                and length > 0
                and record_length is not None
                and start + length > record_length
            ):
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

    if not is_delimited:
        for a_i, a in enumerate(fields):
            if a.start is None or a.length is None or a.start < 0 or a.length <= 0:
                continue
            for b in fields[a_i + 1 :]:
                if b.start is None or b.length is None or b.start < 0 or b.length <= 0:
                    continue
                if a.start < b.start + b.length and b.start < a.start + a.length:
                    errors.append(f"fields '{a.name}' and '{b.name}' overlap")

    if errors:
        raise SchemaError("; ".join(errors))

    return Schema(
        format=fmt or "fixed-width",
        version=version or "1.0.0",
        fields=tuple(fields),
        record_length=record_length,
        codepage=codepage,
        delimiter=delimiter,
        has_header=has_header,
        description=description,
        table=table,
        rules=rules,
        source_path=source_path,
    )
