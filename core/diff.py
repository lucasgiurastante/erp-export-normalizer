"""Row-level diff between two conversions, keyed.

`crosscheck` answers "do these files reconcile?". `diff` answers the follow-up
question a reconciliation failure always raises: "what actually changed?".

Both sides are indexed by key and only the compared fields are retained, so
memory grows with distinct keys times compared fields, never with file size.
`--max-keys` bounds that explicitly and fails loudly instead of growing
without limit.

Changes are reported as three ordered lists, each capped by `sample_limit` so a
runaway diff cannot flood a log:

    added    key present only in the right-hand (new) file
    removed  key present only in the left-hand (old) file
    changed  key in both, but at least one compared field differs
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Iterable, Iterator


class DiffError(ValueError):
    """The diff specification is invalid."""


@dataclasses.dataclass(frozen=True)
class FieldChange:
    field: str
    before: str
    after: str

    def to_dict(self) -> dict:
        return {"field": self.field, "before": self.before, "after": self.after}


@dataclasses.dataclass(frozen=True)
class RowChange:
    key: str
    fields: tuple[FieldChange, ...]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclasses.dataclass
class DiffResult:
    """Summary of a diff: exact counts plus capped samples."""

    added: list[str] = dataclasses.field(default_factory=list)
    removed: list[str] = dataclasses.field(default_factory=list)
    changed: list[RowChange] = dataclasses.field(default_factory=list)
    added_count: int = 0
    removed_count: int = 0
    changed_count: int = 0
    only_in_left: int = 0
    only_in_right: int = 0
    identical: int = 0
    truncated: bool = False
    compared_fields: list[str] = dataclasses.field(default_factory=list)

    @property
    def identical_result(self) -> bool:
        return not (self.added_count or self.removed_count or self.changed_count)

    def to_dict(self) -> dict:
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": [c.to_dict() for c in self.changed],
            "counts": {
                "added": self.added_count,
                "removed": self.removed_count,
                "changed": self.changed_count,
                "only_in_left": self.only_in_left,
                "only_in_right": self.only_in_right,
                "identical": self.identical,
            },
            "compared_fields": list(self.compared_fields),
            "truncated": self.truncated,
            "identical": self.identical_result,
        }


def _canonical(value: object) -> str:
    """Stable text for a field value so comparisons are reproducible.

    Decimals are normalised (`10.00` and `10.0` are the same number) and
    `None` gets a sentinel that cannot collide with a real string.
    """
    if value is None:
        return "\x00null"
    if isinstance(value, decimal.Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(decimal.Decimal(str(value)).normalize(), "f")
    return str(value)


def _key_of(row: dict, key_fields: list[str], label: str) -> str:
    parts = []
    for name in key_fields:
        if name not in row:
            raise DiffError(f"{label}: key field '{name}' missing from row")
        parts.append(_canonical(row[name]))
    return "\x1f".join(parts)


def _projection(
    row: dict, compare_fields: list[str] | None, label: str
) -> dict[str, str]:
    names = list(compare_fields) if compare_fields is not None else list(row)
    out: dict[str, str] = {}
    for name in names:
        if name not in row:
            if compare_fields is not None:
                raise DiffError(f"{label}: compare field '{name}' missing from row")
            continue
        out[name] = _canonical(row[name])
    return out


def _index(
    label: str,
    rows: Iterable[dict],
    key_fields: list[str],
    compare_fields: list[str] | None,
    max_keys: int | None,
) -> dict[str, dict[str, str]]:
    """Index one side by key, keeping only the compared fields."""
    index: dict[str, dict[str, str]] = {}
    for row in rows:
        key = _key_of(row, key_fields, label)
        if key in index:
            raise DiffError(f"{label}: duplicate key {key!r}; diff needs unique keys")
        if max_keys is not None and len(index) >= max_keys:
            raise DiffError(
                f"{label}: distinct key limit reached ({max_keys}); "
                "raise --max-keys or narrow the key"
            )
        index[key] = _projection(row, compare_fields, label)
    return index


def _field_changes(
    before: dict[str, str], after: dict[str, str]
) -> tuple[FieldChange, ...]:
    out: list[FieldChange] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name, "\x00absent")
        new = after.get(name, "\x00absent")
        if old != new:
            out.append(FieldChange(name, old, new))
    return tuple(out)


def diff_rows(
    left_label: str,
    left_rows: Iterable[dict],
    right_label: str,
    right_rows: Iterable[dict],
    key_fields: list[str],
    compare_fields: list[str] | None = None,
    sample_limit: int = 20,
    max_keys: int | None = None,
) -> DiffResult:
    """Diff two row streams on `key_fields`.

    `compare_fields` narrows the comparison; by default every field present in
    a row is compared. Samples are sorted by key for deterministic output.
    """
    if not key_fields:
        raise DiffError("diff needs at least one --key field")
    left = _index(left_label, left_rows, key_fields, compare_fields, max_keys)
    right = _index(right_label, right_rows, key_fields, compare_fields, max_keys)

    added_keys = [k for k in right if k not in left]
    removed_keys = [k for k in left if k not in right]
    changed_keys = [k for k in left if k in right and left[k] != right[k]]

    compared = (
        sorted(compare_fields)
        if compare_fields
        else sorted(set().union(*left.values()) if left else set())
    )
    result = DiffResult(
        added_count=len(added_keys),
        removed_count=len(removed_keys),
        changed_count=len(changed_keys),
        only_in_left=len(removed_keys),
        only_in_right=len(added_keys),
        identical=len(left) - len(removed_keys) - len(changed_keys),
        compared_fields=compared,
    )
    result.added = sorted(added_keys)[:sample_limit]
    result.removed = sorted(removed_keys)[:sample_limit]
    result.truncated = (
        len(added_keys) > sample_limit or len(removed_keys) > sample_limit
    )
    for key in sorted(changed_keys)[:sample_limit]:
        result.changed.append(RowChange(key, _field_changes(left[key], right[key])))
    if len(changed_keys) > sample_limit:
        result.truncated = True
    return result


def format_report(result: DiffResult, left_label: str, right_label: str) -> list[str]:
    """Human-readable lines for the CLI; deterministic order."""
    lines = [
        f"{left_label}: {result.only_in_left + result.identical + result.changed_count}"
        f" rows | {right_label}: "
        f"{result.only_in_right + result.identical + result.changed_count} rows"
    ]
    if result.identical_result:
        lines.append("  no differences")
        return lines
    lines.append(
        f"  added={result.added_count} removed={result.removed_count} "
        f"changed={result.changed_count} identical={result.identical}"
    )
    if result.compared_fields:
        lines.append(f"  compared fields: {', '.join(result.compared_fields)}")
    for key in result.added:
        lines.append(f"  + {key}")
    for key in result.removed:
        lines.append(f"  - {key}")
    for change in result.changed:
        if change.fields:
            detail = ", ".join(
                f"{f.field}: {f.before} -> {f.after}" for f in change.fields
            )
            lines.append(f"  ~ {change.key}: {detail}")
        else:
            lines.append(f"  ~ {change.key}")
    if result.truncated:
        lines.append("  (samples truncated; counts above are exact)")
    return lines


def iter_changes(result: DiffResult) -> Iterator[tuple[str, str]]:
    for key in result.added:
        yield "added", key
    for key in result.removed:
        yield "removed", key
    for change in result.changed:
        yield "changed", change.key
