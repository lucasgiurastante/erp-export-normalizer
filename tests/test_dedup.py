"""Duplicate key detection while streaming."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest

from cli import main
from core import dedup
from core.dedup import DedupError, DedupIndex
from core.schema import build_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")

SCHEMA_OBJ = build_schema(
    {
        "format": "t",
        "version": "1.0.0",
        "record_length": 6,
        "fields": [{"name": "id", "start": 0, "length": 6}],
    }
)


class TestDedupIndex(unittest.TestCase):
    def test_unique_keys_have_no_duplicates(self):
        idx = DedupIndex()
        for line, key in enumerate(["a", "b", "c"], start=1):
            idx.observe(key, line)
        report = idx.report()
        self.assertFalse(report.has_duplicates)
        self.assertEqual(report.distinct_keys, 3)

    def test_repeat_is_reported_with_both_lines(self):
        idx = DedupIndex()
        idx.observe("a", 1)
        idx.observe("b", 2)
        idx.observe("a", 3)
        report = idx.report()
        self.assertTrue(report.has_duplicates)
        self.assertEqual(report.duplicate_count, 1)
        dup = report.duplicates[0]
        self.assertEqual(dup.key, "a")
        self.assertEqual(dup.first_line, 1)
        self.assertEqual(dup.line, 3)

    def test_max_keys_fails_loudly(self):
        """A run that cannot prove uniqueness must not claim it did."""
        idx = DedupIndex(max_keys=2)
        idx.observe("a", 1)
        idx.observe("b", 2)
        with self.assertRaises(DedupError) as ctx:
            idx.observe("c", 3)
        self.assertIn("distinct key limit", str(ctx.exception))

    def test_duplicates_do_not_consume_the_key_budget(self):
        idx = DedupIndex(max_keys=2)
        idx.observe("a", 1)
        idx.observe("a", 2)
        idx.observe("b", 3)
        self.assertEqual(idx.report().duplicate_count, 1)

    def test_sample_limit_keeps_exact_count(self):
        idx = DedupIndex(sample_limit=2)
        idx.observe("dup", 1)  # first sighting is not a duplicate
        for line in range(2, 12):
            idx.observe("dup", line)
        report = idx.report()
        self.assertEqual(report.duplicate_count, 10)
        self.assertEqual(len(report.duplicates), 2)
        self.assertTrue(report.truncated)

    def test_rejects_non_positive_max_keys(self):
        with self.assertRaises(DedupError):
            DedupIndex(max_keys=0)

    def test_row_key_requires_fields(self):
        with self.assertRaises(DedupError):
            dedup.row_key({"id": "1"}, [])

    def test_row_key_missing_field(self):
        with self.assertRaises(DedupError) as ctx:
            dedup.row_key({"id": "1"}, ["id", "nope"])
        self.assertIn("nope", str(ctx.exception))

    def test_composite_key(self):
        self.assertEqual(dedup.row_key({"a": "1", "b": "2"}, ["a", "b"]), "1\x1f2")

    def test_validate_key_fields(self):
        dedup.validate_key_fields(SCHEMA_OBJ, ["id"])
        with self.assertRaises(DedupError) as ctx:
            dedup.validate_key_fields(SCHEMA_OBJ, ["nope"])
        self.assertIn("not in schema", str(ctx.exception))


class TestCliDedup(unittest.TestCase):
    def _fixture_with_dup(self, tmp: str) -> str:
        with open(FIXTURE, "rb") as fh:
            lines = fh.read().splitlines(keepends=True)
        path = os.path.join(tmp, "dup.txt")
        with open(path, "wb") as fh:
            fh.writelines([lines[0], lines[1], lines[0], lines[2]])
        return path

    def _convert(self, src: str, *extra: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o.json")
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        src,
                        "--output",
                        out,
                        "--format",
                        "json",
                        "--key",
                        "id",
                        *extra,
                    ]
                )
            return code, err.getvalue(), out

    def test_clean_file_exits_0(self):
        code, err, _ = self._convert(FIXTURE)
        self.assertEqual(code, 0)
        self.assertIn("duplicates: 0", err)

    def test_duplicate_exits_3_and_names_the_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._fixture_with_dup(tmp)
            code, err, _ = self._convert(src)
            self.assertEqual(code, 3)
            self.assertIn("line 3:", err)
            self.assertIn("duplicate key 'AR1001'", err)
            self.assertIn("first seen on line 1", err)

    def test_line_prefix_contract_preserved(self):
        """Scripts parse 'line N:'; dedup must not invent a new format."""
        with tempfile.TemporaryDirectory() as tmp:
            src = self._fixture_with_dup(tmp)
            code, err, _ = self._convert(src)
            self.assertEqual(code, 3)
            self.assertTrue(
                any(line.startswith("  line 3:") for line in err.splitlines())
            )

    def test_max_keys_returns_error_not_a_silent_pass(self):
        code, err, _ = self._convert(FIXTURE, "--max-keys", "2")
        self.assertEqual(code, 1)
        self.assertIn("distinct key limit", err)

    def test_unknown_key_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        FIXTURE,
                        "--output",
                        os.path.join(tmp, "o.json"),
                        "--format",
                        "json",
                        "--key",
                        "nope",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("not in schema", err.getvalue())

    def test_workers_produce_the_same_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._fixture_with_dup(tmp)
            digests = []
            for workers in ("1", "4"):
                out = os.path.join(tmp, f"w{workers}.json")
                with (
                    contextlib.redirect_stderr(io.StringIO()),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    main(
                        [
                            "--schema",
                            SCHEMA,
                            "--input",
                            src,
                            "--output",
                            out,
                            "--format",
                            "json",
                            "--key",
                            "id",
                            "--workers",
                            workers,
                        ]
                    )
                with open(out, "rb") as fh:
                    digests.append(fh.read())
            self.assertEqual(digests[0], digests[1])

    def test_no_key_means_no_dedup(self):
        """Without --key nothing is checked: a duplicated row is not an error."""
        with tempfile.TemporaryDirectory() as tmp:
            src = self._fixture_with_dup(tmp)
            out = os.path.join(tmp, "o.json")
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        src,
                        "--output",
                        out,
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertNotIn("duplicate", err.getvalue())


if __name__ == "__main__":
    unittest.main()
