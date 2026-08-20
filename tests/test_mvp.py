"""Tests MVP Fase 0: fixed-width → JSON/CSV con schema YAML."""
from __future__ import annotations

import decimal
import json
import os
import tempfile
import unittest

from core import converters, parser, schema as schema_mod, validator

from cli import main

SAMPLE_SCHEMA = {
    "format": "jde_fixed_width",
    "version": "1.0.0",
    "record_length": 44,
    "codepage": "cp850",
    "fields": [
        {"name": "id", "start": 0, "length": 10},
        {"name": "tipo", "start": 10, "length": 15},
        {"name": "fecha", "start": 25, "length": 8, "type": "date", "format": "YYYYMMDD"},
        {"name": "monto", "start": 33, "length": 8, "type": "decimal", "scale": 2, "align": "right"},
        {"name": "moneda", "start": 41, "length": 3},
    ],
}

def rec(id_: str, cliente: str, fecha: str, monto: str, moneda: str) -> bytes:
    return (
        id_[:10].ljust(10)
        + cliente[:15].ljust(15)
        + fecha
        + monto.rjust(8)
        + moneda[:3].ljust(3)
    ).encode()


REC_OK = rec("0000000001", "CLIENTE X", "20250115", "12345", "USD")
REC_BAD_DATE = rec("0000000002", "CLIENTE Y", "20251301", "10000", "EUR")
REC_SHORT = b"0000000003CORTADO"


def write_schema(tmp: str) -> str:
    import yaml

    path = os.path.join(tmp, "schema.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(SAMPLE_SCHEMA, fh)
    return path


class TestSchema(unittest.TestCase):
    def test_ok(self):
        sch = schema_mod.build_schema(SAMPLE_SCHEMA)
        self.assertEqual(sch.record_length, 44)
        self.assertEqual(sch.version, "1.0.0")

    def test_overflow_rechazado(self):
        data = dict(SAMPLE_SCHEMA, fields=[
            {"name": "id", "start": 40, "length": 10},
        ])
        with self.assertRaises(schema_mod.SchemaError) as ctx:
            schema_mod.build_schema(data)
        self.assertIn("desborda", str(ctx.exception))

    def test_overlap_rechazado(self):
        data = dict(SAMPLE_SCHEMA, fields=[
            {"name": "a", "start": 0, "length": 10},
            {"name": "b", "start": 5, "length": 10},
        ])
        with self.assertRaises(schema_mod.SchemaError) as ctx:
            schema_mod.build_schema(data)
        self.assertIn("overlap", str(ctx.exception))

    def test_falta_version(self):
        data = {k: v for k, v in SAMPLE_SCHEMA.items() if k != "version"}
        with self.assertRaises(schema_mod.SchemaError):
            schema_mod.build_schema(data)


class TestConverters(unittest.TestCase):
    def test_fecha_iso(self):
        self.assertEqual(converters.convert_date("20250115", "YYYYMMDD"), "2025-01-15")

    def test_fecha_invalida(self):
        with self.assertRaises(converters.ConversionError):
            converters.convert_date("20251301", "YYYYMMDD")

    def test_decimal_scale(self):
        field = schema_mod.Field(name="monto", start=0, length=8, type="decimal", scale=2, align="right")
        self.assertEqual(converters.convert_decimal("    12345", field), decimal.Decimal("123.45"))

    def test_decimal_signo_final(self):
        field = schema_mod.Field(name="monto", start=0, length=8, type="decimal", scale=2, align="right")
        self.assertEqual(converters.convert_decimal("  12345-", field), decimal.Decimal("-123.45"))


class TestPipeline(unittest.TestCase):
    def _run(self, tmp: str, records: list[bytes], fmt: str, dry_run: bool = False):
        schema_path = write_schema(tmp)
        input_path = os.path.join(tmp, "input.txt")
        with open(input_path, "wb") as fh:
            for r in records:
                fh.write(r + b"\n")
        output_path = os.path.join(tmp, f"out.{fmt}")
        code = main([
            "--schema", schema_path,
            "--input", input_path,
            "--output", output_path,
            "--format", fmt,
        ] + (["--dry-run"] if dry_run else []))
        return code, output_path

    def test_json_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out_path = self._run(tmp, [REC_OK], "json")
            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(rows[0]["id"], "0000000001")
            self.assertEqual(rows[0]["fecha"], "2025-01-15")
            self.assertEqual(rows[0]["monto"], 123.45)
            self.assertEqual(rows[0]["moneda"], "USD")

    def test_csv_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out_path = self._run(tmp, [REC_OK], "csv")
            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            self.assertEqual(lines[0], "id,tipo,fecha,monto,moneda")
            self.assertIn("2025-01-15", lines[1])
            self.assertIn("123.45", lines[1])

    def test_validacion_acumulativa(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self._run(tmp, [REC_OK, REC_BAD_DATE, REC_SHORT], "json")
            self.assertEqual(code, 3)  # EXIT_VALIDATION
            with open(os.path.join(tmp, "out.json"), encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(len(rows), 1)  # solo la fila válida

    def test_dry_run_sin_salida(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out_path = self._run(tmp, [REC_OK, REC_BAD_DATE], "json", dry_run=True)
            self.assertEqual(code, 3)
            self.assertFalse(os.path.exists(out_path))

    def test_schema_invalido_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.yaml")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("format: x\n")  # sin version/record_length/fields
            code = main(["--schema", bad, "--input", "x", "--output", "y"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()