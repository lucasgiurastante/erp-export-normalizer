"""Format auto-detection against the built-in schema library.

Heuristic: for every schema in the library, score how well it parses the
first records of the input (record length match + successfully converted
fields). The highest-scoring schema wins; a zero score means no match.
Deterministic: candidate order is sorted by path, ties go to the first.
"""
from __future__ import annotations

import glob
import os
import sys

from .schema import Schema, SchemaError, load_schema
from .validator import Validator

DEFAULT_FORMATS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "formats"
)
SAMPLE_RECORDS = 5
PERFECT_RECORD_BONUS = 10


class Detection:
    def __init__(self, schema: Schema, source_path: str, score: int):
        self.schema = schema
        self.source_path = source_path
        self.score = score


class Detector:
    def __init__(self, formats_dir: str = DEFAULT_FORMATS_DIR):
        self.formats_dir = formats_dir

    def _candidates(self) -> list[tuple[str, Schema]]:
        candidates: list[tuple[str, Schema]] = []
        for path in sorted(glob.glob(os.path.join(self.formats_dir, "*.yaml"))):
            try:
                candidates.append((path, load_schema(path)))
            except (OSError, SchemaError) as exc:
                print(f"warning: skipping schema {path}: {exc}", file=sys.stderr)
        return candidates

    @staticmethod
    def _sample_records(path: str) -> list[bytes]:
        records: list[bytes] = []
        with open(path, "rb") as fh:
            for raw in fh:
                record = raw.rstrip(b"\r\n")
                if not record:
                    continue
                records.append(record)
                if len(records) >= SAMPLE_RECORDS:
                    break
        return records

    @staticmethod
    def _score(schema: Schema, records: list[bytes]) -> int:
        """Score = points from records that parse perfectly. A record must be
        fully valid (record length + every field converts) to contribute,
        so a total of 0 means 'no match'."""
        val = Validator(schema)
        total = 0
        for record in records:
            result = val.validate_record(0, record)
            if result.ok:
                total += PERFECT_RECORD_BONUS + len(schema.fields)
        return total

    def detect(self, path: str) -> Detection | None:
        records = self._sample_records(path)
        if not records:
            return None
        best: Detection | None = None
        for source_path, schema in self._candidates():
            score = self._score(schema, records)
            if best is None or score > best.score:
                best = Detection(schema=schema, source_path=source_path, score=score)
        if best is not None and best.score > 0:
            return best
        return None