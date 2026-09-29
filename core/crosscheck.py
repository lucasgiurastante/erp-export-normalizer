"""Cross-file reconciliation between two or more conversions.

A single file can pass every per-line check and still be wrong: a bank
statement that does not reconcile against the ledger is a real failure that
`validator` and `rules` cannot see, because they only ever look at one input.

This module reconciles several conversions on a *key* and reports the
differences, without ever holding more than one row per file in memory:

    - `sum`   : a numeric column must total the same value in every file
    - `count` : every file must have the same number of rows for a key
    - `unique`: a key must not appear more than once in a file
    - `missing`: a key present in the reference file must exist in all others
                (and, with `extra=True`, the reverse too)

Keys are the only state kept, so memory grows with the number of distinct
keys, never with the file size. Use `--max-keys` to bound that explicitly
and fail loudly rather than growing without limit.
"""

from __future__ import annotations

import dataclasses
import decimal
from collections.abc import Iterable, Iterator

from .schema import Schema


class CrossCheckError(ValueError):
    """The cross-check specification is invalid."""


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of one reconciliation check across all files."""

    name: str
    kind: str
    ok: bool
    message: str
    details: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "ok": self.ok,
            "message": self.message,
            "details": list(self.details),
        }


@dataclasses.dataclass
class FileTotals:
    """Per-file accumulators. Constant memory apart from the key index."""

    label: str
    rows: int = 0
    sums: dict[str, decimal.Decimal] = dataclasses.field(default_factory=dict)
    keys: set[str] = dataclasses.field(default_factory=set)
    duplicates: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "file": self.label,
            "rows": self.rows,
            "sums": {k: str(v) for k, v in sorted(self.sums.items())},
            "keys": len(self.keys),
            "duplicates": len(self.duplicates),
        }


def _to_decimal(value: object, field: str, file_label: str) -> decimal.Decimal:
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, bool):
        raise CrossCheckError(f"{file_label}: field '{field}' is a boolean")
    if isinstance(value, (int, float)):
        return decimal.Decimal(str(value))
    raise CrossCheckError(
        f"{file_label}: field '{field}' is not numeric ({type(value).__name__})"
    )


def _key_of(row: dict, fields: list[str], file_label: str) -> str:
    parts = []
    for name in fields:
        if name not in row:
            raise CrossCheckError(f"{file_label}: key field '{name}' missing from row")
        parts.append(str(row[name]))
    return "\x1f".join(parts)


def summarize(
    label: str,
    rows: Iterable[dict],
    schema: Schema,
    key_fields: list[str],
    sum_fields: list[str],
    max_keys: int | None = None,
    sample_limit: int = 20,
) -> FileTotals:
    """Stream one conversion into per-file totals, bounded by `max_keys`."""
    totals = FileTotals(label)
    names = {f.name for f in schema.fields}
    for name in key_fields:
        if name not in names:
            raise CrossCheckError(f"{label}: key field '{name}' not in schema")
    for name in sum_fields:
        if name not in names:
            raise CrossCheckError(f"{label}: sum field '{name}' not in schema")

    for row in rows:
        totals.rows += 1
        key = _key_of(row, key_fields, label)
        if key in totals.keys:
            if len(totals.duplicates) < sample_limit:
                totals.duplicates.append(key)
        elif max_keys is not None and len(totals.keys) >= max_keys:
            raise CrossCheckError(
                f"{label}: distinct key limit reached ({max_keys}); "
                "raise --max-keys or narrow the key"
            )
        else:
            totals.keys.add(key)
        for name in sum_fields:
            if name in row and row[name] is not None:
                totals.sums[name] = totals.sums.get(
                    name, decimal.Decimal(0)
                ) + _to_decimal(row[name], name, label)
    return totals


def _find(totals: list[FileTotals], field: str) -> list[tuple[str, decimal.Decimal]]:
    return [(t.label, t.sums.get(field, decimal.Decimal(0))) for t in totals]


def run_checks(
    totals: list[FileTotals], spec: tuple[dict, ...] | None
) -> list[CheckResult]:
    """Apply the reconciliation checks to already-summarised files."""
    results: list[CheckResult] = []
    for i, check in enumerate(spec or ()):
        kind = check.get("type")
        name = str(check.get("name") or f"{kind}[{i}]")
        if kind == "sum":
            field = check.get("field")
            if not field:
                raise CrossCheckError(f"checks[{i}]: 'sum' requires 'field'")
            sum_values = _find(totals, str(field))
            ok = all(v == sum_values[0][1] for _, v in sum_values[1:])
            sum_detail = [f"{label}={v}" for label, v in sum_values]
            message = (
                f"sum '{field}' agrees across {len(totals)} files"
                if ok
                else f"sum '{field}' differs: " + ", ".join(sum_detail)
            )
            results.append(
                CheckResult(name, "sum", ok, message, [] if ok else sum_detail)
            )
        elif kind == "count":
            row_values = [(t.label, t.rows) for t in totals]
            ok = all(v == row_values[0][1] for _, v in row_values[1:])
            detail = [f"{label}={v}" for label, v in row_values]
            message = (
                f"row count agrees across {len(totals)} files"
                if ok
                else "row count differs: " + ", ".join(detail)
            )
            results.append(
                CheckResult(name, "count", ok, message, [] if ok else detail)
            )
        elif kind == "unique":
            bad = [(t.label, t.duplicates) for t in totals if t.duplicates]
            ok = not bad
            detail = [f"{label}: {len(d)} duplicate keys" for label, d in bad]
            message = "keys unique in every file" if ok else "duplicate keys found"
            results.append(
                CheckResult(
                    name,
                    "unique",
                    ok,
                    message,
                    [f"{label} e.g. {d[0]!r}" for label, d in bad if d],
                )
            )
        elif kind == "missing":
            if len(totals) < 2:
                raise CrossCheckError(
                    f"checks[{i}]: 'missing' needs a reference and at least "
                    "one other file"
                )
            reference, others = totals[0], totals[1:]
            gaps = [
                (
                    t.label,
                    sorted(reference.keys - t.keys)[:10],
                    sorted(t.keys - reference.keys)[:10],
                )
                for t in others
            ]
            extra = bool(check.get("extra"))
            offenders = [
                (label, absent, unexpected)
                for label, absent, unexpected in gaps
                if absent or (extra and unexpected)
            ]
            ok = not offenders
            detail = []
            for label, absent, unexpected in offenders:
                detail.append(f"{label}: {len(absent)} missing from file")
                if unexpected:
                    detail.append(f"{label}: {len(unexpected)} keys not in reference")
            message = (
                f"all {len(reference.keys)} keys present in every file"
                if ok
                else "key sets differ against reference "
                f"'{reference.label}': " + "; ".join(detail)
            )
            results.append(CheckResult(name, "missing", ok, message, detail))
        else:
            raise CrossCheckError(f"checks[{i}]: unsupported type {kind!r}")
    return results


def iter_rows(results: Iterable) -> Iterator[dict]:
    """Small helper so callers can chain generators without materialising."""
    yield from results


def summarize_stream(
    sources: list[tuple[str, Iterable[dict], Schema]],
    spec: tuple[dict, ...] | None,
    key_fields: list[str] | None = None,
    sum_fields: list[str] | None = None,
    max_keys: int | None = None,
) -> tuple[list[FileTotals], list[CheckResult]]:
    """Convenience: summarise several conversions and check them in one go."""
    if len(sources) < 2:
        raise CrossCheckError("cross-check needs at least two files")
    keys = list(key_fields or ())
    sums = list(sum_fields or ())
    totals = [
        summarize(label, rows, schema, keys, sums, max_keys=max_keys)
        for label, rows, schema in sources
    ]
    return totals, run_checks(totals, spec)
