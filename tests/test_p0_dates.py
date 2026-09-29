"""P0-2 date formats: convert_date valid/invalid + fixtures + schema gate."""

from __future__ import annotations

import os
import unittest

from core import converters
from core import schema as schema_mod

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


def fixture_lines(name: str) -> list[str]:
    path = os.path.join(EXAMPLES_DIR, name)
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh.read().splitlines() if ln.strip()]


class TestConvertDateValid(unittest.TestCase):
    def test_yyyymmdd_regression(self):
        self.assertEqual(converters.convert_date("20250115", "YYYYMMDD"), "2025-01-15")

    def test_yyyy_mm_dd(self):
        self.assertEqual(
            converters.convert_date("2025-01-15", "YYYY-MM-DD"), "2025-01-15"
        )

    def test_ddmmyyyy(self):
        self.assertEqual(converters.convert_date("15012025", "DDMMYYYY"), "2025-01-15")

    def test_dd_slash_mm_slash_yyyy(self):
        self.assertEqual(
            converters.convert_date("15/01/2025", "DD/MM/YYYY"), "2025-01-15"
        )

    def test_yymmdd(self):
        self.assertEqual(converters.convert_date("250115", "YYMMDD"), "2025-01-15")


class TestConvertDateInvalid(unittest.TestCase):
    def _assert_invalid(self, value: str, fmt: str):
        with self.assertRaises(converters.ConversionError) as ctx:
            converters.convert_date(value, fmt)
        self.assertIn(fmt, str(ctx.exception))

    def test_yyyymmdd_invalid(self):
        self._assert_invalid("20251301", "YYYYMMDD")

    def test_yyyy_mm_dd_invalid(self):
        self._assert_invalid("2025-13-01", "YYYY-MM-DD")

    def test_ddmmyyyy_invalid(self):
        self._assert_invalid("32122025", "DDMMYYYY")

    def test_dd_slash_mm_slash_yyyy_invalid(self):
        self._assert_invalid("32/12/2025", "DD/MM/YYYY")

    def test_yymmdd_invalid(self):
        self._assert_invalid("251301", "YYMMDD")


class TestDateFixtures(unittest.TestCase):
    def _assert_fixture(self, filename: str, fmt: str, expected: list[str]):
        lines = fixture_lines(filename)
        self.assertEqual(len(lines), 3, f"{filename} must hold 3 valid lines")
        got = [converters.convert_date(ln, fmt) for ln in lines]
        self.assertEqual(got, expected)

    def test_fixture_yyyymmdd(self):
        self._assert_fixture(
            "date_yyyymmdd.txt", "YYYYMMDD", ["2025-01-15", "2024-08-22", "1999-12-31"]
        )

    def test_fixture_yyyy_mm_dd(self):
        self._assert_fixture(
            "date_yyyy-mm-dd.txt",
            "YYYY-MM-DD",
            ["2025-01-15", "2024-08-22", "1999-12-31"],
        )

    def test_fixture_ddmmyyyy(self):
        self._assert_fixture(
            "date_ddmmyyyy.txt", "DDMMYYYY", ["2025-01-15", "2024-08-22", "1999-12-31"]
        )

    def test_fixture_dd_slash_mm_slash_yyyy(self):
        self._assert_fixture(
            "date_ddmmyyyy_slash.txt",
            "DD/MM/YYYY",
            ["2025-01-15", "2024-08-22", "1999-12-31"],
        )

    def test_fixture_yymmdd(self):
        self._assert_fixture(
            "date_yymmdd.txt", "YYMMDD", ["2025-01-15", "2024-08-22", "1999-12-31"]
        )


class TestSchemaDateGate(unittest.TestCase):
    def test_build_schema_rejects_unsupported_date_format(self):
        data = {
            "format": "test_fixed",
            "version": "1.0.0",
            "record_length": 10,
            "fields": [
                {
                    "name": "d",
                    "start": 0,
                    "length": 10,
                    "type": "date",
                    "format": "MM-DD-YYYY",
                },
            ],
        }
        with self.assertRaises(schema_mod.SchemaError) as ctx:
            schema_mod.build_schema(data)
        self.assertIn("unsupported date format", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
