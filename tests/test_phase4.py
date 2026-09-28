"""Phase 4 tests: Singer tap output and the zero-dependency web UI."""

from __future__ import annotations

import decimal
import json
import os
import socket
import sys
import tempfile
import threading
import types as pytypes
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from cli import main
from core import webui
from tests.test_mvp import REC_BAD_DATE, REC_OK, rec, write_schema


class TestSinger(unittest.TestCase):
    def _run(self, tmp: str, records: list[bytes]):
        input_path = os.path.join(tmp, "in.txt")
        with open(input_path, "wb") as fh:
            fh.writelines(r + b"\n" for r in records)
        out_path = os.path.join(tmp, "out.singer")
        code = main(
            [
                "--schema",
                write_schema(tmp),
                "--input",
                input_path,
                "--output",
                out_path,
                "--format",
                "singer",
            ]
        )
        with open(out_path, encoding="utf-8") as fh:
            lines = [json.loads(ln) for ln in fh if ln.strip()]
        return code, lines

    def test_singer_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, lines = self._run(
                tmp, [REC_OK, rec("2", "B", "20250116", "67890", "EUR")]
            )
            self.assertEqual(code, 0)
            self.assertEqual(lines[0]["type"], "SCHEMA")
            self.assertEqual(lines[0]["stream"], "jde_fixed_width")
            props = lines[0]["schema"]["properties"]
            self.assertEqual(props["amount"], {"type": "number"})
            self.assertEqual(props["date"], {"type": "string"})
            records = [ln for ln in lines if ln["type"] == "RECORD"]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["record"]["date"], "2025-01-15")
            self.assertEqual(records[0]["record"]["amount"], 123.45)
            self.assertEqual(lines[-1]["type"], "STATE")

    def test_singer_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, first = self._run(tmp, [REC_OK])
            _, second = self._run(tmp, [REC_OK])
            self.assertEqual(first, second)

    def test_singer_errors_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, lines = self._run(tmp, [REC_OK, REC_BAD_DATE])
            self.assertEqual(code, 3)
            records = [ln for ln in lines if ln["type"] == "RECORD"]
            self.assertEqual(len(records), 1)  # only the valid record


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestWebUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        formats_dir = os.path.join(os.path.dirname(__file__), "..", "core", "formats")
        cls.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), webui.make_handler(os.path.abspath(formats_dir))
        )
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path: str) -> str:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.read().decode("utf-8")

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))

    def test_index_serves_html(self):
        html = self._get("/")
        self.assertIn("erp-export-normalizer", html)
        self.assertIn("generate", html.lower())

    def test_api_generate(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "data.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("date,amount\n20250115,1234.50\n20250220,567.00\n")
            result = self._post("/api/generate", {"input": csv_path})
            self.assertIn("format: delimited", result["schema"])
            self.assertEqual(result["delimiter"], ",")

    def test_api_convert(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "data.csv")
            with open(csv_path, "w", encoding="utf-8") as fh:
                fh.write("date,amount,customer\n20250115,1234.50,CUST A\n")
            gen = self._post("/api/generate", {"input": csv_path})
            result = self._post(
                "/api/convert",
                {
                    "input": csv_path,
                    "schema": gen["schema"],
                    "format": "json",
                },
            )
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("records: 1", result["stderr"])
            self.assertEqual(result["preview"][0]["date"], "2025-01-15")

    def test_api_convert_missing_input(self):
        result = self._post(
            "/api/convert",
            {
                "input": "/nonexistent/file.txt",
                "schema": "format: delimited\nfields: []\n",
            },
        )
        self.assertIn("error", result)


class TestSparkMock(unittest.TestCase):
    """Spark backend type mapping without JVM (P1-1): fake SparkSession."""

    def test_spark_type_mapping_without_jvm(self):
        from core import io as io_mod

        captured: dict = {}

        class FakeStringType:
            def __repr__(self) -> str:
                return "StringType()"

        class FakeDecimalType:
            def __init__(self, precision: int, scale: int):
                self.precision = precision
                self.scale = scale

        class FakeStructField:
            def __init__(self, name, dataType, nullable=True):  # noqa: N803
                self.name = name
                self.dataType = dataType
                self.nullable = nullable

        class FakeStructType:
            def __init__(self, fields):
                self.fields = list(fields)

        class FakeDataFrame:
            def __init__(self, data, schema):
                self._data = list(data)
                self.schema = schema

            def collect(self):
                names = [f.name for f in self.schema.fields]
                return [dict(zip(names, row)) for row in self._data]

        class FakeSparkSession:
            builder = None  # patched below

            def createDataFrame(self, data, schema):
                captured["data"] = list(data)
                captured["schema"] = schema
                return FakeDataFrame(data, schema)

        class FakeBuilder:
            def __init__(self, session):
                self._session = session

            def appName(self, *args, **kwargs):
                return self

            def getOrCreate(self):
                return self._session

        session = FakeSparkSession()
        FakeSparkSession.builder = FakeBuilder(session)

        pyspark_mod = pytypes.ModuleType("pyspark")
        sql_mod = pytypes.ModuleType("pyspark.sql")
        types_mod = pytypes.ModuleType("pyspark.sql.types")
        sql_mod.SparkSession = FakeSparkSession
        types_mod.DecimalType = FakeDecimalType
        types_mod.StringType = FakeStringType
        types_mod.StructField = FakeStructField
        types_mod.StructType = FakeStructType

        saved = {
            k: sys.modules.get(k)
            for k in ("pyspark", "pyspark.sql", "pyspark.sql.types")
        }
        sys.modules["pyspark"] = pyspark_mod
        sys.modules["pyspark.sql"] = sql_mod
        sys.modules["pyspark.sql.types"] = types_mod

        def _restore():
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        self.addCleanup(_restore)

        with tempfile.TemporaryDirectory() as tmp:
            in_path = os.path.join(tmp, "in.txt")
            with open(in_path, "wb") as fh:
                fh.write(rec("1", "A", "20250115", "12345", "USD") + b"\n")
                fh.write(rec("2", "B", "20250116", "67890", "EUR") + b"\n")
            # Always mocked: works whether or not pyspark/JVM is installed.
            df = io_mod.read_erp(in_path, schema=write_schema(tmp), backend="spark")
            rows = df.collect()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["date"], "2025-01-15")
            self.assertEqual(rows[1]["amount"], decimal.Decimal("678.90"))
            self.assertEqual(
                [f.name for f in df.schema.fields],
                ["id", "type", "date", "amount", "currency"],
            )
            by_name = {f.name: f for f in captured["schema"].fields}
            amount_type = by_name["amount"].dataType
            self.assertIsInstance(amount_type, FakeDecimalType)
            self.assertEqual((amount_type.precision, amount_type.scale), (38, 2))
            for name in ("id", "type", "date", "currency"):
                self.assertIsInstance(
                    by_name[name].dataType, FakeStringType, f"field {name!r}"
                )


if __name__ == "__main__":
    unittest.main()
