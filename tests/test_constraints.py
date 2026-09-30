"""Per-field constraints: required, min, max, pattern, enum.

The distinction the RFC insists on: a value that could not be *converted*
is corrupt; one that converted but broke a *constraint* is out of policy.
Both are reported, worded so the operator can tell them apart.
"""

from __future__ import annotations

import contextlib
import decimal
import io
import os
import tempfile
import unittest

import yaml

from cli import main
from core import constraints
from core.schema import SchemaError, build_schema
from core.validator import Validator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")


def schema_with(**field):
    base = {"name": "a", "start": 0, "length": 12, "type": "string"}
    base.update(field)
    return build_schema(
        {"format": "t", "version": "1.0.0", "record_length": 12, "fields": [base]}
    ).fields[0]


class TestRequired(unittest.TestCase):
    def test_empty_value_fails(self):
        f = schema_with(required=True)
        self.assertIn("required field is empty", constraints.check(f, "   ", "   ")[0])

    def test_present_value_passes(self):
        f = schema_with(required=True)
        self.assertEqual(constraints.check(f, "ACME", "ACME"), [])

    def test_looks_at_the_text_not_the_converted_value(self):
        """A decimal of all spaces is 0, which is not 'missing'.

        Checking the converted value would let a blank amount pass as a
        genuine zero, which is precisely the bug this guards against.
        """
        f = schema_with(type="decimal", scale=2, required=True)
        self.assertTrue(constraints.check(f, decimal.Decimal(0), "        "))

    def test_respects_trim(self):
        f = schema_with(required=True, trim=True)
        self.assertEqual(constraints.check(f, "x", "  x  "), [])

    def test_stops_after_required_fails(self):
        """A pattern check on an absent value would report nonsense."""
        f = schema_with(required=True, pattern="^[A-Z]+$")
        problems = constraints.check(f, None, "")
        self.assertEqual(problems, ["required field is empty"])


class TestMinMax(unittest.TestCase):
    def test_below_min(self):
        f = schema_with(type="decimal", scale=2, min=0)
        self.assertIn(
            "below min 0", constraints.check(f, decimal.Decimal("-1"), "-1")[0]
        )

    def test_above_max(self):
        f = schema_with(type="decimal", scale=2, max=100)
        self.assertIn(
            "above max 100", constraints.check(f, decimal.Decimal("500"), "500")[0]
        )

    def test_bounds_are_inclusive(self):
        f = schema_with(type="decimal", scale=2, min=0, max=100)
        self.assertEqual(constraints.check(f, decimal.Decimal("0"), "0"), [])
        self.assertEqual(constraints.check(f, decimal.Decimal("100"), "100"), [])

    def test_inside_range_passes(self):
        f = schema_with(type="decimal", scale=2, min=0, max=100)
        self.assertEqual(constraints.check(f, decimal.Decimal("42.5"), "42.5"), [])

    def test_date_bounds_accept_iso(self):
        f = schema_with(
            type="date", format="YYYYMMDD", min="2024-01-01", max="2025-12-31"
        )
        self.assertIn("above max", constraints.check(f, "2099-01-01", "20990101")[0])
        self.assertEqual(constraints.check(f, "2024-06-01", "20240601"), [])

    def test_date_bounds_accept_the_fields_own_format(self):
        """Nobody should have to remember two spellings of the same date."""
        f = schema_with(type="date", format="YYYYMMDD", min="20240101", max="20251231")
        self.assertIn("below min", constraints.check(f, "20230101", "20230101")[0])
        self.assertEqual(constraints.check(f, "20240601", "20240601"), [])

    def test_unreadable_date_bound_is_caught_at_load(self):
        with self.assertRaises(SchemaError) as ctx:
            schema_with(type="date", format="YYYYMMDD", min="not-a-date")
        self.assertIn("cannot read", str(ctx.exception))

    def test_text_bounds_are_case_insensitive(self):
        """'M' is below 'a' by codepoint; a person means the alphabet."""
        f = schema_with(type="string", min="a", max="z")
        self.assertEqual(constraints.check(f, "M", "M"), [])
        self.assertIn("below min", constraints.check(f, "0abc", "0abc")[0])
        self.assertIn("above max", constraints.check(f, "zz9", "zz9")[0])

    def test_every_violation_is_reported(self):
        f = schema_with(type="decimal", scale=2, min=0, max=10)
        self.assertEqual(len(constraints.check(f, decimal.Decimal("99"), "99")), 1)


class TestPattern(unittest.TestCase):
    def test_match(self):
        f = schema_with(pattern=r"^[A-Z0-9]+$")
        self.assertEqual(constraints.check(f, "AB123", "AB123"), [])

    def test_no_match(self):
        f = schema_with(pattern=r"^[A-Z0-9]+$")
        self.assertIn("does not match", constraints.check(f, "ab-1", "ab-1")[0])

    def test_message_shows_the_raw(self):
        f = schema_with(pattern=r"^[A-Z]+$")
        self.assertIn("'lowercase'", constraints.check(f, "lowercase", "lowercase")[0])

    def test_pattern_on_non_string_is_refused_at_load(self):
        """A pattern on a decimal is a config error, not a runtime one."""
        with self.assertRaises(SchemaError):
            schema_with(type="decimal", scale=2, pattern=r"^\d+$")


