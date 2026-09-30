"""Mainframe credit/debit signs: CR/DB suffixes and EBCDIC overpunch.

The trap this guards against is not a wrong conversion, it is a right-looking
conversion per row that makes the column total wrong. Every amount here looks
plausible on its own; only the sum reveals a sign flipped.
"""

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
from core.converters import ConversionError
from core.schema import SchemaError, build_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar_crdb.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar_crdb.txt")


def field(length: int = 10, **kwargs):
    data = {
        "name": "amount",
        "start": 0,
        "length": length,
        "type": "decimal",
        "scale": 2,
    }
    data.update(kwargs)
    return build_schema(
        {
            "format": "t",
            "version": "1.0.0",
            "record_length": length,
            "fields": [data],
        }
    ).fields[0]


def conv(raw: bytes, **kwargs):
    return converters.convert_field(raw, field(**kwargs), "utf-8")


class TestCreditDebitSuffix(unittest.TestCase):
    def test_cr_is_positive(self):
        self.assertEqual(conv(b"1234.56CR"), decimal.Decimal("1234.56"))

    def test_db_is_negative(self):
        self.assertEqual(conv(b"1234.56DB"), decimal.Decimal("-1234.56"))

    def test_lowercase_is_accepted(self):
        self.assertEqual(conv(b"1234.56cr"), decimal.Decimal("1234.56"))
        self.assertEqual(conv(b"1234.56db"), decimal.Decimal("-1234.56"))

    def test_works_with_a_trailing_minus_too(self):
        self.assertEqual(conv(b"1234.56-"), decimal.Decimal("-1234.56"))

    def test_plain_number_untouched(self):
        self.assertEqual(conv(b"1234.56  "), decimal.Decimal("1234.56"))
        self.assertEqual(conv(b"-1234.56 "), decimal.Decimal("-1234.56"))

    def test_scale_applies_after_the_suffix_is_stripped(self):
        """Fixed-width integer with scale: '0012345CR' -> 123.45."""
        self.assertEqual(conv(b"0012345CR", length=9), decimal.Decimal("123.45"))

    def test_suffix_only_is_an_error(self):
        with self.assertRaises(ConversionError):
            conv(b"CR       ")

    def test_garbage_still_errors(self):
        with self.assertRaises(ConversionError):
            conv(b"12AB56  ")

    def test_cannot_corrupt_valid_data(self):
        """No legitimate number ends in CR/DB, so nothing that used to work breaks."""
        for raw in (b"1234.56  ", b"0.00     ", b"-99.9   ", b"      "):
            try:
                conv(raw)
            except ConversionError as exc:
                self.assertNotIn("CR", str(exc))


class TestOverpunch(unittest.TestCase):
    def test_letter_carries_the_digit_and_the_sign(self):
        # A..I are +0..+9, J..R are -0..-9
        self.assertEqual(
            conv(b"12345I  ", length=7, overpunch=True), decimal.Decimal("1234.58")
        )
        self.assertEqual(
            conv(b"12345R  ", length=7, overpunch=True), decimal.Decimal("-1234.58")
        )

    def test_a_is_zero(self):
        self.assertEqual(
            conv(b"00000A  ", length=7, overpunch=True), decimal.Decimal("0.00")
        )

    def test_plain_number_still_works_with_overpunch_on(self):
        self.assertEqual(
            conv(b"12345   ", length=7, overpunch=True), decimal.Decimal("123.45")
        )

    def test_opt_in(self):
        """Without the flag a trailing letter is still an error."""
        with self.assertRaises(ConversionError):
            conv(b"12345I  ", length=7)

    def test_cr_is_not_mistaken_for_overpunch(self):
        """'CR' ends in 'R', which is overpunch for -8. Order matters."""
        self.assertEqual(conv(b"1234.56CR", overpunch=True), decimal.Decimal("1234.56"))

    def test_unknown_letter_errors(self):
        with self.assertRaises(ConversionError):
            conv(b"12345Z  ", length=7, overpunch=True)

    def test_schema_rejects_overpunch_on_non_decimal(self):
        with self.assertRaises(SchemaError) as ctx:
            build_schema(
                {
                    "format": "t",
                    "version": "1.0.0",
                    "record_length": 8,
                    "fields": [
                        {
                            "name": "a",
                            "start": 0,
                            "length": 8,
                            "type": "string",
                            "overpunch": True,
                        }
                    ],
                }
            )
        self.assertIn("only applies to type decimal", str(ctx.exception))

    def test_schema_rejects_non_boolean(self):
        with self.assertRaises(SchemaError) as ctx:
            build_schema(
                {
                    "format": "t",
                    "version": "1.0.0",
                    "record_length": 8,
                    "fields": [
                        {
                            "name": "a",
                            "start": 0,
                            "length": 8,
                            "type": "decimal",
                            "overpunch": "yes",
                        }
                    ],
                }
            )
        self.assertIn("overpunch must be a boolean", str(ctx.exception))


