"""Zero-dependency web UI: schema generation and conversion preview.

Air-gapped by design: stdlib `http.server` only, no external assets, and the
server binds to 127.0.0.1 by default. Paths are server-local (single-user
workstations, per the design rule "no network, no database").
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from . import generator

INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>erp-export-normalizer</title>
<style>
body{font-family:ui-monospace,Menlo,monospace;background:#111;color:#ddd;
max-width:720px;margin:2rem auto;padding:0 1rem}
h1{font-size:1.3rem}label{display:block;margin:.8rem 0 .2rem}
input,select,textarea,button{width:100%;box-sizing:border-box;
background:#1c1c1c;color:#eee;border:1px solid #444;padding:.45rem;font:inherit}
button{margin-top:1rem;cursor:pointer}
pre{white-space:pre-wrap;background:#1c1c1c;border:1px solid #444;
padding:.6rem;max-height:18rem;overflow:auto}
</style></head><body>
<h1>erp-export-normalizer</h1>
<p>Generate a schema from an example flat file, then preview a conversion.
Server-local paths only. No data leaves this machine.</p>
<label>Input file path</label>
<input id="input" placeholder="/path/to/export.txt">
<label>Output format</label>
<select id="format">
<option>json</option><option>csv</option><option>ndjson</option>
<option>sql</option><option>singer</option>
</select>
<button onclick="generate()">Generate schema</button>
<pre id="schema"></pre>
<button onclick="convert()">Preview conversion</button>
<pre id="report"></pre>
<pre id="preview"></pre>
<script>
async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  return r.json();
}
async function generate() {
  const r = await post("/api/generate", {input: input.value});
  schema.textContent = r.schema || r.error;
}
async function convert() {
  const schemaText = schema.textContent;
  if (!schemaText || schemaText.startsWith("Error")) { alert("generate first"); return; }
  const r = await post("/api/convert", {
    input: input.value, schema: schemaText, format: format.value,
  });
  report.textContent = r.stdout + r.stderr;
  preview.textContent = JSON.stringify(r.preview, null, 2);
}
</script></body></html>
"""


def _reply(handler, code: int, payload: str, ctype: str = "application/json") -> None:
    body = payload.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class _BaseHandler(BaseHTTPRequestHandler):
    formats_dir: str = "formats"

    def log_message(self, fmt: str, *args) -> None:
        pass  # quiet

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            _reply(self, 200, INDEX_HTML, "text/html")
        else:
            _reply(self, 404, json.dumps({"error": "not found"}))

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/generate":
                self._api_generate(body)
            elif self.path == "/api/convert":
                self._api_convert(body)
            else:
                _reply(self, 404, json.dumps({"error": "not found"}))
        except (ValueError, json.JSONDecodeError) as exc:
            _reply(self, 400, json.dumps({"error": f"bad request: {exc}"}))

    def _api_generate(self, body: dict) -> None:
        input_path = body.get("input", "")
        if not input_path or not os.path.exists(input_path):
            _reply(self, 400, json.dumps({"error": "input file not found"}))
            return
        try:
            data = generator.generate_schema(
                input_path,
                codepage=body.get("codepage", "utf-8"),
                has_header=body.get("has_header"),
            )
        except (OSError, ValueError) as exc:
            _reply(self, 400, json.dumps({"error": str(exc)}))
            return
        text = yaml.safe_dump(data, sort_keys=False)
        _reply(self, 200, json.dumps({
            "schema": text,
            "delimiter": data["delimiter"],
            "has_header": data["has_header"],
        }))

    def _api_convert(self, body: dict) -> None:
        from cli import main as cli_main

        input_path = body.get("input", "")
        schema_text = body.get("schema", "")
        fmt = body.get("format", "json")
        if not input_path or not os.path.exists(input_path):
            _reply(self, 400, json.dumps({"error": "input file not found"}))
            return
        if not schema_text:
            _reply(self, 400, json.dumps({"error": "schema required"}))
            return
        try:
            yaml.safe_load(schema_text)
        except yaml.YAMLError as exc:
            _reply(self, 400, json.dumps({"error": f"invalid schema: {exc}"}))
            return
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = os.path.join(tmp, "schema.yaml")
            with open(schema_path, "w", encoding="utf-8") as fh:
                fh.write(schema_text)
            ext = {"excel": "xlsx"}.get(fmt, fmt)
            out_path = os.path.join(tmp, f"out.{ext}")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli_main([
                    "--schema", schema_path,
                    "--input", input_path,
                    "--output", out_path,
                    "--format", fmt,
                ])
            preview = self._preview(out_path, fmt)
            _reply(self, 200, json.dumps({
                "exit_code": code,
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "preview": preview,
            }))

    @staticmethod
    def _preview(path: str, fmt: str) -> object:
        if not os.path.exists(path):
            return None
        if fmt == "json":
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
            return rows[:5]
        if fmt == "csv":
            with open(path, encoding="utf-8") as fh:
                return fh.read().splitlines()[:6]
        return None  # binary formats have no cheap preview


def make_handler(formats_dir: str):
    return type(
        "WebUiHandler", (_BaseHandler,), {"formats_dir": formats_dir}
    )


def serve(host: str = "127.0.0.1", port: int = 8000, formats_dir: str = "formats") -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(formats_dir))
    actual = httpd.server_address[1]
    print(f"erp-export-normalizer web UI on http://{host}:{actual} (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")