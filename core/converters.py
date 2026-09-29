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
    if field.type == "packed":
        # packed decimal is binary (BCD), never decoded through a codepage
        return convert_packed(raw, field)
    return convert_text(decode_field(raw, field, default_codepage), field)


def convert_text(text: str, field: Field) -> object:
    if field.type == "string":
        return text.strip()
    if field.type == "date":
        return convert_date(text, field.format)
    if field.type == "decimal":
        return convert_decimal(text, field)
    return text


# COMP-3 (packed decimal) sign nibbles. The last nibble of the field carries
# the sign; 0xC/0xF are the common "positive" and 0xD the common "negative".
# 0xA/0xB are the rarer alternating-sign variants seen in some compilers.
PACKED_POSITIVE = frozenset({0xA, 0xC, 0xE, 0xF})
PACKED_NEGATIVE = frozenset({0xB, 0xD})


def convert_packed(raw: bytes, field: Field) -> decimal.Decimal:
    """Decode a COMP-3 (packed decimal) byte field into a Decimal.

    Layout: two BCD digits per byte, the final nibble being the sign, so an
    N-byte field holds 2N-1 digits. `scale` positions the implied decimal
    point (PIC S9(7)V99 COMP-3 -> scale 2, 5 bytes).
    """
    if not raw:
        raise ConversionError("empty packed decimal")
    nibbles: list[int] = []
    for byte in raw:
        nibbles.append(byte >> 4)
        nibbles.append(byte & 0x0F)
    sign = nibbles.pop()
    if sign in PACKED_NEGATIVE:
        negative = True
    elif sign in PACKED_POSITIVE:
        negative = False
    else:
        raise ConversionError(f"invalid packed decimal sign nibble 0x{sign:X}")
    digits: list[str] = []
    for nib in nibbles:
        if nib > 9:
            raise ConversionError(f"invalid packed decimal digit nibble 0x{nib:X}")
        digits.append(str(nib))
    text = "".join(digits)
    if not text:
        raise ConversionError("packed decimal has no digits")
    value = decimal.Decimal(text)
    if field.scale:
        value = value.scaleb(-field.scale)
    return -value if negative else value


DATE_FORMATS = {
    "YYYYMMDD": "%Y%m%d",
    "YYYY-MM-DD": "%Y-%m-%d",
    "DDMMYYYY": "%d%m%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "YYMMDD": "%y%m%d",
}


def convert_date(text: str, fmt: str | None) -> str:
    stripped = text.strip()
    pattern = DATE_FORMATS.get(fmt or "")
    if pattern is None:
        raise ConversionError(f"unsupported date format: {fmt!r}")
    try:
        dt = datetime.datetime.strptime(stripped, pattern).date()
    except ValueError as exc:
        raise ConversionError(f"invalid date {text!r} (expected {fmt})") from exc
    return dt.isoformat()


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
