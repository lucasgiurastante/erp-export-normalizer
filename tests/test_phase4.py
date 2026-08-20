"""Phase 4 tests: Singer tap output and the zero-dependency web UI."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
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
        formats_dir = os.path.join(os.path.dirname(__file__), "..", "formats")
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
            self.assertIn("records: 1", result["stdout"])
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


if __name__ == "__main__":
    unittest.main()
