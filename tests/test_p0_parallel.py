"""P0-5 tests: --chunk-lines passthrough + deterministic parallel order.

- ``--chunk-lines`` parses and defaults to ``core.parallel.CHUNK_LINES``.
- ``validate_parallel`` with a tiny chunk produces the same ordered
  results as the serial validator (small real fixture, not 50k).
- End-to-end: ``--workers 2 --chunk-lines <small>`` output is
  byte-identical to ``--workers 1``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from cli import build_parser, main
from core import parallel, parser, validator
from core import schema as schema_mod

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")

_FIXTURE_CANDIDATES = (
    os.path.join(REPO_ROOT, "examples", "jde_ar.txt"),
    os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt"),
)
FIXTURE_PATH = next(p for p in _FIXTURE_CANDIDATES if os.path.exists(p))


class TestChunkLinesFlag(unittest.TestCase):
    def test_default_is_chunk_lines_constant(self):
        args = build_parser().parse_args(["--input", "in.txt", "--output", "out.json"])
        self.assertEqual(args.chunk_lines, parallel.CHUNK_LINES)

    def test_custom_value_parses(self):
        args = build_parser().parse_args(
            [
                "--input",
                "in.txt",
                "--output",
                "out.json",
                "--workers",
                "2",
                "--chunk-lines",
                "10000",
            ]
        )
        self.assertEqual(args.chunk_lines, 10_000)
        self.assertEqual(args.workers, 2)


class TestParallelOrder(unittest.TestCase):
    def _records(
        self, n: int = 200
    ) -> tuple[schema_mod.Schema, list[tuple[int, bytes]]]:
        sch = schema_mod.load_schema(SCHEMA_PATH)
        with open(FIXTURE_PATH, "rb") as fh:
            seed = fh.read().splitlines()
        items = [(i + 1, seed[i % len(seed)]) for i in range(n)]
        return sch, items

    def test_small_chunk_matches_serial_order(self):
        sch, items = self._records(200)
        serial = validator.Validator(sch)
        expected = [serial.validate_record(n, raw) for n, raw in items]
        got = list(parallel.validate_parallel(iter(items), sch, 2, chunk_lines=7))
        self.assertEqual(len(got), len(expected))
        for exp, res in zip(expected, got, strict=True):
            self.assertEqual(res.line, exp.line)
            self.assertEqual(res.ok, exp.ok)
            self.assertEqual(
                [(f.name, f.value) for f in res.fields],
                [(f.name, f.value) for f in exp.fields],
            )

    def test_cli_workers2_small_chunk_matches_workers1(self):
        with tempfile.TemporaryDirectory() as tmp:
            sch = schema_mod.load_schema(SCHEMA_PATH)
            with open(FIXTURE_PATH, "rb") as fh:
                seed = fh.read().splitlines(keepends=True)
            input_path = os.path.join(tmp, "in.txt")
            with open(input_path, "wb") as fh:
                for i in range(200):
                    fh.write(seed[i % len(seed)])
            assert sch is not None
            out_serial = os.path.join(tmp, "serial.json")
            out_parallel = os.path.join(tmp, "parallel.json")
            code = main(
                [
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    input_path,
                    "--output",
                    out_serial,
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 0)
            code = main(
                [
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    input_path,
                    "--output",
                    out_parallel,
                    "--format",
                    "json",
                    "--workers",
                    "2",
                    "--chunk-lines",
                    "7",
                ]
            )
            self.assertEqual(code, 0)
            with open(out_serial, encoding="utf-8") as fh:
                serial_rows = json.load(fh)
            with open(out_parallel, encoding="utf-8") as fh:
                parallel_rows = json.load(fh)
            self.assertEqual(parallel_rows, serial_rows)
            # Order check against the real parser stream (not just CLI bytes).
            reader = parser.FixedWidthReader(sch, input_path)
            stream_ids = [
                r.fields[0].value
                for r in (
                    validator.Validator(sch).validate_record(n, raw)
                    for n, raw in reader.records(skip_first=False)
                )
                if r.ok
            ]
            self.assertEqual(
                [r["id"] for r in parallel_rows], [str(v) for v in stream_ids]
            )


if __name__ == "__main__":
    unittest.main()
