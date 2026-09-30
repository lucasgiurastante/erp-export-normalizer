"""Field conversion: dates, decimals, codepages."""

from __future__ import annotations

import datetime
import decimal
import hashlib
import unicodedata
from typing import Literal, cast

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
    """Decode one field, turning an undecodable byte into a field error.

    Strict decoding is right, but a bare `UnicodeDecodeError` escaping to
    the caller would kill a whole run over a 44-byte record that has one bad
    byte in it. A bad byte is a *field* problem, and the report exists to
    say which field and where.
    """
    cp = codec_for(field.codepage or default_codepage)
    try:
        return raw.decode(cp, errors="strict")
    except UnicodeDecodeError as exc:
        raise ConversionError(
            f"byte 0x{raw[exc.start]:02X} at offset {exc.start} is not valid in {cp}"
        ) from exc


def convert_field(raw: bytes, field: Field, default_codepage: str) -> object:
    if field.type == "packed":
        # packed decimal is binary (BCD), never decoded through a codepage
        return convert_packed(raw, field)
    try:
        text = decode_field(raw, field, default_codepage)
    except ConversionError:
        # the caller prefixes the field name; adding it here would print it
        # twice in the report
        raise
    text = normalize_text(text, field)
    if field.mask:
        return mask_value(text, field)
    return convert_text(text, field)


def normalize_text(text: str, field: Field) -> str:
    """Per-field text normalization.

    Legacy ERP exports carry the same value spelled several ways: padded,
    full-width digits, decomposed accents. Options are explicit and per
    field, so two schemas that must agree still produce identical output
    only when they say so.
    """
    if field.normalize:
        # the schema validator restricts this to the four NFC/NFD/NFKC/NFKD
        # forms, which is what unicodedata.normalize accepts
        form = cast('Literal["NFC", "NFD", "NFKC", "NFKD"]', field.normalize)
        text = unicodedata.normalize(form, text)
    if field.trim:
        # strip() removes NBSP too, which is a common EBCDIC artifact
        text = text.strip()
    if field.case == "upper":
        text = text.upper()
    elif field.case == "lower":
        text = text.lower()
    elif field.case == "title":
        text = text.title()
    return text


def mask_value(value: str, field: Field) -> str:
    """Redact a field so fixtures can be shared without leaking data.

    `partial` keeps `mask_keep` leading and trailing characters, which is
    usually enough to correlate rows without identifying a person. `hash`
    is deterministic (SHA-256, truncated) so the same input always masks
    to the same token and joins still work.
    """
    mode = field.mask
    keep = field.mask_keep
    if mode == "full":
        return "*" * len(value)
    if mode == "hash":
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return f"sha256:{digest[:16]}"
    # partial
    if keep <= 0 or keep * 2 >= len(value):
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]


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


# Mainframe credit/debit markers, the two-letter form that follows the digits:
# "1234.56CR" is a credit (adds), "1234.56DB" a debit (subtracts). A number
# can never end in these, so recognising them cannot corrupt valid data.
CREDIT_SUFFIX = "CR"
DEBIT_SUFFIX = "DB"

# EBCDIC overpunch, the one-letter form where the sign and the last digit
# share a position: after decoding, "A".."I" are +0..+9 and "J".."R" are
# -0..-9. Unlike CR/DB this can collide with a legitimate trailing letter,
# so it is opt-in per field.
OVERPUNCH_POSITIVE = "ABCDEFGHI"
OVERPUNCH_NEGATIVE = "JKLMNOPQR"


def _strip_overpunch(stripped: str) -> tuple[str, bool]:
    """Split an overpunched last digit into `(digits, negative)`."""
    last = stripped[-1].upper()
    if last in OVERPUNCH_POSITIVE:
        return stripped[:-1] + str(OVERPUNCH_POSITIVE.index(last)), False
    if last in OVERPUNCH_NEGATIVE:
        return stripped[:-1] + str(OVERPUNCH_NEGATIVE.index(last)), True
    return stripped, False


def convert_decimal(text: str, field: Field) -> decimal.Decimal:
    stripped = text.strip()
    if not stripped:
        raise ConversionError("empty decimal")
    negative = False
    upper = stripped.upper()

    if upper.endswith(CREDIT_SUFFIX):  # "1234.56CR"
        stripped = stripped[: -len(CREDIT_SUFFIX)]
        negative = False
    elif upper.endswith(DEBIT_SUFFIX):  # "1234.56DB"
        stripped = stripped[: -len(DEBIT_SUFFIX)]
        negative = True
    elif stripped.endswith("-"):  # trailing sign: "12345-"
        negative = True
        stripped = stripped[:-1]
    elif field.overpunch:
        # checked last: "CR" also ends in "R", and CR is not overpunch
        stripped, overpunched_negative = _strip_overpunch(stripped)
        negative = overpunched_negative

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
