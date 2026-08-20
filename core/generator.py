"""Automatic schema inference from an example file (delimited formats).

Given a sample flat file, detect the delimiter, infer column names and
types, and emit a `delimited` YAML schema ready for erp-normalize.
"""

from __future__ import annotations

import datetime
import decimal
import os
import re

from .schema import DELIMITED_FORMAT

DELIMITERS = [",", "\t", ";", "|"]
DATE_8_RE = re.compile(r"^\d{8}$")


def _is_date(text: str) -> bool:
    if not DATE_8_RE.fullmatch(text):
        return False
    try:
        datetime.date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
        return True
    except ValueError:
        return False


def _is_decimal(text: str) -> bool:
    t = text.removesuffix("-")  # trailing mainframe-style sign
    try:
        decimal.Decimal(t)
        return True
    except (decimal.InvalidOperation, ValueError):
        return False


def detect_delimiter(lines: list[str]) -> str | None:
    """Pick the delimiter that splits every non-empty line into the same
    number of parts. Returns None when no consistent delimiter is found."""
    best: tuple[int, str] | None = None
    for d in DELIMITERS:
        counts = [ln.count(d) for ln in lines if ln.strip()]
        if not counts:
            continue
        if all(c == counts[0] for c in counts) and counts[0] >= 1:
            score = counts[0] * 100 + len(counts)
            if best is None or score > best[0]:
                best = (score, d)
    return best[1] if best else None


def _looks_like_header(cells: list[str]) -> bool:
    if len(cells) < 2:
        return False
    stripped = [c.strip() for c in cells]
    if not all(stripped):
        return False
    if any(_is_decimal(c) or _is_date(c) for c in stripped):
        return False
    return len({c.lower() for c in stripped}) == len(stripped)


def _infer_column(samples: list[str]) -> dict:
    nonempty = [s for s in samples if s.strip()]
    if not nonempty:
        return {"type": "string"}
    if all(_is_date(s) for s in nonempty):
        return {"type": "date", "format": "YYYYMMDD"}
    if all(_is_decimal(s) for s in nonempty):
        scales = []
        for s in nonempty:
            t = s.rstrip("-")
            scales.append(len(t.split(".")[1]) if "." in t else 0)
        return {"type": "decimal", "scale": max(scales)}
    return {"type": "string"}


def generate_schema(
    path: str,
    *,
    name: str | None = None,
    delimiter: str | None = None,
    codepage: str = "utf-8",
    has_header: bool | None = None,
    sample_lines: int = 50,
    version: str = "1.0.0",
) -> dict:
    """Infer a delimited schema dict from an example file."""
    with open(path, "rb") as fh:
        raw_lines = []
        for _ in range(sample_lines):
            raw = fh.readline().rstrip(b"\r\n")
            if not raw:
                break
            raw_lines.append(raw)
    lines = [ln.decode(codepage, errors="replace") for ln in raw_lines]
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        raise ValueError("input file is empty")

    if delimiter is None:
        delimiter = detect_delimiter(nonempty)
        if delimiter is None:
            raise ValueError(
                "no consistent delimiter found; fixed-width inference is not "
                "supported - provide a schema manually"
            )

    rows = [ln.split(delimiter) for ln in nonempty]
    ncols = max(len(r) for r in rows)

    if has_header is None:
        has_header = _looks_like_header(rows[0])

    data_rows = rows[1:] if has_header else rows
    columns = [[row[i] for row in data_rows] for i in range(ncols)]
    base_names = [c.strip() for c in rows[0]] if has_header else []
    names = [
        (base_names[i] if i < len(base_names) and base_names[i] else f"column_{i}")
        for i in range(ncols)
    ]

    fields = []
    for i in range(ncols):
        spec = _infer_column(columns[i])
        fields.append({"name": names[i], **spec})

    data = {
        "format": DELIMITED_FORMAT,
        "version": version,
        "description": f"Auto-generated from {os.path.basename(path)}",
        "codepage": codepage,
        "delimiter": delimiter,
        "has_header": has_header,
        "fields": fields,
    }
    if name:
        data["table"] = name
    return data
