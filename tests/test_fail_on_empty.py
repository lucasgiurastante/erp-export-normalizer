"""--fail-on-empty: an empty extract must not pass for a good one."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest

import yaml

from cli import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")
PACKED = os.path.join(REPO_ROOT, "core", "formats", "cobol_packed.yaml")
PACKED_FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "cobol_packed.txt")

DELIMITED = {
    "format": "delimited",
    "version": "1.0.0",
    "delimiter": ",",
    "has_header": True,
    "fields": [
        {"name": "id", "type": "string"},
        {"name": "amount", "type": "decimal", "scale": 2},
    ],
}


class FailOnEmptyBase(unittest.TestCase):
    def _write_schema(self, tmp: str, data: dict) -> str:
        path = os.path.join(tmp, "s.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh)
        return path

    def _run(self, schema: str, src: str, *extra: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o.json")
            err, sout = io.StringIO(), io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(sout):
                code = main(
                    [
                        "--schema",
                        schema,
                        "--input",
                        src,
                        "--output",
                        out,
                        "--format",
                        "json",
                        *extra,
                    ]
                )
            return code, err.getvalue(), sout.getvalue()


class TestEmptyFile(FailOnEmptyBase):
    def _empty(self, tmp: str) -> str:
        path = os.path.join(tmp, "empty.txt")
        open(path, "wb").close()
        return path

    def test_flag_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, err, _ = self._run(SCHEMA, self._empty(tmp), "--fail-on-empty")
            self.assertEqual(code, 3)
            self.assertIn("fail-on-empty", err)
            self.assertIn("empty (0 bytes)", err)

    def test_without_flag_exits_0_but_warns(self):
        """Opt-in breaks the pipeline; the warning is always there."""
        with tempfile.TemporaryDirectory() as tmp:
            code, err, _ = self._run(SCHEMA, self._empty(tmp))
            self.assertEqual(code, 0)
            self.assertIn("warning: no records produced", err)

    def test_warning_says_the_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, err, _ = self._run(SCHEMA, self._empty(tmp))
            self.assertIn("0 bytes", err)


class TestHeaderOnlyFile(FailOnEmptyBase):
    def test_message_distinguishes_it_from_a_truly_empty_file(self):
        """The case I flagged: a pipeline that always trips on a header-only
        file just turns the flag off and goes back to silence."""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "header.txt")
            with open(src, "wb") as fh:
                fh.write(b"ID,AMOUNT\n")
            schema = self._write_schema(tmp, DELIMITED)
            code, err, _ = self._run(schema, src, "--fail-on-empty")
            self.assertEqual(code, 3)
            self.assertNotIn("is empty (0 bytes)", err)
            self.assertIn("header row", err)

    def test_names_the_byte_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "header.txt")
            with open(src, "wb") as fh:
                fh.write(b"ID,AMOUNT\n")
            schema = self._write_schema(tmp, DELIMITED)
            _, err, _ = self._run(schema, src, "--fail-on-empty")
            self.assertIn("10 bytes", err)


class TestAllRowsInvalid(FailOnEmptyBase):
    def test_not_confused_with_a_quiet_file(self):
        """Rows that all fail are a validation problem, not an empty feed.

        The error report already says it, and the emptiness check must not
        pile on top and muddy that.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "bad.txt")
            with open(src, "wb") as fh:
                fh.write(b"ID,AMOUNT\nnotanumber,notanumber\n")
            schema = self._write_schema(tmp, DELIMITED)
            code, err, _ = self._run(schema, src, "--fail-on-empty")
            self.assertEqual(code, 3)
            self.assertIn("invalid decimal", err)
            self.assertNotIn("no records produced", err)


class TestNonEmptyIsUnaffected(FailOnEmptyBase):
    def test_a_good_file_never_warns(self):
        code, err, _ = self._run(SCHEMA, FIXTURE, "--fail-on-empty")
        self.assertEqual(code, 0)
        self.assertNotIn("no records", err)
        self.assertNotIn("fail-on-empty", err)

    def test_a_file_with_only_errors_is_not_empty(self):
        """Every row bad is different from no rows: the error report stands."""
        code, err, _ = self._run(PACKED, PACKED_FIXTURE, "--fail-on-empty")
        self.assertEqual(code, 3)
        self.assertNotIn("no records produced", err)

    def test_default_is_unchanged(self):
        """Without the flag an empty file still exits 0, as before."""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "empty.txt")
            open(src, "wb").close()
            self.assertEqual(self._run(SCHEMA, src)[0], 0)


class TestDryRun(FailOnEmptyBase):
    def test_dry_run_respects_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "empty.txt")
            open(src, "wb").close()
            code, err, _ = self._run(SCHEMA, src, "--dry-run", "--fail-on-empty")
            self.assertEqual(code, 3)
            self.assertIn("fail-on-empty", err)


if __name__ == "__main__":
    unittest.main()
