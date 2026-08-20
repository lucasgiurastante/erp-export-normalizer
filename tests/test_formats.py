"""Built-in format library tests: each format/ schema parses a representative
record and the detector picks the correct schema for its own fixture."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from cli import main
from core import detector as detector_mod
from core.schema import load_schema

FORMATS_DIR = os.path.join(os.path.dirname(__file__), "..", "formats")


def pad(s: str, width: int, codepage: str = "ascii") -> bytes:
    return s[:width].ljust(width).encode(codepage, errors="replace")


def rec_sap_fi(
    company: str,
    document: str,
    year: str,
    date: str,
    account: str,
    postkey: str,
    amount: str,
    currency: str,
    text: str,
) -> bytes:
    return (
        pad(company, 4)
        + pad(document, 10)
        + pad(year, 4)
        + pad(date, 8)
        + pad(account, 10)
        + pad(postkey, 2)
        + amount.rjust(15).encode("ascii")
        + pad(currency, 3)
        + pad(text, 22)
    )


def rec_jde_ap(
    address: str, invoice: str, date: str, amount: str, currency: str, supplier: str
) -> bytes:
    return (
        pad(address, 8)
        + pad(invoice, 12)
        + pad(date, 8)
        + amount.rjust(13).encode("ascii")
        + pad(currency, 3)
        + pad(supplier, 20)
    )


def rec_jde_gl(
    ledger: str,
    account: str,
    date: str,
    debit: str,
    credit: str,
    currency: str,
    description: str,
) -> bytes:
    return (
        pad(ledger, 2)
        + pad(account, 12)
        + pad(date, 8)
        + debit.rjust(13).encode("ascii")
        + credit.rjust(13).encode("ascii")
        + pad(currency, 3)
        + pad(description, 30)
    )


def rec_cobol(key: str, name: str, balance: str, date: str, status: str) -> bytes:
    return (
        pad(key, 6, "cp037")
        + pad(name, 30, "cp037")
        + balance.rjust(12).encode("cp037")
        + pad(date, 8, "cp037")
        + pad(status, 1, "cp037")
    )


SAP_FI_OK = rec_sap_fi(
    "1000",
    "0100000000",
    "2025",
    "20250115",
    "400000",
    "40",
    "123456789012345",
    "EUR",
    "CUSTOMER PAYMENT",
)
JDE_AP_OK = rec_jde_ap(
    "00004211", "INV000123456", "20250210", "1234500000000", "USD", "SUPPLIER A"
)
JDE_GL_OK = rec_jde_gl(
    "AA",
    "1.1010.1010",
    "20250301",
    "1000000000000",
    "0000000000000",
    "USD",
    "RENT EXPENSE",
)
COBOL_OK = rec_cobol("000001", "CUSTOMER NAME HERE", "000012345600", "20250401", "A")


class TestBuiltinSchemas(unittest.TestCase):
    def test_all_schemas_load(self):
        for name in (
            "jde_ar.yaml",
            "sap_batch.yaml",
            "sap_fi_document.yaml",
            "jde_ap.yaml",
            "jde_gl.yaml",
            "cobol_fixed.yaml",
        ):
            path = os.path.join(FORMATS_DIR, name)
            self.assertTrue(os.path.exists(path), name)
            sch = load_schema(path)
            self.assertGreater(len(sch.fields), 0, name)

    def _convert(self, fmt: str, record: bytes) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "input.txt")
            with open(in_path, "wb") as fh:
                fh.write(record + b"\n")
            out_path = os.path.join(tmp, "out.json")
            code = main(
                [
                    "--schema",
                    os.path.join(FORMATS_DIR, f"{fmt}.yaml"),
                    "--input",
                    in_path,
                    "--output",
                    out_path,
                    "--format",
                    "json",
                ]
            )
            self.assertEqual(code, 0, fmt)
            with open(out_path, encoding="utf-8") as fh:
                return json.load(fh)

    def test_sap_fi_converts(self):
        rows = self._convert("sap_fi_document", SAP_FI_OK)
        self.assertEqual(rows[0]["company"], "1000")
        self.assertEqual(rows[0]["date"], "2025-01-15")
        self.assertEqual(rows[0]["amount"], 1234567890123.45)

    def test_jde_ap_converts(self):
        rows = self._convert("jde_ap", JDE_AP_OK)
        self.assertEqual(rows[0]["invoice"], "INV000123456")
        self.assertEqual(rows[0]["date"], "2025-02-10")
        self.assertEqual(rows[0]["amount"], 12345000000.0)

    def test_jde_gl_converts(self):
        rows = self._convert("jde_gl", JDE_GL_OK)
        self.assertEqual(rows[0]["account"], "1.1010.1010")
        self.assertEqual(rows[0]["debit"], 10000000000.0)
        self.assertEqual(rows[0]["credit"], 0.0)

    def test_cobol_converts(self):
        rows = self._convert("cobol_fixed", COBOL_OK)
        self.assertEqual(rows[0]["customer_name"].strip(), "CUSTOMER NAME HERE")
        self.assertEqual(rows[0]["balance"], 123456.0)
        self.assertEqual(rows[0]["activity_date"], "2025-04-01")


class TestLibraryDetector(unittest.TestCase):
    def _write(self, tmp: str, record: bytes) -> str:
        path = os.path.join(tmp, "input.txt")
        with open(path, "wb") as fh:
            fh.write(record + b"\n")
        return path

    def _detect(self, record: bytes) -> str | None:
        with tempfile.TemporaryDirectory() as tmp:
            in_path = self._write(tmp, record)
            found = detector_mod.Detector(FORMATS_DIR).detect(in_path)
            return os.path.basename(found.source_path) if found else None

    def test_detect_sap_fi(self):
        self.assertEqual(self._detect(SAP_FI_OK), "sap_fi_document.yaml")

    def test_detect_jde_ap(self):
        self.assertEqual(self._detect(JDE_AP_OK), "jde_ap.yaml")

    def test_detect_jde_gl(self):
        self.assertEqual(self._detect(JDE_GL_OK), "jde_gl.yaml")

    def test_detect_cobol(self):
        self.assertEqual(self._detect(COBOL_OK), "cobol_fixed.yaml")

    def test_detect_no_match(self):
        self.assertIsNone(self._detect(b"this is not any known format at all"))


if __name__ == "__main__":
    unittest.main()
