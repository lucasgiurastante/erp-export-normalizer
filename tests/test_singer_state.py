"""Singer resumable STATE: bookmarks, resume, and byte-equality."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from cli import main
from core.schema import build_schema
from core.validator import FieldValue, RecordResult
from core.writer import SingerWriter, resume_line

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")

SCHEMA = build_schema(
    {
        "format": "t",
        "version": "1.0.0",
        "table": "stream_t",
        "record_length": 4,
        "fields": [{"name": "id", "start": 0, "length": 4}],
    }
)


def result(line: int) -> RecordResult:
    return RecordResult(
        line=line, ok=True, errors=[], fields=[FieldValue("id", f"r{line:03d}", "")]
    )


def messages(out: str) -> list[dict]:
    with open(out, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


class TestSingerState(unittest.TestCase):
    def test_emits_schema_records_and_state(self):
        buf = io.StringIO()
        w = SingerWriter(SCHEMA, buf)
        w.write(result(1))
        w.finish()
        msgs = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual([m["type"] for m in msgs], ["SCHEMA", "RECORD", "STATE"])

    def test_state_carries_the_bookmark_line(self):
        buf = io.StringIO()
        w = SingerWriter(SCHEMA, buf)
        w.write(result(7))
        w.finish()
        state = json.loads(buf.getvalue().splitlines()[-1])
        self.assertEqual(state["value"]["line"], 7)
        self.assertEqual(state["value"]["stream"], "stream_t")

    def test_state_emitted_periodically(self):
        buf = io.StringIO()
        w = SingerWriter(SCHEMA, buf, state_every=2)
        for i in range(1, 6):
            w.write(result(i))
        w.finish()
        states = [
            json.loads(line)
            for line in buf.getvalue().splitlines()
            if '"STATE"' in line
        ]
        # after 2, 4 and the final one
        self.assertEqual([s["value"]["line"] for s in states], [2, 4, 5])

    def test_state_every_zero_only_finishes(self):
        buf = io.StringIO()
        w = SingerWriter(SCHEMA, buf, state_every=0)
        for i in range(1, 4):
            w.write(result(i))
        self.assertNotIn("STATE", buf.getvalue())
        w.finish()
        self.assertEqual(buf.getvalue().count("STATE"), 1)

    def test_emit_schema_false_suppresses_header(self):
        buf = io.StringIO()
        SingerWriter(SCHEMA, buf, emit_schema=False).finish()
        self.assertNotIn("SCHEMA", buf.getvalue())

    def test_state_file_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.state")
            w = SingerWriter(SCHEMA, io.StringIO(), state_path=path)
            w.write(result(3))
            w.finish()
            self.assertEqual(resume_line(path), 3)


class TestResumeLine(unittest.TestCase):
    def test_missing_file_is_zero(self):
        self.assertEqual(resume_line("/nonexistent/state.json"), 0)

    def test_malformed_file_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json")
            self.assertEqual(resume_line(path), 0)

    def test_wrong_shape_is_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            for payload in (
                {},
                {"value": None},
                {"value": {"line": "x"}},
                {"value": {"line": -1}},
                {"value": {"line": True}},
            ):
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                self.assertEqual(resume_line(path), 0, payload)


class TestCliResume(unittest.TestCase):
    def _big_input(self, path: str, lines: int) -> None:
        with open(FIXTURE, "rb") as fh:
            seed = fh.read().splitlines(keepends=True)
        with open(path, "wb") as fh:
            for i in range(lines):
                fh.write(seed[i % len(seed)])

    def _convert(self, *argv: str) -> int:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return main(list(argv))

    def test_resume_is_byte_identical_to_one_run(self):
        """The determinism promise must survive an interrupted run."""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "big.txt")
            self._big_input(src, 2500)
            full = os.path.join(tmp, "full.jsonl")
            self.assertEqual(
                self._convert(
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    src,
                    "--output",
                    full,
                    "--format",
                    "singer",
                    "--state-every",
                    "1000",
                ),
                0,
            )
            # A target that received SCHEMA + 1000 records + the STATE, then died.
            with open(full, encoding="utf-8") as fh:
                lines = fh.read().splitlines(keepends=True)
            partial = os.path.join(tmp, "partial.jsonl")
            with open(partial, "w", encoding="utf-8") as fh:
                fh.writelines(lines[:1002])
            state = json.loads(lines[1001])
            self.assertEqual(state["value"]["line"], 1000)
            state_path = os.path.join(tmp, "partial.jsonl.state")
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)

            self.assertEqual(
                self._convert(
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    src,
                    "--output",
                    partial,
                    "--format",
                    "singer",
                    "--state",
                    state_path,
                    "--state-every",
                    "1000",
                ),
                0,
            )
            with open(full, "rb") as a, open(partial, "rb") as b:
                self.assertEqual(a.read(), b.read())

    def test_resume_skips_already_emitted_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "big.txt")
            self._big_input(src, 100)
            out = os.path.join(tmp, "s.jsonl")
            state_path = os.path.join(tmp, "s.jsonl.state")
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump({"type": "STATE", "value": {"line": 40}}, fh)
            self.assertEqual(
                self._convert(
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    src,
                    "--output",
                    out,
                    "--format",
                    "singer",
                    "--state",
                    state_path,
                ),
                0,
            )
            records = [m for m in messages(out) if m["type"] == "RECORD"]
            self.assertEqual(len(records), 60)
            # the fixture cycles AR1001..AR1003, so line 41 is AR1002
            self.assertEqual(records[0]["record"]["id"], "AR1002")

    def test_state_sidecar_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "s.jsonl")
            self._convert(
                "--schema",
                SCHEMA_PATH,
                "--input",
                FIXTURE,
                "--output",
                out,
                "--format",
                "singer",
            )
            self.assertTrue(os.path.exists(out + ".state"))
            self.assertEqual(resume_line(out + ".state"), 3)

    def test_bad_state_file_starts_from_the_top(self):
        """A target that lost its state must still make progress."""
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "s.jsonl")
            state_path = os.path.join(tmp, "broken.state")
            with open(state_path, "w", encoding="utf-8") as fh:
                fh.write("garbage")
            self.assertEqual(
                self._convert(
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    FIXTURE,
                    "--output",
                    out,
                    "--format",
                    "singer",
                    "--state",
                    state_path,
                ),
                0,
            )
            records = [m for m in messages(out) if m["type"] == "RECORD"]
            self.assertEqual(len(records), 3)

    def test_state_ignored_for_other_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o.json")
            state_path = os.path.join(tmp, "s.state")
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump({"type": "STATE", "value": {"line": 2}}, fh)
            self.assertEqual(
                self._convert(
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    FIXTURE,
                    "--output",
                    out,
                    "--format",
                    "json",
                    "--state",
                    state_path,
                ),
                0,
            )
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(len(json.load(fh)), 3)


if __name__ == "__main__":
    unittest.main()
