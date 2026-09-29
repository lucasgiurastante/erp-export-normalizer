"""Duplicate primary-key detection while streaming.

A re-sent batch is the most common failure in an incremental ERP extract: the
feed runs twice, or a file is concatenated with itself, and the same document
number lands in the output twice. Downstream that becomes a double payment.

The card originally proposed HyperLogLog or a temporary SQLite file. Neither
fits what this tool promises:

- HyperLogLog answers "how many distinct keys are there?" with an
  *estimate*. This repo reports the line number and the offending value, so
  an estimate is not actionable.
- A temporary SQLite file makes the run depend on disk the caller did not
  budget for, and leaves state behind if the process is killed.

Instead the key index is kept in memory and **explicitly bounded**. The
memory a run may use is a decision the caller makes (`--max-keys`), not a
surprise; when the bound is hit the run fails loudly and says so, rather than
silently degrading into an estimate or spilling to disk.
"""

from __future__ import annotations

import dataclasses

from .schema import Schema


class DedupError(ValueError):
    """The dedup specification is invalid, or the key bound was exceeded."""


@dataclasses.dataclass(frozen=True)
class Duplicate:
    """One repeated key, with the first and current source lines."""

    key: str
    first_line: int
    line: int

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "first_line": self.first_line,
            "line": self.line,
        }


@dataclasses.dataclass
class DedupResult:
    duplicates: list[Duplicate] = dataclasses.field(default_factory=list)
    duplicate_count: int = 0
    distinct_keys: int = 0
    truncated: bool = False
    max_keys: int | None = None

    @property
    def has_duplicates(self) -> bool:
        return self.duplicate_count > 0

    def to_dict(self) -> dict:
        return {
            "duplicates": [d.to_dict() for d in self.duplicates],
            "duplicate_count": self.duplicate_count,
            "distinct_keys": self.distinct_keys,
            "truncated": self.truncated,
            "max_keys": self.max_keys,
        }


def row_key(row: dict, key_fields: list[str]) -> str:
    """Build a stable composite key from the named fields."""
    if not key_fields:
        raise DedupError("dedup needs at least one --key field")
    parts = []
    for name in key_fields:
        if name not in row:
            raise DedupError(f"key field '{name}' missing from row")
        value = row[name]
        parts.append("" if value is None else str(value))
    return "\x1f".join(parts)


class DedupIndex:
    """Bounded index of keys seen so far.

    `max_keys` caps distinct keys. Reaching it is an error, not a silent
    switch to approximate counting: a run that cannot prove uniqueness
    should not claim it proved it.
    """

    def __init__(self, max_keys: int | None = None, sample_limit: int = 20):
        if max_keys is not None and max_keys <= 0:
            raise DedupError("--max-keys must be > 0")
        self._max_keys = max_keys
        self._sample_limit = sample_limit
        self._first_seen: dict[str, int] = {}
        self.result = DedupResult(max_keys=max_keys)

    def observe(self, key: str, line: int) -> None:
        first = self._first_seen.get(key)
        if first is not None:
            self.result.duplicate_count += 1
            if len(self.result.duplicates) < self._sample_limit:
                self.result.duplicates.append(Duplicate(key, first, line))
            return
        if self._max_keys is not None and len(self._first_seen) >= self._max_keys:
            raise DedupError(
                f"distinct key limit reached ({self._max_keys}); "
                "raise --max-keys or narrow the key"
            )
        self._first_seen[key] = line
        self.result.distinct_keys += 1

    def observe_row(self, row: dict, key_fields: list[str], line: int) -> None:
        self.observe(row_key(row, key_fields), line)

    def report(self) -> DedupResult:
        self.result.truncated = self.result.duplicate_count > len(
            self.result.duplicates
        )
        return self.result


def validate_key_fields(schema: Schema, key_fields: list[str]) -> None:
    """Fail before the run if a key field is not in the schema."""
    names = {f.name for f in schema.fields}
    missing = [n for n in key_fields if n not in names]
    if missing:
        raise DedupError("key field(s) not in schema: " + ", ".join(sorted(missing)))
