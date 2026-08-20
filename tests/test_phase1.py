"""Phase 1 tests: NDJSON/SQL/Parquet/Excel writers, auto-detection, --verbose."""

from __future__ import annotations

import contextlib
import decimal
import io
import json
import os
import sqlite3
import tempfile
import unittest

from cli import main
from core import detector as detector_mod
from tests.test_mvp import REC_BAD_DATE, REC_OK, rec, write_schema

try:
    import pyarrow
except ImportError:
    pyarrow = None

try:
    import openpyxl
except ImportError:
    openpyxl = None

REC_QUOTE = rec("0000000004", "O'BRIEN CO", "20250210", "-9900", "ARS")


def ext_for(fmt: str) -> str:
    return {"excel": "xlsx"}.get(fmt, fmt)


class TestWriters(unittest.TestCase):
    def _run(
        self, tmp: str, records: list[bytes], fmt: str, extra: list[str] | None = None
    ):
        schema_path = write_schema(tmp)
        input_path = os.path.join(tmp, "input.txt")
        with open(input_path, "wb") as fh:
            fh.writelines(r + b"\n" for r in records)
        out_path = os.path.join(tmp, f"out.{ext_for(fmt)}")
        args = [
            "--schema",
            schema_path,
            "--input",
            input_path,
            "--output",
            out_path,
            "--format",
            fmt,
        ] + (extra or [])
        return main(args), out_path

    def test_ndjson(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(tmp, [REC_OK], "ndjson")
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                row = json.loads(fh.readline())
            self.assertEqual(row["date"], "2025-01-15")
            self.assertEqual(row["amount"], 123.45)

    def test_ndjson_multiple_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(tmp, [REC_OK, REC_OK], "ndjson")
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(len(lines), 2)

    def test_sql_insert(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(tmp, [REC_OK], "sql")
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                sql = fh.read()
            self.assertIn('INSERT INTO "export"', sql)
            self.assertIn("'2025-01-15'", sql)
            # generated SQL must execute against SQLite
            conn = sqlite3.connect(":memory:")
            conn.execute(
                'CREATE TABLE "export" '
                '("id" TEXT, "type" TEXT, "date" TEXT, '
                '"amount" NUMERIC, "currency" TEXT)'
            )
            conn.execute(sql)
            self.assertEqual(
                conn.execute('SELECT "date", "amount" FROM "export"').fetchone(),
                ("2025-01-15", 123.45),
            )

    def test_sql_escapes_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(tmp, [REC_QUOTE], "sql")
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                sql = fh.read()
            self.assertIn("O''BRIEN", sql)

    @unittest.skipUnless(pyarrow, "pyarrow not installed")
    def test_parquet(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(tmp, [REC_OK], "parquet")
            self.assertEqual(code, 0)
            import pyarrow.parquet as pq

            table = pq.read_table(out)
            self.assertEqual(table.num_rows, 1)
            self.assertEqual(table.column("date")[0].as_py(), "2025-01-15")
            self.assertEqual(
                table.column("amount")[0].as_py(), decimal.Decimal("123.45")
            )

    @unittest.skipUnless(openpyxl, "openpyxl not installed")
    def test_excel(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(tmp, [REC_OK], "excel")
            self.assertEqual(code, 0)
            wb = openpyxl.load_workbook(out)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            self.assertEqual(rows[0][0], "id")
            self.assertEqual(rows[1][2], "2025-01-15")
            self.assertEqual(float(rows[1][3]), 123.45)


class TestDetector(unittest.TestCase):
    def _write_input(self, tmp: str, records: list[bytes]) -> str:
        path = os.path.join(tmp, "input.txt")
        with open(path, "wb") as fh:
            fh.writelines(r + b"\n" for r in records)
        return path

    def test_detector_scores_schemas(self):
        det = detector_mod.Detector()
        candidates = det._candidates()
        names = [os.path.basename(p) for p, _ in candidates]
        self.assertIn("jde_ar.yaml", names)
        self.assertIn("sap_batch.yaml", names)

    def test_detect_picks_jde(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = self._write_input(tmp, [REC_OK])
            found = detector_mod.Detector().detect(in_path)
            self.assertIsNotNone(found)
            self.assertEqual(os.path.basename(found.source_path), "jde_ar.yaml")
            self.assertGreater(found.score, 0)

    def test_detect_no_match_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = self._write_input(tmp, [b"totally unrelated payload line"])
            self.assertIsNone(detector_mod.Detector().detect(in_path))

    def test_cli_autodetect(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = self._write_input(tmp, [REC_OK])
            out = os.path.join(tmp, "out.json")
            code = main(["--input", in_path, "--output", out, "--format", "json"])
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(rows[0]["amount"], 123.45)


class TestVerbose(unittest.TestCase):
    def test_verbose_reports_every_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = write_schema(tmp)
            input_path = os.path.join(tmp, "input.txt")
            with open(input_path, "wb") as fh:
                fh.write(REC_OK + b"\n" + REC_BAD_DATE + b"\n")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                code = main(
                    [
                        "--schema",
                        schema_path,
                        "--input",
                        input_path,
                        "--output",
                        os.path.join(tmp, "out.json"),
                        "--format",
                        "json",
                        "--verbose",
                    ]
                )
            self.assertEqual(code, 3)
            diag = buf.getvalue()
            self.assertIn("line 1: OK", diag)
            self.assertIn("line 2: ERR", diag)


if __name__ == "__main__":
    unittest.main()
