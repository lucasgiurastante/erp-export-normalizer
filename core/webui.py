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
  if (!schemaText || schemaText.startsWith("Error")) {
    alert("generate first");
    return;
  }
  const r = await post("/api/convert", {
    input: input.value, schema: schemaText, format: format.value,
  });
  report.textContent = r.stdout + r.stderr;
  preview.textContent = JSON.stringify(r.preview, null, 2);
}
</script></body></html>
"""


def collect_errors(
    schema_path: str,
    input_path: str,
    plugins_dir: str = "core/plugin_examples",
    max_errors: int = 200,
) -> dict:
    """Validate a file and return errors as structured rows.

    The CLI already reports `line N: field 'X': msg | raw='...'`; this
    reuses the same per-line detail so the screen and the terminal never
    disagree about what failed. Output is capped at `max_errors` with an
    exact count, because a broken layout produces an error per line and a
    browser is a worse place to read 400 000 of them than a log file.
    """
    from cli import make_reader  # noqa: PLC0415 - avoids an import cycle
    from core import schema as schema_mod
    from core import validator as validator_mod

    sch = schema_mod.load_schema(schema_path)
    reader = make_reader(sch, input_path, plugins_dir)
    val = validator_mod.Validator(sch)
    stats = validator_mod.Stats()
    errors: list[dict] = []
    for lineno, record in reader.records(skip_first=bool(sch.has_header)):
        result = val.validate_record(lineno, record)
        stats.add(result)
        if result.ok:
            continue
        for detail in result.details:
            if len(errors) >= max_errors:
                break
            errors.append(
                {
                    "line": result.line,
                    "field": detail.get("field") or "",
                    "message": detail.get("message", ""),
                    "raw": detail.get("raw", ""),
                }
            )
    report = stats.report()
    return {
        "schema": os.path.basename(schema_path),
        "input": os.path.basename(input_path),
        "errors": errors,
        "error_count": report["errors"],
        "total": report["total"],
        "ok": report["ok"],
        "truncated": report["errors"] > len(errors),
    }


VALIDATE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>erp-export-normalizer - validation</title>
<style>
body{font-family:ui-monospace,Menlo,monospace;background:#111;color:#ddd;
margin:0;padding:1.5rem}
h1{font-size:1.2rem}
form{display:grid;grid-template-columns:2fr 2fr auto;gap:.5rem;align-items:end;
max-width:60rem;margin-bottom:1rem}
label{display:block;font-size:.75rem;color:#999;margin-bottom:.2rem}
input{width:100%;box-sizing:border-box;background:#1c1c1c;color:#eee;
border:1px solid #444;padding:.4rem;font:inherit}
button{background:#1c1c1c;color:#eee;border:1px solid #444;padding:.4rem 1rem;
font:inherit;cursor:pointer}
.summary{font-size:.85rem;margin:.6rem 0}
.summary .ok{color:#5f5}.summary .bad{color:#f66}
table{border-collapse:collapse;width:100%;max-width:70rem;font-size:.8rem}
th,td{border:1px solid #333;padding:.3rem .45rem;text-align:left;
vertical-align:top}
th{background:#1c1c1c;position:sticky;top:0}
td.line{text-align:right;color:#888;width:4rem;cursor:pointer}
td.field{color:#7cf;width:9rem;word-break:break-all}
td.raw{color:#999;word-break:break-all;width:22rem;white-space:pre-wrap}
td.raw mark{background:#7a4; color:#111}
tr:hover td{background:#1a1a1a}
.note{color:#666;font-size:.75rem;margin-top:1rem}
</style></head><body>
<h1>Validation report</h1>
<form onsubmit="run(event)">
 <div><label>Schema path</label>
  <input id="schema" value="core/formats/jde_ar.yaml"></div>
 <div><label>Input path</label>
  <input id="input" value="examples/data/jde_ar.txt"></div>
 <button type="submit">Validate</button>
</form>
<div class="summary" id="summary">Enter a schema and an input file.</div>
<table><thead><tr>
<th>line</th><th>field</th><th>message</th><th>raw value</th>
</tr></thead><tbody id="rows"></tbody></table>
<p class="note">Server-local paths only. Nothing leaves this machine.</p>
<script>
function highlight(cell, raw, message) {
  // Pull the offending token out of the message when we can: the error text
  // quotes it, and seeing it sit inside the raw slice is the whole point.
  const m = /'([^']+)'/.exec(message);
  const token = m ? m[1] : "";
  if (!token || token.length > raw.length) { cell.textContent = raw; return; }
  const at = raw.indexOf(token);
  if (at === -1) { cell.textContent = raw; return; }
  cell.append(raw.slice(0, at));
  const mark = document.createElement("mark");
  mark.textContent = token;
  cell.append(mark, raw.slice(at + token.length));
}
async function run(ev) {
  ev.preventDefault();
  const body = {schema: document.getElementById("schema").value,
                input: document.getElementById("input").value};
  const r = await fetch("/api/validate", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await r.json();
  const tbody = document.getElementById("rows");
  tbody.replaceChildren();
  const summary = document.getElementById("summary");
  if (data.error) {
    summary.textContent = "error: " + data.error;
    return;
  }
  summary.replaceChildren();
  const bad = data.error_count > 0;
  const head = document.createElement("span");
  head.className = bad ? "bad" : "ok";
  head.textContent = data.error_count
    ? data.error_count + " error(s) in " + data.total + " record(s)"
    : "no errors in " + data.total + " record(s)";
  summary.append(head);
  if (data.truncated) {
    const more = document.createElement("span");
    more.textContent = " - showing the first " + data.errors.length;
    summary.append(more);
  }
  for (const e of data.errors) {
    const tr = document.createElement("tr");
    const line = document.createElement("td");
    line.className = "line"; line.textContent = e.line;
    const field = document.createElement("td");
    field.className = "field"; field.textContent = e.field;
    const msg = document.createElement("td");
    msg.textContent = e.message;
    const raw = document.createElement("td");
    raw.className = "raw"; highlight(raw, e.raw, e.message);
    tr.append(line, field, msg, raw);
    tbody.append(tr);
  }
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
        elif self.path in ("/validate", "/validate.html"):
            _reply(self, 200, VALIDATE_HTML, "text/html")
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
            elif self.path == "/api/validate":
                self._api_validate(body)
            else:
                _reply(self, 404, json.dumps({"error": "not found"}))
        except (ValueError, json.JSONDecodeError) as exc:
            _reply(self, 400, json.dumps({"error": f"bad request: {exc}"}))

    def _api_validate(self, body: dict) -> None:
        schema_path = body.get("schema", "")
        input_path = body.get("input", "")
        if not os.path.exists(input_path):
            _reply(self, 400, json.dumps({"error": "input file not found"}))
            return
        if not os.path.exists(schema_path):
            _reply(self, 400, json.dumps({"error": "schema file not found"}))
            return
        try:
            report = collect_errors(schema_path, input_path, self.formats_dir)
        except (OSError, ValueError) as exc:
            _reply(self, 400, json.dumps({"error": str(exc)}))
            return
        _reply(self, 200, json.dumps(report))

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
        _reply(
            self,
            200,
            json.dumps(
                {
                    "schema": text,
                    "delimiter": data["delimiter"],
                    "has_header": data["has_header"],
                }
            ),
        )

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
                code = cli_main(
                    [
                        "--schema",
                        schema_path,
                        "--input",
                        input_path,
                        "--output",
                        out_path,
                        "--format",
                        fmt,
                    ]
                )
            preview = self._preview(out_path, fmt)
            _reply(
                self,
                200,
                json.dumps(
                    {
                        "exit_code": code,
                        "stdout": stdout.getvalue(),
                        "stderr": stderr.getvalue(),
                        "preview": preview,
                    }
                ),
            )

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
    return type("WebUiHandler", (_BaseHandler,), {"formats_dir": formats_dir})


def serve(
    host: str = "127.0.0.1", port: int = 8000, formats_dir: str = "formats"
) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(formats_dir))
    actual = httpd.server_address[1]
    print(f"erp-export-normalizer web UI on http://{host}:{actual} (Ctrl-C to stop)")
    print(f"  schema + preview: http://{host}:{actual}/")
    print(f"  validation view:  http://{host}:{actual}/validate")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
