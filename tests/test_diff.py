"""Row-level diff between two conversions: added, removed, changed, bounds."""

from __future__ import annotations

import contextlib
import decimal
import io
import json
import os
import unittest

from cli import main
from core import diff as diff_mod
from core.diff import DiffError, diff_rows

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
AR = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")
V2 = os.path.join(REPO_ROOT, "examples", "data", "jde_ar_v2.txt")
COPY = os.path.join(REPO_ROOT, "examples", "data", "jde_ar_copy.txt")


def row(id_: str, amount: str, **extra) -> dict:
    out = {"id": id_, "amount": decimal.Decimal(amount)}
    out.update(extra)
    return out


class TestCanonical(unittest.TestCase):
    def test_decimal_trailing_zeros_normalised(self):
        self.assertEqual(diff_mod._canonical(decimal.Decimal("10.00")), "10")
        self.assertEqual(diff_mod._canonical(decimal.Decimal("10.5")), "10.5")

    def test_none_is_a_sentinel_not_a_string(self):
        self.assertNotEqual(diff_mod._canonical(None), diff_mod._canonical("None"))

    def test_bool_is_not_an_int(self):
        self.assertNotEqual(diff_mod._canonical(True), diff_mod._canonical(1))


class TestDiffRows(unittest.TestCase):
    def test_identical_inputs(self):
        data = [row("1", "10.00"), row("2", "20.00")]
        res = diff_rows("a", data, "b", list(data), ["id"])
        self.assertTrue(res.identical_result)
        self.assertEqual(res.identical, 2)
        self.assertEqual(res.added_count, 0)

    def test_added_removed_changed(self):
        old = [row("1", "10.00"), row("2", "20.00")]
        new = [row("1", "99.00"), row("3", "30.00")]
        res = diff_rows("a", old, "b", new, ["id"])
        self.assertFalse(res.identical_result)
        self.assertEqual(res.added, ["3"])
        self.assertEqual(res.removed, ["2"])
        self.assertEqual(res.added_count, 1)
        self.assertEqual(res.removed_count, 1)
        self.assertEqual(res.changed_count, 1)
        self.assertEqual(res.changed[0].key, "1")

    def test_field_level_detail(self):
        res = diff_rows("a", [row("1", "10.00")], "b", [row("1", "10.01")], ["id"])
        change = res.changed[0]
        self.assertEqual(len(change.fields), 1)
        self.assertEqual(change.fields[0].field, "amount")
        self.assertEqual(change.fields[0].before, "10")
        self.assertEqual(change.fields[0].after, "10.01")

    def test_compare_fields_narrows_the_diff(self):
        old = [row("1", "10.00", note="before")]
        new = [row("1", "10.00", note="after")]
        # by default the note change is visible
        self.assertEqual(diff_rows("a", old, "b", new, ["id"]).changed_count, 1)
        # restricted to amount, both sides agree
        res = diff_rows("a", old, "b", new, ["id"], compare_fields=["amount"])
        self.assertTrue(res.identical_result)

    def test_decimal_equal_different_scale_is_not_a_change(self):
        res = diff_rows("a", [row("1", "10.00")], "b", [row("1", "10.0")], ["id"])
        self.assertTrue(res.identical_result)

    def test_duplicate_key_raises(self):
        with self.assertRaises(DiffError) as ctx:
            diff_rows("a", [row("1", "1"), row("1", "2")], "b", [], ["id"])
        self.assertIn("duplicate key", str(ctx.exception))

    def test_missing_key_field_raises(self):
        with self.assertRaises(DiffError) as ctx:
            diff_rows("a", [{"x": 1}], "b", [], ["id"])
        self.assertIn("key field 'id'", str(ctx.exception))

    def test_missing_compare_field_raises(self):
        with self.assertRaises(DiffError) as ctx:
            diff_rows(
                "a",
                [row("1", "1")],
                "b",
                [row("1", "1")],
                ["id"],
                compare_fields=["nope"],
            )
        self.assertIn("compare field 'nope'", str(ctx.exception))

    def test_needs_a_key(self):
        with self.assertRaises(DiffError):
            diff_rows("a", [], "b", [], [])

    def test_max_keys_bounds_memory(self):
        with self.assertRaises(DiffError) as ctx:
            diff_rows(
                "a",
                [row("1", "1"), row("2", "1"), row("3", "1")],
                "b",
                [],
                ["id"],
                max_keys=2,
            )
        self.assertIn("distinct key limit", str(ctx.exception))

    def test_sample_limit_caps_output_but_not_counts(self):
        old = [row(str(i), "1") for i in range(10)]
        new = [row(str(i), "2") for i in range(10)]
        res = diff_rows("a", old, "b", new, ["id"], sample_limit=3)
        self.assertEqual(res.changed_count, 10)
        self.assertEqual(len(res.changed), 3)
        self.assertTrue(res.truncated)

    def test_output_is_deterministic(self):
        old = [row("3", "1"), row("1", "1"), row("2", "1")]
        new = [row("2", "1"), row("1", "1")]
        first = diff_rows("a", old, "b", new, ["id"]).to_dict()
        second = diff_rows(
            "a", list(reversed(old)), "b", list(reversed(new)), ["id"]
        ).to_dict()
        self.assertEqual(first, second)


class TestFormatReport(unittest.TestCase):
    def test_identical_message(self):
        res = diff_rows("a", [row("1", "1")], "b", [row("1", "1")], ["id"])
        lines = diff_mod.format_report(res, "old.txt", "new.txt")
        self.assertIn("no differences", "\n".join(lines))

    def test_change_lines(self):
        old = [row("1", "1"), row("2", "1")]
        new = [row("1", "2"), row("3", "1")]
        text = "\n".join(
            diff_mod.format_report(
                diff_rows("a", old, "b", new, ["id"]), "old.txt", "new.txt"
            )
        )
        self.assertIn("+ 3", text)
        self.assertIn("- 2", text)
        self.assertIn("~ 1", text)


class TestDiffCli(unittest.TestCase):
    def test_detects_changes_exit_3(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "diff",
                    f"{SCHEMA}={AR}",
                    f"{SCHEMA}={V2}",
                    "--key",
                    "id",
                    "--fields",
                    "amount,date,type,currency",
                ]
            )
        self.assertEqual(code, 3)

    def test_identical_exit_0(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["diff", f"{SCHEMA}={AR}", f"{SCHEMA}={COPY}", "--key", "id"])
        self.assertEqual(code, 0)

    def test_json_output(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                ["diff", f"{SCHEMA}={AR}", f"{SCHEMA}={V2}", "--key", "id", "--json"]
            )
        payload = json.loads(out.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload["counts"]["added"], 1)
        self.assertEqual(payload["counts"]["removed"], 1)
        self.assertEqual(payload["counts"]["changed"], 1)
        self.assertEqual(payload["added"], ["AR1004"])
        self.assertEqual(payload["removed"], ["AR1003"])
        self.assertEqual(payload["old"], "jde_ar.txt")

    def test_bad_pair_format(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["diff", AR, V2, "--key", "id"])
        self.assertEqual(code, 1)
        self.assertIn("SCHEMA=INPUT", err.getvalue())

    def test_needs_key(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(["diff", f"{SCHEMA}={AR}", f"{SCHEMA}={V2}"])
        self.assertEqual(code, 1)
        self.assertIn("--key", err.getvalue())


if __name__ == "__main__":
    unittest.main()
