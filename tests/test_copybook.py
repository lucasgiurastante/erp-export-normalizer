"""COBOL copybook import: PIC parsing, offsets, and the `copybook` CLI command."""

from __future__ import annotations

import os
import tempfile
import unittest

import yaml

from cli import main
from core import copybook
from core.copybook import CopybookError, parse_picture
from core.schema import build_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPYBOOK = os.path.join(REPO_ROOT, "examples", "data", "cust.cpy")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "cobol_packed.txt")

SIMPLE = """\
      * a small copybook
       FD  TEST-FILE.
       01  TEST-REC.
           05  F-ONE       PIC X(4).
           05  F-TWO       PIC 9(6).
           05  F-THREE     PIC S9(7)V99 COMP-3.
           05  F-FOUR      PIC 9(8).
"""


def write(tmp: str, name: str, text: str) -> str:
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class TestParsePicture(unittest.TestCase):
    def test_alphanumeric(self):
        self.assertEqual(parse_picture("X(6)"), ("string", 6, 0, 0))
        self.assertEqual(parse_picture("A(10)"), ("string", 10, 0, 0))
        self.assertEqual(parse_picture("X"), ("string", 1, 0, 0))
        self.assertEqual(parse_picture("XXX"), ("string", 3, 0, 0))

    def test_plain_numeric(self):
        self.assertEqual(parse_picture("9(8)"), ("numeric", 8, 0, 8))
        self.assertEqual(parse_picture("S9(4)"), ("numeric", 4, 0, 4))

    def test_implied_decimal_point(self):
        # V99 is *two* decimal positions, not ninety-nine.
        self.assertEqual(parse_picture("S9(7)V99"), ("numeric", 9, 2, 9))
        self.assertEqual(parse_picture("9(3)V99"), ("numeric", 5, 2, 5))
        self.assertEqual(parse_picture("S9(5)V9(4)"), ("numeric", 9, 4, 9))
        self.assertEqual(parse_picture("9(5)V9(2)"), ("numeric", 7, 2, 7))

    def test_leading_sign_takes_no_position(self):
        self.assertEqual(parse_picture("S9(4)"), parse_picture("9(4)"))

    def test_rejects_bad_clause(self):
        with self.assertRaises(CopybookError):
            parse_picture("")
        with self.assertRaises(CopybookError):
            parse_picture("9(0)")
        with self.assertRaises(CopybookError):
            parse_picture("X(6")
        with self.assertRaises(CopybookError):
            parse_picture("Q(4)")


class TestCopybookParsing(unittest.TestCase):
    def test_offsets_and_record_length(self):
        data = copybook.parse_copybook(COPYBOOK)
        self.assertEqual(data["record_length"], 40)
        self.assertEqual(
            [(f["name"], f["start"], f["length"]) for f in data["fields"]],
            [
                ("CUST_ID", 0, 6),
                ("CUST_NAME", 6, 20),
                ("CUST_BAL", 26, 5),
                ("CUST_DATE", 31, 8),
                ("CUST_STATUS", 39, 1),
            ],
        )

    def test_pic_types_map_to_schema_types(self):
        by_name = {f["name"]: f for f in copybook.parse_copybook(COPYBOOK)["fields"]}
        self.assertNotIn("type", by_name["CUST_ID"])  # string is the default
        self.assertEqual(by_name["CUST_BAL"]["type"], "packed")
        self.assertEqual(by_name["CUST_BAL"]["scale"], 2)
        self.assertEqual(by_name["CUST_DATE"]["type"], "date")
        self.assertEqual(by_name["CUST_DATE"]["format"], "YYYYMMDD")

    def test_no_date_flag_keeps_pic_98_numeric(self):
        data = copybook.parse_copybook(COPYBOOK, want_date=False)
        by_name = {f["name"]: f for f in data["fields"]}
        self.assertNotEqual(by_name["CUST_DATE"].get("type"), "date")

    def test_generated_schema_is_valid(self):
        data = copybook.parse_copybook(COPYBOOK, table="cust_export")
        sch = build_schema(data)
        self.assertEqual(sch.record_length, 40)
        self.assertEqual(len(sch.fields), 5)

    def test_dashes_become_underscores(self):
        self.assertEqual(copybook.normalize_field_name("cust-bal"), "CUST_BAL")

    def test_select_record_by_name(self):
        text = (
            "       FD  F1.\n"
            "       01  FIRST-REC.\n"
            "           05  A PIC X(2).\n"
            "       01  SECOND-REC.\n"
            "           05  B PIC X(4).\n"
            "           05  C PIC X(6).\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "two.cpy", text)
            first = copybook.parse_copybook(path)
            self.assertEqual([f["name"] for f in first["fields"]], ["A"])
            second = copybook.parse_copybook(path, record="SECOND-REC")
            self.assertEqual([f["name"] for f in second["fields"]], ["B", "C"])
            self.assertEqual(second["record_length"], 10)

    def test_no_fd_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "bad.cpy", "       01  X.\n           05 A PIC X(2).\n")
            with self.assertRaises(CopybookError) as ctx:
                copybook.parse_copybook(path)
            self.assertIn("no FD", str(ctx.exception))

    def test_unknown_record_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "one.cpy", SIMPLE)
            with self.assertRaises(CopybookError) as ctx:
                copybook.parse_copybook(path, record="NOPE")
            self.assertIn("NOPE", str(ctx.exception))

    def test_comment_and_sequence_columns_ignored(self):
        text = (
            "000100* this is a comment line\n"
            "000200 FD  F.\n"
            "000300 01  R.\n"
            "000400     05 A PIC X(3).\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "seq.cpy", text)
            data = copybook.parse_copybook(path)
            self.assertEqual([f["name"] for f in data["fields"]], ["A"])


class TestCopybookCli(unittest.TestCase):
    def test_writes_valid_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "schema.yaml")
            code = main(
                [
                    "copybook",
                    COPYBOOK,
                    "--output",
                    out,
                    "--name",
                    "cust_export",
                ]
            )
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            self.assertEqual(data["record_length"], 40)
            self.assertEqual(data["table"], "cust_export")
            self.assertEqual(data["codepage"], "ebcdic-cp037")

    def test_generated_schema_converts_the_fixture(self):
        """The import is only worth anything if the schema actually runs."""
        with tempfile.TemporaryDirectory() as tmp:
            schema = os.path.join(tmp, "schema.yaml")
            out = os.path.join(tmp, "out.json")
            self.assertEqual(main(["copybook", COPYBOOK, "--output", schema]), 0)
            code = main(
                [
                    "--schema",
                    schema,
                    "--input",
                    FIXTURE,
                    "--output",
                    out,
                    "--format",
                    "json",
                ]
            )
            # 1 intentional bad BCD record in the fixture.
            self.assertEqual(code, 3)
            with open(out, encoding="utf-8") as fh:
                import json

                rows = json.load(fh)
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["CUST_ID"], "000001")
            self.assertEqual(rows[0]["CUST_BAL"], 1234567.99)
            self.assertEqual(rows[0]["CUST_DATE"], "2025-01-15")
            self.assertEqual(rows[1]["CUST_BAL"], -0.42)

    def test_missing_file_returns_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "copybook",
                    os.path.join(tmp, "nope.cpy"),
                    "--output",
                    os.path.join(tmp, "s.yaml"),
                ]
            )
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
