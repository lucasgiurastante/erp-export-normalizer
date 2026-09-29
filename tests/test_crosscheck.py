"""Cross-file reconciliation: sums, counts, key sets, duplicates, bounds."""

from __future__ import annotations

import contextlib
import decimal
import io
import os
import tempfile
import unittest

from cli import main
from core.crosscheck import CrossCheckError, FileTotals, run_checks, summarize
from core.schema import build_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
AR = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")
LEDGER = os.path.join(REPO_ROOT, "examples", "data", "jde_ar_ledger.txt")
PARTIAL = os.path.join(REPO_ROOT, "examples", "data", "jde_ar_partial.txt")

SCHEMA_OBJ = build_schema(
    {
        "format": "t",
        "version": "1.0.0",
        "record_length": 12,
        "fields": [
            {"name": "id", "start": 0, "length": 4},
            {"name": "amount", "start": 4, "length": 8, "type": "decimal", "scale": 2},
        ],
    }
)


def rows(*specs: tuple[str, str]) -> list[dict]:
    return [{"id": i, "amount": decimal.Decimal(a)} for i, a in specs]


def totals_for(label: str, data: list[dict]) -> FileTotals:
    return summarize(label, data, SCHEMA_OBJ, ["id"], ["amount"])


class TestSummarize(unittest.TestCase):
    def test_counts_rows_keys_and_sums(self):
        t = totals_for("a", rows(("1", "10.50"), ("2", "5.25")))
        self.assertEqual(t.rows, 2)
        self.assertEqual(len(t.keys), 2)
        self.assertEqual(t.sums["amount"], decimal.Decimal("15.75"))

    def test_duplicate_keys_are_recorded(self):
        t = totals_for("a", rows(("1", "1.00"), ("1", "2.00")))
        self.assertEqual(len(t.keys), 1)
        self.assertEqual(t.duplicates, ["1"])
        # the sum still counts both rows: duplicates are a separate check
        self.assertEqual(t.sums["amount"], decimal.Decimal("3.00"))

    def test_unknown_field_raises(self):
        with self.assertRaises(CrossCheckError) as ctx:
            summarize("a", [{"id": "1"}], SCHEMA_OBJ, ["nope"], [])
        self.assertIn("key field 'nope'", str(ctx.exception))

    def test_non_numeric_sum_raises(self):
        with self.assertRaises(CrossCheckError) as ctx:
            summarize(
                "a", [{"id": "1", "amount": "abc"}], SCHEMA_OBJ, ["id"], ["amount"]
            )
        self.assertIn("not numeric", str(ctx.exception))

    def test_max_keys_bounds_memory(self):
        """A large file must fail loudly rather than index without limit."""
        with self.assertRaises(CrossCheckError) as ctx:
            summarize(
                "a",
                rows(("1", "1"), ("2", "1"), ("3", "1")),
                SCHEMA_OBJ,
                ["id"],
                ["amount"],
                max_keys=2,
            )
        self.assertIn("distinct key limit", str(ctx.exception))


class TestRunChecks(unittest.TestCase):
    def test_sum_agrees(self):
        a = totals_for("a", rows(("1", "10.00")))
        b = totals_for("b", rows(("1", "10.00")))
        (res,) = run_checks([a, b], ({"type": "sum", "field": "amount"},))
        self.assertTrue(res.ok)

    def test_sum_differs(self):
        a = totals_for("a", rows(("1", "10.00")))
        b = totals_for("b", rows(("1", "9.99")))
        (res,) = run_checks([a, b], ({"type": "sum", "field": "amount"},))
        self.assertFalse(res.ok)
        self.assertIn("10.00", res.message)
        self.assertIn("9.99", res.message)
        self.assertEqual(len(res.details), 2)

    def test_count_differs(self):
        a = totals_for("a", rows(("1", "1.00")))
        b = totals_for("b", rows(("1", "1.00"), ("2", "1.00")))
        (res,) = run_checks([a, b], ({"type": "count"},))
        self.assertFalse(res.ok)

    def test_unique_detects_duplicates(self):
        a = totals_for("a", rows(("1", "1.00"), ("1", "2.00")))
        b = totals_for("b", rows(("1", "1.00")))
        (res,) = run_checks([a, b], ({"type": "unique"},))
        self.assertFalse(res.ok)
        self.assertIn("a", " ".join(res.details))

    def test_missing_against_reference(self):
        a = totals_for("a", rows(("1", "1.00"), ("2", "1.00")))
        b = totals_for("b", rows(("1", "1.00")))
        (res,) = run_checks([a, b], ({"type": "missing"},))
        self.assertFalse(res.ok)

    def test_missing_ignores_extra_by_default(self):
        """A key the others do not have is not an error unless asked for."""
        a = totals_for("a", rows(("1", "1.00")))
        b = totals_for("b", rows(("1", "1.00"), ("2", "1.00")))
        (res,) = run_checks([a, b], ({"type": "missing"},))
        self.assertTrue(res.ok)
        (res2,) = run_checks([a, b], ({"type": "missing", "extra": True},))
        self.assertFalse(res2.ok)

    def test_missing_needs_two_files(self):
        with self.assertRaises(CrossCheckError):
            run_checks([totals_for("a", rows(("1", "1")))], ({"type": "missing"},))

    def test_unknown_check_raises(self):
        with self.assertRaises(CrossCheckError):
            run_checks([totals_for("a", []), totals_for("b", [])], ({"type": "nope"},))


class TestCrosscheckCli(unittest.TestCase):
    def _run(self, checks: str, *inputs: str, extra: list[str] | None = None) -> int:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            return main(
                [
                    "crosscheck",
                    *inputs,
                    "--key",
                    "id",
                    "--sum",
                    "amount",
                    "--checks",
                    checks,
                    *(extra or []),
                ]
            )

    def test_matching_files_pass(self):
        code = self._run(
            "count,sum:amount,unique,missing",
            f"{SCHEMA}={AR}",
            f"{SCHEMA}={LEDGER}",
        )
        self.assertEqual(code, 0)

    def test_missing_key_fails_with_exit_3(self):
        code = self._run(
            "count,sum:amount,missing",
            f"{SCHEMA}={AR}",
            f"{SCHEMA}={PARTIAL}",
        )
        self.assertEqual(code, 3)

    def test_json_report(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                [
                    "crosscheck",
                    f"{SCHEMA}={AR}",
                    f"{SCHEMA}={PARTIAL}",
                    "--key",
                    "id",
                    "--sum",
                    "amount",
                    "--checks",
                    "sum:amount",
                    "--json",
                ]
            )
        import json

        report = json.loads(out.getvalue())
        self.assertEqual(code, 3)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["files"]), 2)
        self.assertEqual(report["files"][1]["file"], "jde_ar_partial.txt")

    def test_needs_two_inputs(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["crosscheck", f"{SCHEMA}={AR}", "--checks", "count"])
        self.assertEqual(code, 1)
        self.assertIn("at least two", err.getvalue())

    def test_bad_pair_format(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["crosscheck", AR, LEDGER, "--checks", "count"])
        self.assertEqual(code, 1)
        self.assertIn("SCHEMA=INPUT", err.getvalue())

    def test_unknown_check(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "crosscheck",
                    f"{SCHEMA}={AR}",
                    f"{SCHEMA}={LEDGER}",
                    "--checks",
                    "bogus",
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("unknown check", err.getvalue())

    def test_missing_file_returns_schema_or_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = self._run("count", f"{SCHEMA}={AR}", f"{SCHEMA}={tmp}/nope.txt")
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
