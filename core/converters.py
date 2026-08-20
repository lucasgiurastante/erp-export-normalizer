"""Conversión de campos: fechas, decimales, codepages."""
from __future__ import annotations

import datetime
import decimal

from .schema import Field


class ConversionError(ValueError):
    """Valor de campo no convertible según su tipo/format en el schema."""


def decode_field(raw: bytes, field: Field, default_codepage: str) -> str:
    cp = field.codepage or default_codepage
    return raw.decode(cp, errors="strict")


def convert_field(raw: bytes, field: Field, default_codepage: str) -> object:
    text = decode_field(raw, field, default_codepage)
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
            raise ConversionError(f"fecha no válida {text!r} (espera YYYYMMDD)")
        try:
            dt = datetime.date(
                int(stripped[0:4]), int(stripped[4:6]), int(stripped[6:8])
            )
        except ValueError as exc:
            raise ConversionError(f"fecha no válida {text!r}: {exc}") from exc
        return dt.isoformat()
    raise ConversionError(f"formato de fecha no soportado: {fmt!r}")


def convert_decimal(text: str, field: Field) -> decimal.Decimal:
    stripped = text.strip()
    if not stripped:
        raise ConversionError("decimal vacío")
    negative = False
    if stripped.endswith("-"):  # signo final estilo mainframe: "12345-"
        negative = True
        stripped = stripped[:-1]
    try:
        value = decimal.Decimal(stripped)
    except (decimal.InvalidOperation, ValueError) as exc:
        raise ConversionError(f"decimal no válido {text!r}") from exc
    if negative:
        value = -value
    if field.scale:
        value = value.scaleb(-field.scale)
    return value