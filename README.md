# erp-export-normalizer

[![CI](https://github.com/lucasgiurastante/erp-export-normalizer/actions/workflows/ci.yml/badge.svg)](https://github.com/lucasgiurastante/erp-export-normalizer/actions/workflows/ci.yml)

Schema-driven, streaming converter for legacy ERP flat files. Fixed-width
records (JD Edwards, SAP exports, mainframe dumps) become clean JSON or CSV —
validated, line-numbered error reports, never loaded fully into memory.

Built for air-gapped environments: no network, no database, no hidden state.
Same input + same schema = same output. Determinism is the audit guarantee.

## Features

- **Schema-first** — a YAML file describes the layout; the parser is generic.
  Knowledge is portable: one shared schema = identical parsing at any company.
- **Streaming** — multi-GB files processed line by line with constant memory.
- **Cumulative validation** — collects every error instead of stopping at the
  first one; report includes the line number of each failure.
- **Codepage-aware** — fields are sliced by byte offset and decoded per field
  (UTF-8, CP850, CP1252, Latin-1, EBCDIC-CP037).
- **Seven output formats** — JSON, CSV, NDJSON, SQL inserts, Parquet (exact
  `decimal128` for financial values), and Excel (write-only, bounded memory).
  Plus **Singer** — tap output (SCHEMA/RECORD/STATE), deterministic, ready
  for Singer targets and Airbyte's CDK.
- **Zero-dependency web UI** — `serve` runs a local, air-gapped interface for
  schema generation and conversion preview (stdlib only, binds to 127.0.0.1).
- **Fixed-width and delimited** — byte-offset slicing or delimiter splitting
  (`,`/`\t`/`;`/`|`), with optional header rows.
- **Auto-detection** — omit `--schema`; the tool scores the built-in schema
  library against your file and picks the best match. Built-in library:
  `jde_ar`, `jde_ap`, `jde_gl` (JD Edwards), `sap_batch`, `sap_fi_document`
  (SAP), `cobol_fixed` (EBCDIC mainframe).
- **Schema generation** — `generate-schema` infers a delimited schema (names,
  types, scale) from an example file.
- **Batch + parallel** — glob inputs with `--output-dir`; `--workers N`
  parallelizes validation with byte-identical output.
- **Business rules** — schema-level `sum` and `balance` (debits = credits)
  checks, evaluated in O(1) memory.
- **Audit evidence** — `--checksum` writes a SHA-256 sidecar (input/output
  hashes, schema version, counts, timestamp).

## Try it now

```bash
pip install erp-export-normalizer
git clone https://github.com/lucasgiurastante/erp-export-normalizer
cd erp-export-normalizer/examples

# JD Edwards export (fixed-width, CP850) -> JSON, no config needed
erp-normalize --input data/jde_ar.txt --output - --format json

# COBOL mainframe dump (EBCDIC) -> NDJSON; decoding happens automatically
erp-normalize --input data/cobol.txt --output - --format ndjson

# binary length-prefixed frames via the bundled plugin
erp-normalize --schema framed_schema.yaml --input data/framed.bin --output - --format ndjson
```

Sample data and the full command set live in
[`examples/`](examples/README.md).
- **DataFrame integration** — `read_erp()` loads exports straight into
  Pandas, Polars, or Spark (explicit `decimal128(38, scale)` schema; requires
  a JVM only at call time).
- **`--dry-run`** — validate without writing output.
- **`--verbose`** — per-line diagnostics (`OK`/`ERR` with reasons).
- **Custom binary parsers (plugins)** — drop a Python `Reader` in `core/plugin_examples/` (or any dir via `--plugins-dir`)
  and reference it from the schema (`parser: <module>`); validation, error
  reports and determinism still apply.
- **Deterministic** — identical input produces identical output, every run.

## Installation

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -e .          # core: JSON/CSV/NDJSON/SQL
pip install -e ".[parquet]"    # + Parquet (pyarrow)
pip install -e ".[excel]"      # + Excel (openpyxl)
pip install -e ".[dataframe]"  # + read_erp() (pandas, polars)
pip install -e ".[spark]"      # + read_erp(backend="spark") (pyspark)
pip install -e ".[lint]"       # + ruff, mypy (development)
pip install -e ".[all]"        # everything
```

## Quick start

```bash
# validate and report errors without writing output
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output /dev/null --dry-run

# convert to JSON
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.json --format json

# convert to CSV
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.csv --format csv

# auto-detect the schema from the built-in library (no --schema)
erp-normalize --input export.txt --output export.json

# more formats: NDJSON, SQL inserts, Parquet, Excel
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.ndjson --format ndjson
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.sql --format sql
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.parquet --format parquet
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.xlsx --format excel

# per-line diagnostics
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.json --verbose --dry-run
```

### Schema generation, batch, and audit

```bash
# infer a delimited schema from an example file, then convert with it
erp-normalize generate-schema example.csv --output example_schema.yaml
erp-normalize --schema example_schema.yaml --input example.csv --output example.json

# batch-convert a whole directory of exports
erp-normalize --schema core/formats/jde_ar.yaml --input "exports/*.txt" --output-dir converted/ --format parquet

# parallel validation (byte-identical output to serial)
erp-normalize --schema core/formats/jde_ar.yaml --input big.txt --output big.json --workers 4

# audit evidence sidecar (<output>.sha256)
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output export.json --checksum

# Singer tap stream (pipe into any Singer target)
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt --output - --format singer | target-postgres

# validate the schema library
erp-normalize registry core/formats/

# zero-dependency web UI (generate schemas without touching YAML)
erp-normalize serve --port 8000
```

A conversion run prints a summary to stdout:

```
records: 3 | ok: 1 | errors: 2
```

and per-line diagnostics to stderr:

```
  line 2: field 'date': invalid date '20251301': month must be in 1..12, not 13
  line 3: length 17 != record_length 44; field 'type': out of range (short line)
```

## Schema format

`core/formats/jde_ar.yaml` — a JD Edwards Accounts Receivable export:

```yaml
format: jde_fixed_width
version: 1.0.0
description: "JD Edwards AR export (Accounts Receivable) - example schema"
record_length: 44
codepage: cp850
table: jde_ar_export
fields:
  - {name: id,       start: 0,  length: 10}
  - {name: type,     start: 10, length: 15}
  - {name: date,     start: 25, length: 8,  type: date,    format: YYYYMMDD}
  - {name: amount,   start: 33, length: 8,  type: decimal, scale: 2, align: right}
  - {name: currency, start: 41, length: 3}
```

Field attributes:

| Attribute   | Required | Meaning                                          |
|-------------|----------|--------------------------------------------------|
| `name`      | yes      | Output column name                               |
| `start`     | yes      | Zero-based byte offset                            |
| `length`    | yes      | Field width in bytes                             |
| `type`      | no       | `string` (default), `date`, `decimal`            |
| `format`    | no       | Date format, e.g. `YYYYMMDD` (required for dates)|
| `scale`     | no       | Decimal places for `decimal` (scaleb semantics)  |
| `align`     | no       | `left` (default) or `right`; padding is stripped  |
| `codepage`  | no       | Per-field codepage override                       |

Schema-level attributes: `format`, `version` (required), `record_length`
(required), `codepage` (default `utf-8`), `description`, and `table` — the
target SQL table name for the `sql` output (validated as an identifier,
defaults to `export`).

Schemas are validated at load time: required keys, duplicate names, overlapping
fields, and overflows past `record_length` are rejected with a clear message.

### Business rules

Rules run over all valid records in O(1) memory and are checked at the end;
violations exit with code 3 and print per-rule messages.

```yaml
rules:
  - {type: sum, field: amount, expected: 123456.78}   # control total
  - {type: balance, positive: debit, negative: credit} # debits = credits
```

### Custom parsers (plugins)

For formats the built-in readers cannot handle — binary records, framed
payloads, packed decimals. A plugin is a Python module in `core/plugin_examples/` exposing
a `Reader` class with a `records()` method yielding `(line_no, record_bytes)`
(the same contract as `parser.FixedWidthReader`). The schema selects it via
`parser`:

```yaml
format: framed
version: 1.0.0
parser: length_prefixed_frame   # core/plugin_examples/length_prefixed_frame.py
codepage: cp1252
fields:
  - {name: id,   start: 0,  length: 4}
  - {name: date, start: 4,  length: 8,  type: date, format: YYYYMMDD}
  - {name: amt,  start: 12, length: 8,  type: decimal, scale: 2, align: right}
```

Field `start`/`length` are advisory for plugins (the plugin decides how to
slice). `record_length` is not required. Validation still runs afterwards, so
plugins get the same cumulative error report and exit codes. Run with
`--plugins-dir` to use a non-default plugin directory.

### DataFrame integration

```python
from core.io import read_erp

df = read_erp("export.txt", schema="core/formats/jde_ar.yaml")  # pandas
pl_df = read_erp("export.txt", backend="polars")  # auto-detect + polars
spark_df = read_erp("export.txt", backend="spark")  # explicit decimal128 schema
```

`on_error="ignore"` drops invalid records instead of raising. The Spark
backend imports lazily — a JVM is needed only when it is called.

## Exit codes

| Code | Meaning                          |
|------|----------------------------------|
| 0    | Success                          |
| 1    | Runtime error (I/O)              |
| 2    | Invalid schema                   |
| 3    | Validation errors found          |

## Architecture

```
input.txt ──► [fixed-width parser] ──► [converters] ──► [validator] ──► [writer]
                    │                      │                │              │
                    │                      ├─ dates          │              ├─ JSON
                    │                      ├─ decimals       │              └─ CSV
                    │                      └─ codepages      └─ report
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design document: module
responsibilities, design rules, user analysis, and the phased roadmap
(NDJSON/Parquet/SQL, auto-detection, schema registry, semantic validation).
See [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md) for the deep dive:
parsing internals, conversion semantics, determinism guarantees, and the
reasoning behind each design decision.

## Development

```bash
# run the test suite (stdlib unittest, discovered by pytest too)
python -m unittest discover -s tests -v
# or
pytest

# lint, format, and typecheck (mirrors CI)
ruff check .
ruff format --check .
mypy cli.py core
```

## Roadmap

- Phase 1 — NDJSON, Parquet, Excel, SQL inserts; heuristic auto-detection;
  built-in schema library; verbose per-line diagnostics. *(Implemented.)*
- Phase 2 — parallel processing for multi-GB files, batch globbing, automatic
  schema inference, Pandas/Polars integration. *(Implemented.)*
- Phase 3 — shared schema registry, semantic business rules, checksums and
  conversion summaries for audit evidence. *(Implemented: rules, audit
  sidecars, local registry verification, and the schema-generation web UI.
  A centralized community registry remains future.)*
- Phase 4 — Airbyte/Singer connector, Databricks/Spark connector, SaaS.
  *(In progress: Singer tap output and the Spark `read_erp` backend done;
  hosted SaaS remains.)*

## License

MIT — see [LICENSE](LICENSE).