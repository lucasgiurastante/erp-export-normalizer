"""Phase 2 tests: delimited parsing, schema generation, batch globbing,
parallel validation, DataFrame integration."""
from __future__ import annotations

import decimal
import json
import os
import tempfile
import unittest

from cli import main
from core import generator, schema as schema_mod
from tests.test_mvp import rec, write_schema

try:
    import pandas  # noqa: F401
except ImportError:
    pandas = None

try:
    import polars  # noqa: F401
except ImportError:
    polars = None

CSV_HEADER = "date,amount,customer,currency\n"
CSV_ROWS = [
    "20250115,1234.50,CUST A,USD",
    "20250220,567.00,CUST B,EUR",
    "20250310,89.25,CUST C,ARS",
]


def _write(tmp: str, name: str, content: str) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class TestDelimited(unittest.TestCase):
    def _generate_and_convert(self, tmp: str, csv: str):
        in_path = _write(tmp, "data.csv", csv)
        schema_path = os.path.join(tmp, "gen.yaml")
        code = main(["generate-schema", in_path, "--output", schema_path])
        self.assertEqual(code, 0)
        out_path = os.path.join(tmp, "out.json")
        code = main([
            "--schema", schema_path,
            "--input", in_path,
            "--output", out_path,
            "--format", "json",
        ])
        return code, out_path

    def test_generate_infers_types_and_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = _write(tmp, "data.csv", CSV_HEADER + "\n".join(CSV_ROWS) + "\n")
            schema_path = os.path.join(tmp, "gen.yaml")
            code = main(["generate-schema", in_path, "--output", schema_path])
            self.assertEqual(code, 0)
            sch = schema_mod.load_schema(schema_path)
            self.assertEqual(sch.format, "delimited")
            self.assertEqual(sch.delimiter, ",")
            self.assertTrue(sch.has_header)
            by_name = {f.name: f for f in sch.fields}
            self.assertEqual(by_name["date"].type, "date")
            self.assertEqual(by_name["date"].format, "YYYYMMDD")
            self.assertEqual(by_name["amount"].type, "decimal")
            self.assertEqual(by_name["amount"].scale, 2)
            self.assertEqual(by_name["customer"].type, "string")

    def test_generate_then_convert(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out_path = self._generate_and_convert(
                tmp, CSV_HEADER + "\n".join(CSV_ROWS) + "\n"
            )
            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["date"], "2025-01-15")
            self.assertEqual(rows[0]["amount"], 1234.5)

    def test_generate_no_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = _write(tmp, "data.csv", "\n".join(CSV_ROWS) + "\n")
            schema_path = os.path.join(tmp, "gen.yaml")
            code = main([
                "generate-schema", in_path, "--output", schema_path, "--no-header",
            ])
            self.assertEqual(code, 0)
            sch = schema_mod.load_schema(schema_path)
            self.assertFalse(sch.has_header)
            self.assertEqual(sch.fields[0].name, "column_0")
            out_path = os.path.join(tmp, "out.json")
            code = main([
                "--schema", schema_path,
                "--input", in_path,
                "--output", out_path,
                "--format", "json",
            ])
            self.assertEqual(code, 0)

    def test_generate_rejects_no_delimiter(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = _write(tmp, "data.txt", "0123456789ABC\n")
            schema_path = os.path.join(tmp, "gen.yaml")
            code = main(["generate-schema", in_path, "--output", schema_path])
            self.assertEqual(code, 1)

    def test_delimited_missing_column_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            in_path = _write(tmp, "data.csv", CSV_HEADER)
            schema_path = os.path.join(tmp, "gen.yaml")
            main(["generate-schema", in_path, "--output", schema_path])
            bad = _write(tmp, "bad.csv", CSV_HEADER + "20250115,1234.50,CUST A\n")
            out_path = os.path.join(tmp, "out.json")
            code = main([
                "--schema", schema_path,
                "--input", bad,
                "--output", out_path,
                "--format", "json",
            ])
            self.assertEqual(code, 3)


class TestBatch(unittest.TestCase):
    def test_glob_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = write_schema(tmp)
            os.makedirs(os.path.join(tmp, "in"), exist_ok=True)
            _write(tmp, "in/a.txt", "")
            with open(os.path.join(tmp, "in", "a.txt"), "wb") as fh:
                fh.write(rec("1", "A", "20250115", "100", "USD") + b"\n")
                fh.write(rec("2", "B", "20250116", "200", "USD") + b"\n")
            with open(os.path.join(tmp, "in", "b.txt"), "wb") as fh:
                fh.write(rec("3", "C", "20250117", "300", "USD") + b"\n")
            out_dir = os.path.join(tmp, "out")
            os.makedirs(out_dir)
            code = main([
                "--schema", schema_path,
                "--input", os.path.join(tmp, "in", "*.txt"),
                "--output", os.path.join(tmp, "unused.json"),
                "--output-dir", out_dir,
                "--format", "json",
            ])
            self.assertEqual(code, 0)
            for name, expected in (("a.json", 2), ("b.json", 1)):
                with open(os.path.join(out_dir, name), encoding="utf-8") as fh:
                    rows = json.load(fh)
                self.assertEqual(len(rows), expected)

    def test_glob_requires_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = write_schema(tmp)
            os.makedirs(os.path.join(tmp, "in"), exist_ok=True)
            _write(tmp, "in/a.txt", "")
            _write(tmp, "in/b.txt", "")
            code = main([
                "--schema", schema_path,
                "--input", os.path.join(tmp, "in", "*.txt"),
                "--output", os.path.join(tmp, "x.json"),
            ])
            self.assertEqual(code, 1)

    def test_glob_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main([
                "--schema", write_schema(tmp),
                "--input", os.path.join(tmp, "nope", "*.txt"),
                "--output", os.path.join(tmp, "x.json"),
            ])
            self.assertEqual(code, 1)


class TestParallel(unittest.TestCase):
    def test_parallel_matches_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = write_schema(tmp)
            input_path = os.path.join(tmp, "in.txt")
            with open(input_path, "wb") as fh:
                for i in range(200):
                    fh.write(rec(f"{i:08d}", f"C{i}", "20250115", str(i), "USD") + b"\n")
            serial_out = os.path.join(tmp, "serial.json")
            code = main([
                "--schema", schema_path,
                "--input", input_path,
                "--output", serial_out,
                "--format", "json",
            ])
            self.assertEqual(code, 0)
            par_out = os.path.join(tmp, "parallel.json")
            code = main([
                "--schema", schema_path,
                "--input", input_path,
                "--output", par_out,
                "--format", "json",
                "--workers", "2",
            ])
            self.assertEqual(code, 0)
            with open(serial_out, encoding="utf-8") as fh:
                serial = fh.read()
            with open(par_out, encoding="utf-8") as fh:
                parallel = fh.read()
            self.assertEqual(serial, parallel)  # deterministic output


class TestIo(unittest.TestCase):
    @unittest.skipUnless(pandas, "pandas not installed")
    def test_read_erp_pandas(self):
        from core.io import read_erp

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "in.txt")
            with open(in_path, "wb") as fh:
                fh.write(rec("1", "A", "20250115", "12345", "USD") + b"\n")
                fh.write(rec("2", "B", "20250116", "67890", "EUR") + b"\n")
            df = read_erp(in_path, schema=write_schema(tmp))
            self.assertEqual(list(df.columns), ["id", "type", "date", "amount", "currency"])
            self.assertEqual(df.iloc[0]["date"], "2025-01-15")
            self.assertEqual(df.iloc[1]["amount"], decimal.Decimal("678.90"))

    @unittest.skipUnless(pandas, "pandas not installed")
    def test_read_erp_raises_on_errors(self):
        from core.io import ErpValidationError, read_erp

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "in.txt")
            with open(in_path, "wb") as fh:
                fh.write(rec("1", "A", "20251301", "12345", "USD") + b"\n")
            with self.assertRaises(ErpValidationError):
                read_erp(in_path, schema=write_schema(tmp))

    @unittest.skipUnless(polars, "polars not installed")
    def test_read_erp_polars(self):
        from core.io import read_erp

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "in.txt")
            with open(in_path, "wb") as fh:
                fh.write(rec("1", "A", "20250115", "12345", "USD") + b"\n")
            df = read_erp(in_path, schema=write_schema(tmp), backend="polars")
            self.assertEqual(df.height, 1)
            self.assertEqual(df["date"][0], "2025-01-15")


if __name__ == "__main__":
    unittest.main()