class TestEnum(unittest.TestCase):
    def test_allowed_value(self):
        f = schema_with(enum=["USD", "EUR"])
        self.assertEqual(constraints.check(f, "USD", "USD"), [])

    def test_rejected_value_lists_the_options(self):
        f = schema_with(enum=["USD", "EUR"])
        problem = constraints.check(f, "GBP", "GBP")[0]
        self.assertIn("not one of the allowed", problem)
        self.assertIn("USD", problem)
        self.assertIn("EUR", problem)

    def test_compares_the_typed_value_for_decimals(self):
        """`enum: [1, 2]` must match a decimal that arrives as `1.00`."""
        f = schema_with(type="decimal", scale=2, enum=[1, 2, 3])
        self.assertEqual(constraints.check(f, decimal.Decimal("1.00"), "1.00"), [])

    def test_compares_dates_as_dates(self):
        f = schema_with(
            type="date", format="YYYYMMDD", enum=["2024-01-01", "2024-06-30"]
        )
        self.assertEqual(constraints.check(f, "20240101", "20240101"), [])
        self.assertIn("not one of", constraints.check(f, "20250101", "20250101")[0])

    def test_long_list_is_truncated_in_the_message(self):
        f = schema_with(enum=[str(i) for i in range(20)])
        self.assertIn("...", constraints.check(f, "x", "x")[0])


class TestSchemaLoadTimeValidation(unittest.TestCase):
    def test_pattern_that_does_not_compile_fails_the_schema(self):
        with self.assertRaises(SchemaError) as ctx:
            schema_with(pattern="[unclosed")
        self.assertIn("invalid pattern", str(ctx.exception))

    def test_pattern_on_decimal_fails_the_schema(self):
        with self.assertRaises(SchemaError) as ctx:
            schema_with(type="decimal", scale=2, pattern="^1$")
        self.assertIn("only applies to type string", str(ctx.exception))

    def test_empty_enum_fails_the_schema(self):
        with self.assertRaises(SchemaError) as ctx:
            schema_with(enum=[])
        self.assertIn("must not be empty", str(ctx.exception))

    def test_min_above_max_fails_the_schema(self):
        with self.assertRaises(SchemaError) as ctx:
            schema_with(type="decimal", scale=2, min=100, max=1)
        self.assertIn("is above max", str(ctx.exception))

    def test_date_bound_in_the_wrong_domain_fails_the_schema(self):
        with self.assertRaises(SchemaError) as ctx:
            schema_with(type="decimal", scale=2, min="2024-01-01")
        self.assertIn("not a number", str(ctx.exception))

    def test_no_constraints_is_still_valid(self):
        self.assertIsNotNone(schema_with())


class TestReportWording(unittest.TestCase):
    """The whole point: an operator must be able to tell the two apart."""

    def _validate(self, record_spec: dict, raw: bytes):
        sch = build_schema(
            {
                "format": "t",
                "version": "1.0.0",
                "record_length": 20,
                "fields": [
                    {
                        "name": "amount",
                        "start": 0,
                        "length": 8,
                        "type": "decimal",
                        "scale": 2,
                        "min": 0,
                    }
                ],
            }
        )
        return Validator(sch).validate_record(1, raw)

    def test_corrupt_data_says_invalid(self):
        result = self._validate({}, b"  ABCDEFGH          ")
        self.assertFalse(result.ok)
        self.assertIn("invalid decimal", result.errors[0])

    def test_out_of_policy_data_says_below_min(self):
        result = self._validate({}, b"  -50.00            ")
        self.assertFalse(result.ok)
        self.assertIn("below min 0", result.errors[0])
        self.assertNotIn("invalid", result.errors[0])

    def test_line_prefix_contract_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in.txt")
            with open(src, "wb") as fh:
                fh.write(b"aaaa\nbbbb\n")
            spath = os.path.join(tmp, "s.yaml")
            with open(spath, "w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    {
                        "format": "t",
                        "version": "1.0.0",
                        "record_length": 4,
                        "fields": [
                            {"name": "a", "start": 0, "length": 4, "min": "zzzz"}
                        ],
                    },
                    fh,
                )
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "--schema",
                        spath,
                        "--input",
                        src,
                        "--output",
                        os.path.join(tmp, "o.json"),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 3)
            self.assertIn("line 1:", err.getvalue())
            self.assertIn("line 2:", err.getvalue())


class TestMaskingOrder(unittest.TestCase):
    def test_constraint_sees_the_real_value_not_the_mask(self):
        """Otherwise `required` would answer about `***`."""
        sch = build_schema(
            {
                "format": "t",
                "version": "1.0.0",
                "record_length": 11,
                "fields": [
                    {
                        "name": "cuit",
                        "start": 0,
                        "length": 11,
                        "required": True,
                        "pattern": r"^\d{11}$",
                        "mask": "partial",
                        "mask_keep": 2,
                    }
                ],
            }
        )
        ok = Validator(sch).validate_record(1, b"20301234567")
        self.assertTrue(ok.ok, ok.errors)
        bad = Validator(sch).validate_record(2, b"XXXXXXXXXXX")
        self.assertFalse(bad.ok)
        self.assertIn("does not match", bad.errors[0])


if __name__ == "__main__":
    unittest.main()
