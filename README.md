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
  `jde_ar`, `jde_ap`, `jde_gl` (JD Edwards), `sap_batch`, `sap_fi_document`,
  `sap_fi_bseg` (SAP), `jde_gl_distinct`, `cobol_fixed` (EBCDIC mainframe)
  and `cobol_packed` (EBCDIC with COMP-3 balances).
- **Schema generation** — `generate-schema` infers a delimited schema (names,
  types, scale) from an example file.
- **COBOL copybook import** — `copybook` turns a copybook's `FD`/`PIC` clauses
  into a ready schema, so the record layout stays in the client's own COBOL
  source. `COMP-3` packed decimals, `COMP`/`BINARY` and implied decimal points
  are resolved.
- **COMP-3 packed decimals** — new `type: packed` for mainframe BCD fields
  (`PIC S9(7)V99 COMP-3` → 5 bytes, `scale: 2`).
- **Cross-file reconciliation** — `crosscheck` compares totals, row counts and
  key sets across two or more exports in bounded memory.
- **Row-level diff** — `diff` shows what actually changed between two exports:
  added, removed, and field-level changes, with capped samples and exact counts.
- **Resumable Singer tap** — `STATE` carries a real bookmark; `--state` resumes
  an interrupted extract and produces byte-identical output.
- **Batch + parallel** — glob inputs with `--output-dir`; `--workers N`
  parallelizes validation with byte-identical output.
- **Business rules** — schema-level `sum` and `balance` (debits = credits)
  checks, evaluated in O(1) memory.
- **Performance gate in CI** — a relative regression gate against a committed
  baseline, plus an absolute determinism check across worker configurations.
- **Audit evidence** — `--checksum` writes a SHA-256 sidecar (input/output
  hashes, schema version, counts, timestamp).
- **Duplicate key detection** — `--key id` fails a run that repeats a primary
  key, reporting the line and the first sighting.
- **Per-field text normalization** — `trim`, `case` and unicode `normalize`
  (NFC/NFD/NFKC/NFKD) per field, for legacy exports that spell the same
  value several ways.
- **PII masking** — `mask: full | partial | hash` per field, so a fixture can
  be shared without leaking it. Masked values are always strings.
- **Versioned schema registry** — `registry search` / `registry verify`
  against a local index with checksums. See
  [docs/RFC_REGISTRY.md](docs/RFC_REGISTRY.md).
- **Postgres destination** — `--postgres-dsn` loads records straight into a
  table in input order, `numeric` for amounts, with a load report.

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

# resumable Singer tap: a STATE bookmark every 1000 records
erp-normalize --schema core/formats/jde_ar.yaml --input huge.txt \
  --output out.jsonl --format singer --state-every 1000
# ...the run died. Pick it up where the target left off:
erp-normalize --schema core/formats/jde_ar.yaml --input huge.txt \
  --output out.jsonl --format singer --state out.jsonl.state

# validate the schema library
erp-normalize registry core/formats/
# ...or check a versioned index: checksums + schema validity
erp-normalize registry verify registry.yaml
erp-normalize registry search registry.yaml cobol

# fail the run if a primary key repeats (line + first sighting reported)
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt \
  --output out.json --key id

# an empty extract is almost always a broken feed, not a quiet day
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt \
  --output out.json --fail-on-empty

# load straight into Postgres, streaming, amounts as numeric
pip install 'erp-export-normalizer[postgres]'
erp-normalize --schema core/formats/jde_ar.yaml --input export.txt \
  --postgres-dsn postgresql://user@host/db --batch-size 500

# zero-dependency web UI (generate schemas without touching YAML)
erp-normalize serve --port 8000
#   /         schema generation + conversion preview
#   /validate per-line error table with the offending raw value
```

### Normalizing and masking, per field

Legacy exports spell the same value several ways, and some fields cannot be
handed out as they are. Both are explicit, per field, and validated when the
schema loads:

```yaml
fields:
  - {name: customer, start: 0,  length: 20, trim: true, case: upper}
  - {name: cuit,     start: 20, length: 11, mask: partial, mask_keep: 3}
  - {name: doc,      start: 31, length: 12, mask: hash}