class TestTheSumIsWhatMatters(unittest.TestCase):
    """Per-row conversions can all look right and still total wrong."""

    def test_mixed_signs_total_correctly(self):
        values = [
            conv(b"1234.56CR"),  # credit  +1234.56
            conv(b"0500.00DB"),  # debit   -500.00
            conv(b"0000.00CR"),  # credit     0.00
            conv(b"9999.99DB"),  # debit   -9999.99
        ]
        total = sum(values, decimal.Decimal(0))
        self.assertEqual(total, decimal.Decimal("-9265.43"))

    def test_cr_equals_plain_and_db_is_its_negative(self):
        self.assertEqual(conv(b"1234.56  "), conv(b"1234.56CR"))
        self.assertEqual(conv(b"0500.00  "), -conv(b"0500.00DB"))


class TestCrdbFixture(unittest.TestCase):
    def _convert(self) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o.json")
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        SCHEMA,
                        "--input",
                        FIXTURE,
                        "--output",
                        out,
                        "--format",
                        "json",
                    ]
                )
            # 1 intentional bad amount
            self.assertEqual(code, 3)
            self.assertIn("line 5:", err.getvalue())
            with open(out, encoding="utf-8") as fh:
                return json.load(fh)

    def test_rows_convert_with_the_right_sign(self):
        rows = self._convert()
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            [r["amount"] for r in rows],
            [1234.56, -500.00, 0.00, -9999.99],
        )

    def test_other_columns_are_intact(self):
        row = self._convert()[0]
        self.assertEqual(row["id"], "GL0000001")
        self.assertEqual(row["account"], "ACME CORP")
        self.assertEqual(row["date"], "2025-01-15")

    def test_column_total(self):
        total = sum(r["amount"] for r in self._convert())
        self.assertEqual(decimal.Decimal(str(total)), decimal.Decimal("-9265.43"))

    def test_deterministic_with_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            digests = []
            for workers in ("1", "4"):
                out = os.path.join(tmp, f"w{workers}.json")
                with (
                    contextlib.redirect_stderr(io.StringIO()),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    main(
                        [
                            "--schema",
                            SCHEMA,
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
                with open(out, "rb") as fh:
                    digests.append(fh.read())
            self.assertEqual(digests[0], digests[1])

    def test_shipped_schema_validates(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["registry", "validate", os.path.join(REPO_ROOT, "core", "formats")]
            )
        self.assertEqual(code, 0)

    def test_registry_index_needs_updating(self):
        """The new built-in must be in registry.yaml, or verify will miss it."""
        from core import registry

        index = os.path.join(REPO_ROOT, "registry.yaml")
        if not os.path.exists(index):
            self.skipTest("no registry index")
        names = {e.name for e in registry.load_index(index)}
        self.assertIn("jde_ar_crdb", names, "regenerate registry.yaml")


if __name__ == "__main__":
    unittest.main()
