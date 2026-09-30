"""Regression cases found by the property tests, kept as fixed examples.

A random example is not a regression test: the next run generates something
else. These are the concrete inputs that used to crash, written down so the
fix cannot be undone silently.
"""

from __future__ import annotations

import os
import unittest

from core import converters
from core.schema import build_schema
from core.validator import Validator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def schema(**field):
    base = {"name": "a", "start": 0, "length": 8, "type": "decimal", "scale": 2}
    base.update(field)
    return build_schema(
        {"format": "t", "version": "1.0.0", "record_length": 8, "fields": [base]}
    )


class TestUndecodableBytes(unittest.TestCase):
    """Found 2026-09-30: one bad byte killed the whole conversion.

    `decode_field` decoded strictly and let `UnicodeDecodeError` reach the
    caller, so a 44-byte record with a single 0x80 ended the run with a
    traceback instead of reporting one bad field.
    """

    CASES = [
        b"\x80",
        b"\x80\x00\x00\x00\x00\x00\x00\x00",
        b"\xff" * 8,
        b"1234.5\x80",
        b"\x00\x80\x00\x00\x00\x00\x00\x00",
    ]

    def test_reported_as_a_field_error(self):
        for raw in self.CASES:
            with self.subTest(raw=raw):
                field = schema().fields[0]
                with self.assertRaises(converters.ConversionError) as ctx:
                    converters.convert_field(raw, field, "utf-8")
                self.assertIn("not valid in", str(ctx.exception))

    def test_validation_returns_a_result(self):
        for raw in self.CASES:
            with self.subTest(raw=raw):
                result = Validator(schema()).validate_record(1, raw)
                self.assertFalse(result.ok)
                self.assertEqual(len(result.fields), 1)

    def test_the_message_names_the_offset(self):
        raw = b"1234\x80000"  # 8 bytes, byte 4 is the bad one
        result = Validator(schema()).validate_record(1, raw)
        self.assertIn("offset 4", result.errors[0])

    def test_field_name_is_not_duplicated(self):
        """The report adds `field 'a':`; the converter must not add it too."""
        result = Validator(schema()).validate_record(
            1, b"\x80\x00\x00\x00\x00\x00\x00\x00"
        )
        self.assertEqual(result.errors[0].count("field 'a'"), 1)


class TestValidBytesStillConvert(unittest.TestCase):
    def test_the_fix_did_not_break_good_input(self):
        for raw, expected in ((b"1234.56", "1234.56"), (b"    0.00", "0.00")):
            with self.subTest(raw=raw):
                field = schema().fields[0]
                self.assertEqual(
                    str(converters.convert_field(raw, field, "utf-8")), expected
                )

    def test_latin1_accepts_high_bytes(self):
        field = schema(type="string").fields[0]
        self.assertEqual(converters.convert_field(b"\xe9", field, "latin-1"), "é")


if __name__ == "__main__":
    unittest.main()
