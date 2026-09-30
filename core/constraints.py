"""Per-field constraints: what a value must satisfy beyond its type.

`docs/RFC_FIELD_CONSTRAINTS.md` is the design. The rule that shapes this
module is the separation it insists on:

    a value that could not be CONVERTED is corrupt data
    a value that CONVERTED but broke a CONSTRAINT is out-of-policy data

An operator has to be able to tell those apart. "invalid date" on a
`2099-01-01` that simply sits past the period end is a badly worded
message that sends them looking for corruption in a file that is fine.

So nothing here parses. Constraints run on the already-typed value, and
their messages are worded so the difference is visible in the report.
"""

from __future__ import annotations

import datetime
import decimal
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # breaks the import cycle: schema imports this module
    from .schema import Field

# Date bounds are written the way a business document writes them. If the
# field declares its own `format`, that format is accepted too, so nobody
# has to remember two spellings of the same date.
DATE_FORMATS = {
    "YYYYMMDD": "%Y%m%d",
    "YYYY-MM-DD": "%Y-%m-%d",
    "DDMMYYYY": "%d%m%Y",
    "DD/MM/YYYY": "%d/%m/%Y",
    "YYMMDD": "%y%m%d",
}


class ConstraintError(ValueError):
    """The constraint specification in the schema is invalid."""


def _as_date(value: object, field: Field) -> datetime.date:
    """Parse a bound that a person wrote in a schema."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    patterns = [DATE_FORMATS["YYYY-MM-DD"]]
    if field.format and field.format in DATE_FORMATS:
        patterns.insert(0, DATE_FORMATS[field.format])
    for pattern in patterns:
        try:
            return datetime.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ConstraintError(
        f"field '{field.name}': cannot read {value!r} as a date; use "
        "YYYY-MM-DD" + (f" or {field.format}" if field.format else "")
    )


def _as_number(value: object, field: Field) -> decimal.Decimal:
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, bool):
        raise ConstraintError(f"field '{field.name}': a boolean is not a numeric bound")
    if isinstance(value, int | float):
        return decimal.Decimal(str(value))
    try:
        return decimal.Decimal(str(value).strip())
    except (decimal.InvalidOperation, ValueError) as exc:
        raise ConstraintError(
            f"field '{field.name}': {value!r} is not a number"
        ) from exc


def _same_kind(field: Field, sample: object) -> str:
    """Classify the field's value domain for bound comparison."""
    if field.type in ("decimal", "packed"):
        return "number"
    if field.type == "date":
        return "date"
    if field.type == "string":
        return "text"
    raise ConstraintError(f"field '{field.name}': unsupported type {field.type!r}")


def check(field: Field, value: object, raw: str) -> list[str]:
    """Return every constraint violation for one value, in schema order.

    All violations are returned, not just the first: an operator fixing a
    layout wants the whole list for that line, the same way the conversion
    errors already behave.
    """
    problems: list[str] = []

    # `required` and `pattern` read the text, because that is the layer they
    # are about. A decimal of all spaces is `0`, which is not "missing".
    text = raw.strip()

    if field.required and not text:
        problems.append("required field is empty")
        return problems  # nothing else is meaningful on an absent value

    if field.pattern is not None:
        if field.type != "string":
            raise ConstraintError(
                f"field '{field.name}': 'pattern' only applies to type "
                f"string, not {field.type}"
            )
        if text and not re.search(field.pattern, text):
            problems.append(f"does not match pattern {field.pattern!r} (raw {text!r})")

    if field.allowed:
        if value is None:
            problems.append("value is empty but the field is restricted")
        else:
            allowed = {_enum_key(field, a) for a in field.allowed}
            if _enum_key(field, value) not in allowed:
                shown = ", ".join(str(a) for a in field.allowed[:8])
                if len(field.allowed) > 8:
                    shown += ", ..."
                problems.append(f"{value!r} is not one of the allowed values ({shown})")

    kind = _same_kind(field, value)
    if field.minimum is not None:
        problems.extend(_bound(field, value, kind, "min", "below", True))
    if field.maximum is not None:
        problems.extend(_bound(field, value, kind, "max", "above", False))

    return problems


def _compare_value(field: Field, value: object) -> tuple[str, object]:
    """Reduce a value to a `(kind, comparable)` pair.

    A field's domain is decided by its type, so the caller never mixes
    numbers with dates or text: comparing across kinds would be a type
    error, not a data error.
    """
    if field.type in ("decimal", "packed"):
        return "number", _as_number(value, field)
    if field.type == "date":
        return "date", _as_date(value, field)
    return "text", str(value)


def _compare(field: Field, kind: str, left: object, right: object) -> int:
    """Ordered comparison inside a single kind domain.

    The kind is passed explicitly and re-checked at runtime, so mixing a
    date with a number is a clear configuration error rather than a
    `TypeError` from somewhere deep in a nightly run.
    """
    low: object
    high: object
    if kind == "number":
        low, high = _as_number(left, field), _as_number(right, field)
    elif kind == "date":
        low, high = _as_date(left, field), _as_date(right, field)
    else:
        # case-folded: a person writing min: a, max: z means the alphabet,
        # not the codepoint range where 'M' sits *below* 'a'. Comparing
        # case-sensitively would make that range look broken.
        low, high = str(left).casefold(), str(right).casefold()
    if low < high:  # type: ignore[operator]
        return -1
    return 1 if low > high else 0  # type: ignore[operator]


def _bound(
    field: Field, value: object, kind: str, which: str, verb: str, is_min: bool
) -> list[str]:
    if value is None:
        return []
    bound = field.minimum if is_min else field.maximum
    order = _compare(field, kind, value, bound)
    violated = order < 0 if is_min else order > 0
    return [f"{verb} {which} {bound}"] if violated else []


def _enum_key(field: Field, value: object) -> object:
    """Compare enums on the typed value, not the raw text.

    Without this, `enum: [1, 2, 3]` would never match a decimal that arrives
    as `1.00`, and the constraint would look like it simply does not work.
    """
    if field.type in ("decimal", "packed"):
        return _as_number(value, field)
    if field.type == "date":
        return _as_date(value, field)
    return str(value)


def validate_spec(field: Field) -> None:
    """Check a field's constraints at schema-load time.

    A pattern that does not compile, an empty enum or a min above the max
    are configuration mistakes. Catching them here means they are reported
    before a single byte of the data file is read.
    """
    name = field.name
    if field.pattern is not None:
        if field.type != "string":
            raise ConstraintError(
                f"field '{name}': 'pattern' only applies to type string, "
                f"not {field.type}"
            )
        try:
            re.compile(field.pattern)
        except re.error as exc:
            raise ConstraintError(
                f"field '{name}': invalid pattern {field.pattern!r}: {exc}"
            ) from exc

    if field.allowed is not None and len(field.allowed) == 0:
        raise ConstraintError(f"field '{name}': 'enum' must not be empty")

    # each bound is checked on its own, not only as a pair: a single
    # unreadable bound would otherwise only surface on the first data row
    if field.minimum is not None:
        _compare(field, _same_kind(field, None), field.minimum, field.minimum)
    if field.maximum is not None:
        _compare(field, _same_kind(field, None), field.maximum, field.maximum)

    if field.minimum is not None and field.maximum is not None:
        kind = _same_kind(field, None)
        if _compare(field, kind, field.minimum, field.maximum) > 0:
            raise ConstraintError(
                f"field '{name}': min {field.minimum!r} is above max {field.maximum!r}"
            )
