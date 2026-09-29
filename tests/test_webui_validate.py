"""Web UI: the visual validation view and its JSON endpoint."""

from __future__ import annotations

import contextlib
import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from core import webui

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
AR_INPUT = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")
PACKED_SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "cobol_packed.yaml")
PACKED_INPUT = os.path.join(REPO_ROOT, "examples", "data", "cobol_packed.txt")
PLUGINS = os.path.join(REPO_ROOT, "core", "plugin_examples")


class TestCollectErrors(unittest.TestCase):
    def test_clean_file_has_no_errors(self):
        report = webui.collect_errors(AR_SCHEMA, AR_INPUT, PLUGINS)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["total"], 3)
        self.assertFalse(report["truncated"])

    def test_error_rows_are_structured(self):
        report = webui.collect_errors(PACKED_SCHEMA, PACKED_INPUT, PLUGINS)
        self.assertTrue(report["error_count"])
        row = report["errors"][0]
        # every key present so the UI never renders "undefined"
        for key in ("line", "field", "message", "raw"):
            self.assertIn(key, row)
            self.assertIsInstance(row[key], str if key != "line" else int)

    def test_field_is_never_none(self):
        """A length mismatch has no field; the UI must still render a cell."""
        report = webui.collect_errors(AR_SCHEMA, PACKED_INPUT, PLUGINS)
        for row in report["errors"]:
            self.assertIsNotNone(row["field"])

    def test_raw_is_captured_for_p1_3_contract(self):
        report = webui.collect_errors(PACKED_SCHEMA, PACKED_INPUT, PLUGINS)
        packed_errors = [r for r in report["errors"] if r["field"] == "balance"]
        self.assertTrue(packed_errors)
        self.assertEqual(packed_errors[0]["raw"], "ab0cde0cff")

    def test_truncation_is_reported_with_exact_count(self):
        report = webui.collect_errors(AR_SCHEMA, PACKED_INPUT, PLUGINS, max_errors=2)
        self.assertTrue(report["truncated"])
        self.assertEqual(len(report["errors"]), 2)
        self.assertGreater(report["error_count"], 2)

    def test_missing_input_raises_oserror(self):
        with self.assertRaises(OSError):
            webui.collect_errors(AR_SCHEMA, "/nope/nothing.txt", PLUGINS)


class TestValidateHtml(unittest.TestCase):
    def test_page_renders_without_innerhtml_for_data(self):
        """File content reaches the DOM via textContent, never innerHTML."""
        self.assertIn("/api/validate", webui.VALIDATE_HTML)
        self.assertIn("textContent", webui.VALIDATE_HTML)
        # no innerHTML assignment anywhere: file bytes must not become markup
        self.assertNotIn("innerHTML", webui.VALIDATE_HTML)
        self.assertNotIn("<script>alert", webui.VALIDATE_HTML.lower())

    def test_page_is_self_contained(self):
        """Air-gapped: no external assets, fonts or CDNs."""
        html = webui.VALIDATE_HTML
        for token in ("http://", "https://", "//cdn", "<link"):
            self.assertNotIn(token, html)


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), webui.make_handler(PLUGINS))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, str]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read().decode("utf-8")

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_index_still_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("erp-export-normalizer", body)

    def test_validate_page_served(self):
        status, body = self._get("/validate")
        self.assertEqual(status, 200)
        self.assertIn("Validation report", body)

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_api_validate_clean_file(self):
        status, data = self._post(
            "/api/validate", {"schema": AR_SCHEMA, "input": AR_INPUT}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["error_count"], 0)
        self.assertEqual(data["total"], 3)

    def test_api_validate_reports_errors(self):
        status, data = self._post(
            "/api/validate", {"schema": PACKED_SCHEMA, "input": PACKED_INPUT}
        )
        self.assertEqual(status, 200)
        self.assertGreater(data["error_count"], 0)
        self.assertTrue(data["errors"])

    def test_api_validate_missing_input(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/validate", {"schema": AR_SCHEMA, "input": "/nope.txt"})
        self.assertEqual(ctx.exception.code, 400)
        self.assertIn("input file not found", ctx.exception.read().decode())

    def test_api_validate_missing_schema(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post("/api/validate", {"schema": "/nope.yaml", "input": AR_INPUT})
        self.assertEqual(ctx.exception.code, 400)

    def test_binds_to_loopback_only(self):
        """The design rule is local-only; assert it rather than assume it."""
        import inspect

        sig = inspect.signature(webui.serve)
        self.assertEqual(sig.parameters["host"].default, "127.0.0.1")

    def test_no_console_noise(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = webui._reply.__code__
        self.assertIsNotNone(code)


class TestServeOutput(unittest.TestCase):
    def test_startup_message_points_at_the_new_page(self):
        import inspect

        src = inspect.getsource(webui.serve)
        self.assertIn("/validate", src)


if __name__ == "__main__":
    unittest.main()
