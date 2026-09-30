"""SAP FI schemas: tab delimited, with the real sign mechanism.

The previous version of these schemas was fixed width with no data behind
it, and the BSEG description promised trailing-sign negatives that the
schema did not implement. SAP does not use trailing signs: the sign lives
in `SHKZG` (S = debit, H = credit) and the amounts are unsigned. SAP also
has no canonical fixed-width BSEG export, so tab delimited is what these
schemas are now, because that is what `GUI_DOWNLOAD` and `SE16N` produce.
"""

from __future__ import annotations

import contextlib
import decimal
import io
import json
import os
import tempfile
import unittest

import yaml

from cli import main
from core.schema import build_schema, load_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMATS = os.path.join(REPO_ROOT, "core", "formats")
DATA = os.path.join(REPO_ROOT, "examples", "data")
BSEG = os.path.join(FORMATS, "sap_fi_bseg.yaml")
BKPF = os.path.join(FORMATS, "sap_fi_document.yaml")
BSEG_DATA = os.path.join(DATA, "sap_fi_bseg.txt")
BKPF_DATA = os.path.join(DATA, "sap_fi_document.txt")


def convert(schema: str, src: str) -> tuple[int, list[dict], str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o.json")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "--schema",
                    schema,
                    "--input",
                    src,
                    "--output",
                    out,
                    "--format",
                    "json",
                ]
            )
        with open(out, encoding="utf-8") as fh:
            return code, json.load(fh), err.getvalue()


class TestProvenance(unittest.TestCase):
    """The two claims the old schemas made, checked."""

    def test_bseg_is_tab_delimited(self):
        sch = load_schema(BSEG)
        self.assertEqual(sch.format, "delimited")
        self.assertEqual(sch.delimiter, "\t")
        self.assertTrue(sch.has_header)

    def test_bkpf_is_tab_delimited(self):
        sch = load_schema(BKPF)
        self.assertEqual(sch.format, "delimited")
        self.assertEqual(sch.delimiter, "\t")
        self.assertTrue(sch.has_header)

    def test_neither_promises_trailing_signs_anymore(self):
        """The old descriptions said "with trailing-sign negatives".

        The word may still appear, but only to say that SAP does not use
        it. What must be gone is the claim that these schemas handle one.
        """
        for path in (BSEG, BKPF):
            with self.subTest(schema=os.path.basename(path)):
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                self.assertNotIn("with trailing-sign negatives", text)

    def test_every_sap_schema_has_a_fixture(self):
        """A schema with no data behind it cannot be reviewed by anyone."""
        schemas = [f for f in os.listdir(FORMATS) if f.startswith("sap_")]
        self.assertTrue(schemas)
        for name in schemas:
            stem = name[:-5]
            with self.subTest(schema=name):
                self.assertTrue(
                    any(stem in f for f in os.listdir(DATA)),
                    f"{name} has no example data",
                )


