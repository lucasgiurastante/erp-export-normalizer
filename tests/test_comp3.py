"""COMP-3 (packed decimal) support: BCD decoding, schema validation, CLI."""

from __future__ import annotations

import contextlib
import decimal
import io
import json
import os
import tempfile
import unittest

from cli import main
from core import converters
from core.schema import SchemaError, build_schema
from core.validator import Validator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, "core", "formats", "cobol_packed.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "cobol_packed.txt")


def pack(digits: str, nbytes: int, negative: bool = False) -> bytes:
    """Reference COMP-3 encoder used to build fixtures inside tests."""
    ndigits = nbytes * 2 - 1
    text = digits.zfill(ndigits)
    sign = 0x0D if negative else 0x0C
    nibs = [int(c) for c in text] + [sign]
    if len(nibs) % 2:
        nibs.insert(0, 0)
    return bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, len(nibs), 2))


def packed_field(**kwargs):
    data = {
        "name": "amount",
        "start": 0,
        "length": 5,
        "type": "packed",
        "scale": 2,
    }
    data.update(kwargs)
    return build_schema(
        {"format": "t", "version": "1.0.0", "record_length": 5, "fields": [data]}
    ).fields[0]


class TestConvertPacked(unittest.TestCase):
    def test_positive_with_scale(self):
        # 9 digits, scale 2 -> 1234567.99
        got = converters.convert_packed(pack("123456799", 5), packed_field())
        self.assertEqual(got, decimal.Decimal("1234567.99"))

    def test_negative_sign_nibble_0x0d(self):
        got = converters.convert_packed(
            pack("000000042", 5, negative=True), packed_field()
        )
        self.assertEqual(got, decimal.Decimal("-0.42"))

    def test_unsigned_positive_nibble_0x0f(self):
        field = packed_field()
        raw = bytearray(pack("000123456", 5))
        raw[-1] = (raw[-1] & 0xF0) | 0x0F  # 0xF = unsigned positive
        self.assertEqual(
            converters.convert_packed(bytes(raw), field), decimal.Decimal("1234.56")
        )

    def test_zero_scale_is_integer(self):
        field = packed_field(scale=0, length=4)
        got = converters.convert_packed(pack("0042", 4), field)
        self.assertEqual(got, decimal.Decimal("42"))

    def test_empty_field_raises(self):
        with self.assertRaises(converters.ConversionError) as ctx:
            converters.convert_packed(b"", packed_field())
        self.assertIn("empty packed decimal", str(ctx.exception))

    def test_invalid_sign_nibble_raises(self):
        raw = bytearray(pack("123456789", 5))
        raw[-1] = (raw[-1] & 0xF0) | 0x07  # not a sign nibble
        with self.assertRaises(converters.ConversionError) as ctx:
            converters.convert_packed(bytes(raw), packed_field())
        self.assertIn("sign nibble 0x7", str(ctx.exception))

    def test_invalid_digit_nibble_raises(self):
        with self.assertRaises(converters.ConversionError) as ctx:
            converters.convert_packed(bytes.fromhex("ab0cde0cff"), packed_field())
        self.assertIn("digit nibble 0xA", str(ctx.exception))

    def test_convert_field_does_not_decode_through_codepage(self):
        """A packed field is binary: it must bypass EBCDIC/text decoding."""
        field = packed_field()
        got = converters.convert_field(
            pack("000000042", 5, negative=True), field, "ebcdic-cp037"
        )
        self.assertEqual(got, decimal.Decimal("-0.42"))


class TestPackedSchemaValidation(unittest.TestCase):
    def test_requires_scale(self):
        with self.assertRaises(SchemaError) as ctx:
            build_schema(
                {
                    "format": "t",
                    "version": "1.0.0",
                    "record_length": 5,
                    "fields": [
                        {"name": "a", "start": 0, "length": 5, "type": "packed"}
                    ],
                }
            )
        self.assertIn("requires 'scale'", str(ctx.exception))

    def test_rejects_date_format(self):
        with self.assertRaises(SchemaError) as ctx:
            build_schema(
                {
                    "format": "t",
                    "version": "1.0.0",
                    "record_length": 5,
                    "fields": [
                        {
                            "name": "a",
                            "start": 0,
                            "length": 5,
                            "type": "packed",
                            "scale": 2,
                            "format": "YYYYMMDD",
                        }
                    ],
                }
            )
        self.assertIn("does not accept 'format'", str(ctx.exception))


class TestPackedValidator(unittest.TestCase):
    def test_error_report_shows_hex_raw(self):
        """Packed fields are binary: the report must show hex, not mojibake."""
        schema = build_schema(
            {
                "format": "t",
                "version": "1.0.0",
                "record_length": 5,
                "fields": [
                    {
                        "name": "amount",
                        "start": 0,
                        "length": 5,
                        "type": "packed",
                        "scale": 2,
                    }
                ],
            }
        )
        result = Validator(schema).validate_record(1, bytes.fromhex("ab0cde0cff"))
        self.assertFalse(result.ok)
        self.assertIn("raw='ab0cde0cff'", result.errors[0])


class TestPackedCli(unittest.TestCase):
    def _convert(self, fmt: str = "json", extra: list[str] | None = None) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out." + fmt)
            code = main(
                [
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    FIXTURE,
                    "--output",
                    out,
                    "--format",
                    fmt,
                    *(extra or []),
                ]
            )
            self.assertEqual(code, 3, "fixture has 1 intentional bad BCD record")
            with open(out, encoding="utf-8") as fh:
                if fmt == "json":
                    return json.load(fh)
                with contextlib.suppress(OSError):
                    os.unlink(out)
                return []

    def test_json_fixture_values(self):
        rows = self._convert("json")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["record_key"], "000001")
        self.assertEqual(rows[0]["customer_name"], "ACME CORP")
        self.assertEqual(rows[0]["balance"], 1234567.99)
        self.assertEqual(rows[0]["activity_date"], "2025-01-15")
        self.assertEqual(rows[1]["balance"], -0.42)
        self.assertEqual(rows[2]["balance"], 9999999.99)
        self.assertEqual(rows[3]["balance"], 0.0)

    def test_parallel_output_identical(self):
        """Packed decoding must stay deterministic under --workers."""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.json")
            b = os.path.join(tmp, "b.json")
            for out, workers in ((a, "1"), (b, "4")):
                main(
                    [
                        "--schema",
                        SCHEMA_PATH,
                        "--input",
                        FIXTURE,
                        "--output",
                        out,
                        "--format",
                        "json",
                        "--workers",
                        workers,
                    ]
                )
            with open(a, "rb") as fa, open(b, "rb") as fb:
                self.assertEqual(fa.read(), fb.read())

    def test_error_line_format_preserved(self):
        """Scripts parse 'line N:'; the prefix must survive (P1-3 contract)."""
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(err):
            main(
                [
                    "--schema",
                    SCHEMA_PATH,
                    "--input",
                    FIXTURE,
                    "--output",
                    os.path.join(tmp, "out.json"),
                    "--format",
                    "json",
                ]
            )
        text = err.getvalue()
        self.assertIn("line 5:", text)
        self.assertIn("field 'balance'", text)
        self.assertIn("digit nibble 0xA", text)


if __name__ == "__main__":
    unittest.main()
