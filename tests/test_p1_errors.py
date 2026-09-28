"""P1-3 UX errores: field + raw truncado (50) en error_lines.

Contrato:
- error_lines mantiene keys existentes {"line", "errors"} + "details" opcional.
- cada error de campo incluye field + raw truncado a 50 chars.
- formato CLI "line N:" intacto (lo parsean scripts) — verificado por regex.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import tempfile
import unittest

from cli import main
from core import validator as validator_mod
from core import schema as schema_mod

DELIMITED_SCHEMA = {
    "format": "delimited",
    "version": "1.0.0",
    "delimiter": ",",
    "has_header": False,
    "fields": [
        {"name": "id"},
        {"name": "amount", "type": "decimal", "scale": 2},
    ],
}


def write_schema(tmp: str, data: dict) -> str:
    import yaml

    path = os.path.join(tmp, "schema.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    return path


class TestP1ErrorUx(unittest.TestCase):
    def test_field_and_raw_in_error(self):
        sch = schema_mod.build_schema(dict(DELIMITED_SCHEMA))
        val = validator_mod.Validator(sch)
        res = val.validate_record(12, b"001,NOT_A_NUMBER")
        self.assertFalse(res.ok)
        self.assertTrue(res.errors)
        err = res.errors[0]
        self.assertIn("amount", err)
        self.assertIn("raw='NOT_A_NUMBER'", err)

    def test_raw_truncated_to_50(self):
        sch = schema_mod.build_schema(dict(DELIMITED_SCHEMA))
        val = validator_mod.Validator(sch)
        long_raw = "X" * 80
        res = val.validate_record(1, f"001,{long_raw}".encode())
        self.assertFalse(res.ok)
        err = res.errors[0]
        self.assertIn("amount", err)
        # el sufijo raw='...' va truncado a 50 (el mensaje del converter
        # puede traer el valor completo; solo el sufijo se acota)
        m = re.search(r"raw='([^']*)'", err)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1), long_raw[:50])
        self.assertEqual(len(m.group(1)), 50)
        # detalle estructurado tambien truncado
        self.assertTrue(res.details)
        self.assertLessEqual(len(res.details[0]["raw"]), 50)
        self.assertEqual(res.details[0]["raw"], long_raw[:50])

    def test_error_lines_keeps_keys_and_adds_details(self):
        sch = schema_mod.build_schema(dict(DELIMITED_SCHEMA))
        val = validator_mod.Validator(sch)
        stats = validator_mod.Stats()
        stats.add(val.validate_record(7, b"001,BAD"))
        report = stats.report()
        self.assertEqual(len(report["error_lines"]), 1)
        entry = report["error_lines"][0]
        # keys existentes preservadas
        self.assertIn("line", entry)
        self.assertIn("errors", entry)
        self.assertEqual(entry["line"], 7)
        # keys nuevas opcionales
        self.assertIn("details", entry)
        self.assertIn("amount", entry["errors"][0])
        self.assertIn("raw='BAD'", entry["errors"][0])

    def test_cli_keeps_line_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = write_schema(tmp, dict(DELIMITED_SCHEMA))
            input_path = os.path.join(tmp, "input.txt")
            with open(input_path, "wb") as fh:
                fh.write(b"001,12.34\n")
                fh.write(b"002,NOT_A_NUMBER\n")
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                code = main(
                    [
                        "--schema",
                        schema_path,
                        "--input",
                        input_path,
                        "--output",
                        os.path.join(tmp, "out.json"),
                        "--format",
                        "json",
                    ]
                )
            self.assertEqual(code, 3)
            diag = buf.getvalue()
            # formato "line N:" intacto
            self.assertRegex(diag, r"line\s+\d+:")
            # y con field + raw enriquecido
            self.assertIn("amount", diag)
            self.assertIn("raw='NOT_A_NUMBER'", diag)


if __name__ == "__main__":
    unittest.main()