```

`normalize: NFKC` folds full-width characters and composed accents. `case`
and `normalize` only apply to `string` fields — applying them to a decimal
would silently reorder what the schema says. A masked value is always
written as a string, so the audit trail cannot imply a number survived.

### Importing a COBOL copybook

The record layout usually already exists in the client's COBOL source. Import
it instead of retyping the offsets:

```bash
erp-normalize copybook CUSTFILE.cpy --output cust.yaml --name cust_export
erp-normalize --schema cust.yaml --input export.bin --output cust.json
```

The importer resolves `PIC` clauses and `USAGE` keywords:

| COBOL | schema field |
|---|---|
| `PIC X(20)` | `string`, 20 bytes |
| `PIC 9(8)` | `date` `YYYYMMDD` (or `decimal` with `--no-date`) |
| `PIC S9(7)V99 COMP-3` | `packed`, `scale: 2`, 5 bytes |
| `PIC S9(5) COMP` | `decimal`, machine-word width |

`V` is the implied decimal point, so `S9(7)V99` is 7 integer digits plus 2
decimals — 9 positions. Only `(n)` repeats a position; the `99` after `V` is
two positions, not ninety-nine.

### Reconciling and diffing two exports

A file can pass every per-line check and still be wrong. `crosscheck` asks
whether two exports reconcile; `diff` asks what actually changed.

```bash
# do the statement and the ledger agree on totals, counts and keys?
erp-normalize crosscheck \
  core/formats/jde_ar.yaml=statement.txt \
  core/formats/jde_ar.yaml=ledger.txt \
  --key id --sum amount --checks count,sum:amount,unique,missing

# what changed between yesterday and today?
erp-normalize diff \
  core/formats/jde_ar.yaml=yesterday.txt \
  core/formats/jde_ar.yaml=today.txt \
  --key id --fields amount,date
```

```
added=1 removed=1 changed=1 identical=1
  + AR1004
  - AR1003
  ~ AR1001: amount: 1234.56 -> 1500
```

Both commands keep only one index of keys in memory, never the file itself,
and `--max-keys` makes them fail loudly rather than index without limit. They
exit `3` when something does not reconcile, so a pipeline can gate on them.
`--json` emits the report for machine consumption.

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

The full contract is frozen and versioned in
[docs/PLUGIN_API_v1.md](docs/PLUGIN_API_v1.md): discovery, the mandatory
`Reader(schema, path)` signature, `records(skip_first=False)` yielding
`(lineno, bytes)`, raw-bytes semantics, the error contract (including wrapping
a bad constructor as `PluginError`), and the semver rules for `v1.x`. Read it
before publishing a third-party parser.

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

`crosscheck` and `diff` also exit `3` when the exports do not reconcile, and a
conversion exits `3` with `--fail-on-empty` when the input yields no records,
so a pipeline can gate on a single code. Without the flag an empty input still
exits `0`, but the run prints a warning saying whether the file was empty or
held records that were all rejected.

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

# performance profile: relative regression gate + absolute determinism check
python scripts/perf_profile.py --lines 100000 \
  --baseline perf-baseline.json --tolerance 0.5
# refresh the committed baseline on purpose, not by accident
python scripts/perf_profile.py --lines 100000 --update-baseline perf-baseline.json
```

The performance gate is deliberately **relative**: shared CI runners are
noisy, and an absolute wall-clock threshold produces flaky failures nobody
trusts. A baseline recorded on a different machine is detected and the
regression verdict is **skipped** rather than failed — the difference would be
hardware, not code. Pass `--require-same-machine` to make that a hard error.
The determinism check is **absolute** and never relaxes — every worker
configuration must produce byte-identical output.

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
  *(In progress: the Singer tap is done including resumable `STATE`;
  the Spark `read_erp` backend is done. Hosted SaaS remains out of scope.)*
- Phase 5 — reconciliation, diffing, COBOL tooling. *(Implemented: `crosscheck`,
  `diff`, copybook import, COMP-3 packed decimals.)*

## License

MIT — see [LICENSE](LICENSE).