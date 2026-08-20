"""Phase: plugin parser tests (custom binary readers via plugins/)."""

from __future__ import annotations

import os
import struct
import tempfile
import unittest

from cli import main
from core import plugins as plugins_mod

TESTS_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
PLUGINS_DIR = os.path.join(PROJECT_ROOT, "plugins")

FRAMED_SCHEMA = """\
format: framed
version: 1.0.0
parser: length_prefixed_frame
codepage: cp1252
fields:
  - {name: id,   start: 0,  length: 4}
  - {name: date, start: 4,  length: 8,  type: date,    format: YYYYMMDD}
  - {name: amt,  start: 12, length: 8,  type: decimal, scale: 2, align: right}
"""


def framed_record(payload: bytes) -> bytes:
    return struct.pack(">H", len(payload)) + payload


def payload(id_: str, date_: str, amt: str) -> bytes:
    return (id_[:4].ljust(4) + date_ + amt.rjust(8)).encode("ascii")


class TestPlugins(unittest.TestCase):
    def test_discover_finds_length_prefixed(self):
        found = plugins_mod.discover(PLUGINS_DIR)
        names = [p.name for p in found]
        self.assertIn("length_prefixed_frame", names)

    def test_plugin_reader_converts(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = os.path.join(tmp, "schema.yaml")
            with open(schema_path, "w", encoding="utf-8") as fh:
                fh.write(FRAMED_SCHEMA)
            in_path = os.path.join(tmp, "input.bin")
            with open(in_path, "wb") as fh:
                fh.write(framed_record(payload("0001", "20250115", "12345")))
                fh.write(framed_record(payload("0002", "20250220", "67890")))
            out_path = os.path.join(tmp, "out.json")
            code = main(
                [
                    "--schema",
                    schema_path,
                    "--input",
                    in_path,
                    "--output",
                    out_path,
                    "--format",
                    "json",
                    "--plugins-dir",
                    PLUGINS_DIR,
                ]
            )
            self.assertEqual(code, 0)
            import json

            with open(out_path, encoding="utf-8") as fh:
                rows = json.load(fh)
            self.assertEqual(rows[0]["date"], "2025-01-15")
            self.assertEqual(rows[0]["amt"], 123.45)
            self.assertEqual(rows[1]["amt"], 678.90)

    def test_plugin_validates_bad_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = os.path.join(tmp, "schema.yaml")
            with open(schema_path, "w", encoding="utf-8") as fh:
                fh.write(FRAMED_SCHEMA)
            in_path = os.path.join(tmp, "input.bin")
            with open(in_path, "wb") as fh:
                fh.write(framed_record(payload("0001", "20251301", "12345")))
            code = main(
                [
                    "--schema",
                    schema_path,
                    "--input",
                    in_path,
                    "--output",
                    os.path.join(tmp, "out.json"),
                    "--plugins-dir",
                    PLUGINS_DIR,
                ]
            )
            self.assertEqual(code, 3)  # EXIT_VALIDATION

    def test_plugin_missing_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = os.path.join(tmp, "schema.yaml")
            with open(schema_path, "w", encoding="utf-8") as fh:
                fh.write(
                    "format: framed\n"
                    "version: 1.0.0\n"
                    "parser: does_not_exist\n"
                    "fields: [{name: id, length: 4}]\n"
                )
            code = main(
                [
                    "--schema",
                    schema_path,
                    "--input",
                    os.path.join(tmp, "x.bin"),
                    "--output",
                    os.path.join(tmp, "o.json"),
                    "--plugins-dir",
                    PLUGINS_DIR,
                ]
            )
            self.assertEqual(code, 1)  # EXIT_ERROR

    def test_truncated_frame_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = os.path.join(tmp, "schema.yaml")
            with open(schema_path, "w", encoding="utf-8") as fh:
                fh.write(FRAMED_SCHEMA)
            in_path = os.path.join(tmp, "input.bin")
            with open(in_path, "wb") as fh:
                fh.write(b"\x00\x10" + b"short")  # declares 16 bytes, provides 5
            code = main(
                [
                    "--schema",
                    schema_path,
                    "--input",
                    in_path,
                    "--output",
                    os.path.join(tmp, "out.json"),
                    "--plugins-dir",
                    PLUGINS_DIR,
                ]
            )
            self.assertEqual(code, 1)  # EXIT_ERROR

    def test_schema_with_parser_needs_no_record_length(self):
        import yaml

        from core.schema import build_schema

        data = yaml.safe_load(FRAMED_SCHEMA)
        built = build_schema(data)
        self.assertIsNone(built.record_length)
        self.assertEqual(built.parser, "length_prefixed_frame")


if __name__ == "__main__":
    unittest.main()
