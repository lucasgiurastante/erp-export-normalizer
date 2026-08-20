"""Phase 3 tests: business rules, audit checksums, schema registry."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest

from cli import main
from core import rules
from tests.test_mvp import REC_OK, write_schema

BALANCE_SCHEMA = {
    "format": "jde_fixed_width",
    "version": "1.0.0",
    "record_length": 24,
    "codepage": "utf-8",
    "rules": [{"type": "balance", "positive": "debit", "negative": "credit"}],
    "fields": [
        {
            "name": "debit",
            "start": 0,
            "length": 12,
            "type": "decimal",
            "scale": 2,
            "align": "right",
        },
        {
            "name": "credit",
            "start": 12,
            "length": 12,
            "type": "decimal",
            "scale": 2,
            "align": "right",
        },
    ],
}


def brec(debit: str, credit: str) -> bytes:
    return (debit.rjust(12) + credit.rjust(12)).encode()


def _write_schema_dict(tmp: str, data: dict) -> str:
    import yaml

    path = os.path.join(tmp, "schema.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    return path


def _convert(
    tmp: str, schema_path: str, records: list[bytes], extra: list[str] | None = None
):
    input_path = os.path.join(tmp, "in.txt")
    with open(input_path, "wb") as fh:
        fh.writelines(r + b"\n" for r in records)
    out_path = os.path.join(tmp, "out.json")
    args = [
        "--schema",
        schema_path,
        "--input",
        input_path,
        "--output",
        out_path,
        "--format",
        "json",
    ] + (extra or [])
    return main(args), input_path, out_path


class TestRules(unittest.TestCase):
    def test_sum_rule_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = _write_schema_dict(
                tmp,
                {
                    "format": "jde_fixed_width",
                    "version": "1.0.0",
                    "record_length": 44,
                    "codepage": "cp850",
                    "rules": [{"type": "sum", "field": "amount", "expected": 123.45}],
                    "fields": [
                        {"name": "id", "start": 0, "length": 10},
                        {"name": "type", "start": 10, "length": 15},
                        {
                            "name": "date",
                            "start": 25,
                            "length": 8,
                            "type": "date",
                            "format": "YYYYMMDD",
                        },
                        {
                            "name": "amount",
                            "start": 33,
                            "length": 8,
                            "type": "decimal",
                            "scale": 2,
                            "align": "right",
                        },
                        {"name": "currency", "start": 41, "length": 3},
                    ],
                },
            )
            code, _, _ = _convert(tmp, schema_path, [REC_OK])
            self.assertEqual(code, 0)

    def test_sum_rule_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = _write_schema_dict(
                tmp,
                {
                    "format": "jde_fixed_width",
                    "version": "1.0.0",
                    "record_length": 44,
                    "codepage": "cp850",
                    "rules": [{"type": "sum", "field": "amount", "expected": 999}],
                    "fields": [
                        {"name": "id", "start": 0, "length": 10},
                        {"name": "type", "start": 10, "length": 15},
                        {
                            "name": "date",
                            "start": 25,
                            "length": 8,
                            "type": "date",
                            "format": "YYYYMMDD",
                        },
                        {
                            "name": "amount",
                            "start": 33,
                            "length": 8,
                            "type": "decimal",
                            "scale": 2,
                            "align": "right",
                        },
                        {"name": "currency", "start": 41, "length": 3},
                    ],
                },
            )
            code, _, _ = _convert(tmp, schema_path, [REC_OK])
            self.assertEqual(code, 3)

    def test_balance_rule_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = _write_schema_dict(tmp, BALANCE_SCHEMA)
            code, _, _ = _convert(tmp, schema_path, [brec("100.00", "100.00")])
            self.assertEqual(code, 0)

    def test_balance_rule_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = _write_schema_dict(tmp, BALANCE_SCHEMA)
            code, _, _ = _convert(tmp, schema_path, [brec("100.00", "90.00")])
            self.assertEqual(code, 3)

    def test_balance_multiple_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = _write_schema_dict(tmp, BALANCE_SCHEMA)
            code, _, _ = _convert(
                tmp, schema_path, [brec("60.00", "40.00"), brec("40.00", "60.00")]
            )
            self.assertEqual(code, 0)

    def test_invalid_rule_type_schema_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = _write_schema_dict(
                tmp,
                {
                    "format": "jde_fixed_width",
                    "version": "1.0.0",
                    "record_length": 44,
                    "codepage": "cp850",
                    "rules": [{"type": "magic"}],
                    "fields": [
                        {"name": "id", "start": 0, "length": 10},
                    ],
                },
            )
            code, _, _ = _convert(tmp, bad, [REC_OK])
            self.assertEqual(code, 2)

    def test_rule_engine_direct(self):
        import decimal

        engine = rules.RuleEngine(({"type": "sum", "field": "amount", "expected": 10},))
        engine.observe({"amount": decimal.Decimal(6)})
        engine.observe({"amount": decimal.Decimal(4)})
        self.assertEqual(engine.finalize(), [])
        engine.observe({"amount": decimal.Decimal(1)})
        self.assertEqual(len(engine.finalize()), 1)


class TestChecksum(unittest.TestCase):
    def test_checksum_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = write_schema(tmp)
            code, input_path, out_path = _convert(
                tmp, schema_path, [REC_OK], extra=["--checksum"]
            )
            self.assertEqual(code, 0)
            sidecar = out_path + ".sha256"
            self.assertTrue(os.path.exists(sidecar))
            with open(sidecar, encoding="utf-8") as fh:
                content = fh.read()
            with open(input_path, "rb") as fh:
                expected = hashlib.sha256(fh.read()).hexdigest()
            self.assertIn(f"input_sha256={expected}", content)
            self.assertIn("schema_version=1.0.0", content)
            self.assertIn("records_ok=1", content)
            self.assertIn("utc_timestamp=", content)
            self.assertIn("output_sha256=", content)


class TestRegistry(unittest.TestCase):
    def test_registry_verify_ok(self):
        formats_dir = os.path.join(os.path.dirname(__file__), "..", "formats")
        code = main(["registry", os.path.abspath(formats_dir)])
        self.assertEqual(code, 0)

    def test_registry_reports_invalid_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "broken.yaml"), "w", encoding="utf-8") as fh:
                fh.write("format: x\n")
            code = main(["registry", tmp])
            self.assertEqual(code, 2)

    def test_registry_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(["registry", tmp])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