class TestBseg(unittest.TestCase):
    def test_converts_and_skips_the_bad_row(self):
        code, rows, err = convert(BSEG, BSEG_DATA)
        self.assertEqual(code, 3)  # one row has an invalid SHKZG
        self.assertEqual(len(rows), 5)
        self.assertIn("line 7:", err)

    def test_field_names_match_the_data_dictionary(self):
        sch = load_schema(BSEG)
        self.assertEqual(
            [f.name for f in sch.fields],
            [
                "mandt",
                "bukrs",
                "belnr",
                "gjahr",
                "buzei",
                "bschl",
                "koart",
                "hkont",
                "shkzg",
                "dmbtr",
                "wrbtr",
                "waers",
                "sgtxt",
            ],
        )

    def test_amounts_are_unsigned(self):
        """SAP keeps the sign in SHKZG; a negative DMBTR would be wrong."""
        _, rows, _ = convert(BSEG, BSEG_DATA)
        for row in rows:
            with self.subTest(belnr=row["belnr"], buzei=row["buzei"]):
                self.assertGreaterEqual(row["dmbtr"], decimal.Decimal(0))

    def test_shkzg_is_restricted_to_s_and_h(self):
        code, rows, err = convert(BSEG, BSEG_DATA)
        self.assertEqual(code, 3)
        for row in rows:
            self.assertIn(row["shkzg"], ("S", "H"))

    def test_debit_and_credit_both_appear(self):
        _, rows, _ = convert(BSEG, BSEG_DATA)
        self.assertEqual({r["shkzg"] for r in rows}, {"S", "H"})

    def test_documents_balance(self):
        """Applying SHKZG, a balanced document nets to zero.

        This is what proves the sign lives in SHKZG and not in the amount,
        which is how SAP actually stores it.
        """
        _, rows, _ = convert(BSEG, BSEG_DATA)
        for belnr in ("1900000001", "1900000002"):
            with self.subTest(belnr=belnr):
                doc = [r for r in rows if r["belnr"] == belnr]
                self.assertGreater(len(doc), 1)
                net = sum(r["dmbtr"] if r["shkzg"] == "S" else -r["dmbtr"] for r in doc)
                self.assertEqual(net, decimal.Decimal("0.00"))


class TestBkpf(unittest.TestCase):
    def test_converts_and_skips_the_impossible_date(self):
        code, rows, err = convert(BKPF, BKPF_DATA)
        self.assertEqual(code, 3)
        self.assertEqual(len(rows), 3)
        self.assertIn("line 5:", err)
        self.assertIn("invalid date", err)

    def test_dates_normalise_to_iso(self):
        _, rows, _ = convert(BKPF, BKPF_DATA)
        self.assertEqual(rows[0]["budat"], "2024-09-01")
        self.assertEqual(rows[0]["bldat"], "2024-08-15")

    def test_document_type_is_kept_as_text(self):
        _, rows, _ = convert(BKPF, BKPF_DATA)
        self.assertEqual(rows[0]["blart"], "RE")
        self.assertEqual(rows[1]["blart"], "KR")


class TestCrossFile(unittest.TestCase):
    """The header and the line items have to join, which is the point."""

    def test_every_line_item_has_a_matching_header(self):
        _, headers, _ = convert(BKPF, BKPF_DATA)
        _, lines, _ = convert(BSEG, BSEG_DATA)
        doc_keys = {(h["bukrs"], h["belnr"], h["gjahr"]) for h in headers}
        for line in lines:
            with self.subTest(belnr=line["belnr"], buzei=line["buzei"]):
                self.assertIn((line["bukrs"], line["belnr"], line["gjahr"]), doc_keys)


class TestRegistryAndLibrary(unittest.TestCase):
    def test_library_still_validates(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["registry", "validate", FORMATS])
        self.assertEqual(code, 0)

    def test_index_verifies(self):
        index = os.path.join(REPO_ROOT, "registry.yaml")
        if not os.path.exists(index):
            self.skipTest("no registry index")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["registry", "verify", index]), 0)

    def test_the_two_fixed_width_claims_are_gone(self):
        """sap_batch keeps fixed width; the other two no longer claim it."""
        by_name = {}
        for name in os.listdir(FORMATS):
            with open(os.path.join(FORMATS, name), encoding="utf-8") as fh:
                by_name[name[:-5]] = yaml.safe_load(fh)
        self.assertEqual(by_name["sap_fi_bseg"]["format"], "delimited")
        self.assertEqual(by_name["sap_fi_document"]["format"], "delimited")

    def test_both_reload_through_build_schema(self):
        """A schema is only real if the loader can read it back."""
        for path in (BSEG, BKPF):
            with self.subTest(schema=os.path.basename(path)):
                with open(path, encoding="utf-8") as fh:
                    sch = build_schema(yaml.safe_load(fh))
                self.assertEqual(sch.format, "delimited")
                self.assertEqual(sch.delimiter, "\t")


if __name__ == "__main__":
    unittest.main()
