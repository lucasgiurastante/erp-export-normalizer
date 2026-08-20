"""Field conversion: dates, decimals, codepages."""

from __future__ import annotations

import datetime
import decimal

from .schema import Field


class ConversionError(ValueError):
    """Field value not convertible per its type/format in the schema."""


CODEC_ALIASES = {
    "ebcdic-cp037": "cp037",
    "ebcdic": "cp037",
}


def codec_for(codepage: str) -> str:
    """Map schema codepage names to Python codec names (e.g. EBCDIC)."""
    return CODEC_ALIASES.get(codepage, codepage)


def decode_field(raw: bytes, field: Field, default_codepage: str) -> str:
    cp = codec_for(field.codepage or default_codepage)
    return raw.decode(cp, errors="strict")


def convert_field(raw: bytes, field: Field, default_codepage: str) -> object:
    return convert_text(decode_field(raw, field, default_codepage), field)


def convert_text(text: str, field: Field) -> object:
    if field.type == "string":
        return text.strip()
    if field.type == "date":
        return convert_date(text, field.format)
    if field.type == "decimal":
        return convert_decimal(text, field)
    return text


def convert_date(text: str, fmt: str | None) -> str:
    stripped = text.strip()
    if fmt == "YYYYMMDD":
        if len(stripped) != 8 or not stripped.isdigit():
            raise ConversionError(f"invalid date {text!r} (expected YYYYMMDD)")
        try:
            dt = datetime.date(
                int(stripped[0:4]), int(stripped[4:6]), int(stripped[6:8])
            )
        except ValueError as exc:
            raise ConversionError(f"invalid date {text!r}: {exc}") from exc
        return dt.isoformat()
    raise ConversionError(f"unsupported date format: {fmt!r}")


def convert_decimal(text: str, field: Field) -> decimal.Decimal:
    stripped = text.strip()
    if not stripped:
        raise ConversionError("empty decimal")
    negative = False
    if stripped.endswith("-"):  # trailing mainframe-style sign: "12345-"
        negative = True
        stripped = stripped[:-1]
    try:
        value = decimal.Decimal(stripped)
    except (decimal.InvalidOperation, ValueError) as exc:
        raise ConversionError(f"invalid decimal {text!r}") from exc
    if negative:
        value = -value
    # `scale` repositions implicit decimals (fixed-width integers like
    # "12345" with scale 2 -> 123.45). Explicit decimal points already carry
    # their precision and must not be shifted again (delimited formats).
    if field.scale and "." not in stripped:
        value = value.scaleb(-field.scale)
    return value
