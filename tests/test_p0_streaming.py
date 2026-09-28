"""P0-1 tests: JsonWriter streams incrementally (constant memory).

Covers INSTRUCTIVO_AGENTE.md § P0-1 acceptance for the streaming rewrite
of ``core/writer.py`` JsonWriter (open ``[``, ``json.dump`` per row,
close ``]`` on ``finish()``, 0 rows -> ``[]``):

- 100k-line input built by repeating the real ``jde_ar`` fixture converts
  to valid JSON with the exact row count.
- Peak Python memory during the 100k conversion stays bounded
  (tracemalloc; generous cap, no fragile exact-MB assert).
- ``--workers 1`` (serial) and ``--workers 2`` (parallel) outputs are
  byte-identical (same md5).
- Empty input produces ``[]``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import tracemalloc
import unittest

from cli import main

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")

# Instructivo cites examples/jde_ar.txt; the fixture lives in examples/data/.
_FIXTURE_CANDIDATES = (
    os.path.join(REPO_ROOT, "examples", "jde_ar.txt"),
    os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt"),
)
FIXTURE_PATH = next(p for p in _FIXTURE_CANDIDATES if os.path.exists(p))

N_ROWS = 100_000
# Generous bound: measured streaming peak is ~1 MB for 100k rows / ~11 MB
# output; a buffering writer holding every row would scale with N_ROWS.
# Deliberately not an exact-MB assert (machine/tracemalloc variance).
PEAK_CAP_MB = 50

EXPECTED_IDS = ("AR1001", "AR1002", "AR1003")


def _md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _convert(input_path: str, output_path: str, workers: int) -> int:
    return main(
        [
            "--schema",
            SCHEMA_PATH,
            "--input",
            input_path,
            "--output",
            output_path,
            "--format",
            "json",
            "--workers",
            str(workers),
        ]
    )


class TestP0JsonStreaming(unittest.TestCase):
    def _write_big_input(self, tmp: str, n: int = N_ROWS) -> str:
        with open(FIXTURE_PATH, "rb") as fh:
            seed = fh.read().splitlines(keepends=True)
        self.assertGreater(len(seed), 0, "fixture must provide seed lines")
        input_path = os.path.join(tmp, "input.txt")
        with open(input_path, "wb") as fh:
            for i in range(n):
                fh.write(seed[i % len(seed)])
        return input_path

    def test_100k_json_valid_count_and_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = self._write_big_input(tmp)
            out_serial = os.path.join(tmp, "out_w1.json")
            out_parallel = os.path.join(tmp, "out_w2.json")

            tracemalloc.start()
            try:
                code_serial = _convert(input_path, out_serial, workers=1)
            finally:
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            self.assertEqual(code_serial, 0)
            self.assertLess(
                peak,
                PEAK_CAP_MB * 1024 * 1024,
                f"streaming peak {peak / 1024 / 1024:.1f} MB exceeds "
                f"{PEAK_CAP_MB} MB cap: JsonWriter is buffering rows",
            )

            code_parallel = _convert(input_path, out_parallel, workers=2)
            self.assertEqual(code_parallel, 0)

            # Determinism: serial vs parallel byte-identical (streamed md5).
            self.assertEqual(_md5(out_serial), _md5(out_parallel))

            # Array envelope without loading the whole file.
            with open(out_serial, "rb") as fh:
                self.assertEqual(fh.read(1), b"[")
                fh.seek(-2, os.SEEK_END)
                self.assertEqual(fh.read(), b"]\n")

            # Full validity + exact row count + fixture spot-checks.
            with open(out_serial, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(len(rows), N_ROWS)
            for i, expected_id in enumerate(EXPECTED_IDS):
                self.assertEqual(rows[i]["id"], expected_id)
            self.assertEqual(rows[-1]["id"], EXPECTED_IDS[(N_ROWS - 1) % 3])
            self.assertEqual(rows[0]["date"], "2024-08-15")
            self.assertEqual(rows[0]["amount"], 1234.56)
            self.assertEqual(rows[0]["currency"], "USD")

    def test_zero_rows_produces_empty_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, "empty.txt")
            open(input_path, "wb").close()
            out_path = os.path.join(tmp, "empty.json")
            self.assertEqual(_convert(input_path, out_path, workers=1), 0)
            with open(out_path, encoding="utf-8") as fh:
                raw = fh.read()
            self.assertEqual(raw.strip(), "[]")
            self.assertEqual(json.loads(raw), [])


if __name__ == "__main__":
    unittest.main()
