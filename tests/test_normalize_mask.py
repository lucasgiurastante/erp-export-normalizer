"""Per-field text normalization and output masking."""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest

from cli import main
from core import converters
from core.converters import mask_value, normalize_text
from core.schema import SchemaError, build_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JDE_AR = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")


def field(**kwargs):
    data = {"name": "n", "start": 0, "length": 20}
    data.update(kwargs)
    return build_schema(
        {
            "format": "t",
            "version": "1.0.0",
            "record_length": 20,
            "fields": [data],
        }
    ).fields[0]


class TestNormalizeText(unittest.TestCase):
    def test_trim(self):
        self.assertEqual(normalize_text("  ACME  ", field(trim=True)), "ACME")

    def test_trim_removes_nbsp(self):
        """EBCDIC exports often pad with NBSP, which str.strip() handles."""
        self.assertEqual(normalize_text(" ACME ", field(trim=True)), "ACME")

    def test_no_trim_by_default(self):
        self.assertEqual(normalize_text("  ACME  ", field()), "  ACME  ")

    def test_case_options(self):
        self.assertEqual(normalize_text("acme", field(case="upper")), "ACME")
        self.assertEqual(normalize_text("ACME", field(case="lower")), "acme")
        self.assertEqual(normalize_text("acme corp", field(case="title")), "Acme Corp")

    def test_nfkc_folds_fullwidth_and_composed(self):
        # full-width digits and a composed accented char
        self.assertEqual(normalize_text("ＡＣＭＥ", field(normalize="NFKC")), "ACME")
        self.assertEqual(normalize_text("peña", field(normalize="NFC")), "peña")

    def test_combined(self):
        f = field(trim=True, case="upper", normalize="NFKC")
        # full-width latin folds to ascii under NFKC
        self.assertEqual(normalize_text("  ａｃｍｅ ", f), "ACME")

    def test_nfkc_does_not_fold_across_scripts(self):
        """NFKC normalizes compatibility, it does not transliterate.

        A Cyrillic C is not a Latin C: folding between scripts would be a
        guess about the data, not a normalization.
        """
        f = field(trim=True, case="upper", normalize="NFKC")
        self.assertEqual(normalize_text("aс", f), "AС")


class TestMaskValue(unittest.TestCase):
    def test_full(self):
        self.assertEqual(mask_value("20301234567", field(mask="full")), "*" * 11)

    def test_partial_keeps_edges(self):
        self.assertEqual(
            mask_value("20301234567", field(mask="partial", mask_keep=2)),
            "20*******67",
        )

    def test_partial_too_wide_degrades_to_full(self):
        # keeping half the value would reveal most of it
        f = field(mask="partial", mask_keep=5)
        self.assertEqual(mask_value("1234567890", f), "*" * 10)

    def test_hash_is_deterministic(self):
        f = field(mask="hash")
        a = mask_value("20301234567", f)
        b = mask_value("20301234567", f)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("sha256:"))

    def test_hash_differs_per_value(self):
        f = field(mask="hash")
        self.assertNotEqual(mask_value("20301234567", f), mask_value("20301234568", f))

    def test_mask_keeps_type_string(self):
        """A masked number must not stay a number, or the audit trail lies."""
        f = field(mask="partial", mask_keep=2)
        got = converters.convert_field(b"20301234567        ", f, "utf-8")
        self.assertIsInstance(got, str)


class TestSchemaValidation(unittest.TestCase):
    def test_rejects_unknown_case(self):
        with self.assertRaises(SchemaError) as ctx:
            field(case="camel")
        self.assertIn("unsupported case", str(ctx.exception))

    def test_rejects_unknown_normalize(self):
        with self.assertRaises(SchemaError) as ctx:
            field(normalize="NFKZ")
        self.assertIn("unsupported normalize", str(ctx.exception))

    def test_rejects_unknown_mask(self):
        with self.assertRaises(SchemaError) as ctx:
            field(mask="blur")
        self.assertIn("unsupported mask", str(ctx.exception))

    def test_rejects_negative_mask_keep(self):
        with self.assertRaises(SchemaError) as ctx:
            field(mask="partial", mask_keep=-1)
        self.assertIn("mask_keep", str(ctx.exception))

    def test_case_on_numeric_is_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            build_schema(
                {
                    "format": "t",
                    "version": "1.0.0",
                    "record_length": 4,
                    "fields": [
                        {
                            "name": "a",
                            "start": 0,
                            "length": 4,
                            "type": "decimal",
                            "case": "upper",
                        }
                    ],
                }
            )
        self.assertIn("only apply to", str(ctx.exception))


class TestCliEndToEnd(unittest.TestCase):
    def _convert(self, schema: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            spath = os.path.join(tmp, "s.yaml")
            with open(spath, "w", encoding="utf-8") as fh:
                import yaml

                yaml.safe_dump(schema, fh)
            out = os.path.join(tmp, "o.json")
            main(
                [
                    "--schema",
                    spath,
                    "--input",
                    FIXTURE,
                    "--output",
                    out,
                    "--format",
                    "json",
                ]
            )
            with open(out, encoding="utf-8") as fh:
                return json.load(fh)

    def test_masked_fixture_is_still_parseable(self):
        """A masked export must convert cleanly, with no errors."""
        base = {
            "format": "jde_fixed_width",
            "version": "1.0.0",
            "record_length": 44,
            "codepage": "cp850",
            "table": "jde_ar_export",
            "fields": [
                {"name": "id", "start": 0, "length": 10, "trim": True},
                {
                    "name": "type",
                    "start": 10,
                    "length": 15,
                    "trim": True,
                    "case": "upper",
                },
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
                {
                    "name": "currency",
                    "start": 41,
                    "length": 3,
                    "mask": "partial",
                    "mask_keep": 1,
                },
            ],
        }
        rows = self._convert(base)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["type"], "INVOICE")
        # mask_keep=1 keeps the first and last character of a 3-char currency
        self.assertEqual(rows[0]["currency"], "U*D")
        self.assertEqual(rows[0]["amount"], 1234.56)

    def test_registry_still_valid(self):

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main(["registry", os.path.join(REPO_ROOT, "core", "formats")]), 0
            )


if __name__ == "__main__":
    unittest.main()
