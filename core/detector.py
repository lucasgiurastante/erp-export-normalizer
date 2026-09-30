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

from . import compression
from .schema import Schema, SchemaError, load_schema
from .validator import Validator

DEFAULT_FORMATS_DIR = os.path.join(os.path.dirname(__file__), "formats")
SAMPLE_RECORDS = 20
PERFECT_RECORD_BONUS = 10
AMBIGUITY_MARGIN = 5


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
        # decompressed on the way in, so auto-detection works on a .gz too
        with compression.open_binary(path) as fh:
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
        """Score a schema against sampled records.

        A delimited schema has no `record_length`, and scoring it out just
        for that meant auto-detection could never pick one: every delimited
        file scored 0 and lost to whatever fixed-width schema happened to
        match. The tie-breaker has to consider the delimiter too, or two
        delimited schemas with the same column count look equally good.
        """
        if schema.record_length is None and not schema.delimiter:
            return 0
        val = Validator(schema)
        total = 0
        for record in records:
            result = val.validate_record(0, record)
            total += sum(1 for fv in result.fields if fv.value is not None)
            if result.ok:
                total += PERFECT_RECORD_BONUS + len(schema.fields)
        return total

    @staticmethod
    def _same_shape(a: Schema, b: Schema) -> bool:
        """True when two schemas are indistinguishable on a raw byte line."""
        if a.record_length is not None and b.record_length is not None:
            return a.record_length == b.record_length
        if a.delimiter and b.delimiter:
            return a.delimiter == b.delimiter and len(a.fields) == len(b.fields)
        return False

    def detect(self, path: str) -> Detection | None:
        records = self._sample_records(path)
        if not records:
            return None
        scored: list[tuple[int, str, Schema]] = [
            (self._score(schema, records), source_path, schema)
            for source_path, schema in self._candidates()
        ]
        if not scored:
            return None
        # Stable sort: ties keep the path-sorted candidate order.
        scored.sort(key=lambda item: item[0], reverse=True)
        (best_score, best_path, best_schema), *rest = scored
        if best_score <= 0:
            return None
        if rest:
            second_score, _, second_schema = rest[0]
            if second_score > 0 and best_score - second_score < AMBIGUITY_MARGIN:
                if self._same_shape(best_schema, second_schema):
                    print(
                        "ambiguous match, pass --schema",
                        file=sys.stderr,
                    )
                return None
        return Detection(schema=best_schema, source_path=best_path, score=best_score)
