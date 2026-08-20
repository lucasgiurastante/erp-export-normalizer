# Architecture: erp-export-normalizer

Core: schema-driven, streaming, validation with report

```
input.txt ──► [detector] ──► [parser (schema YAML)] ──► [decoder/validator] ──► [transformer] ──► [writer]
                  │                    │                       │                    │              │
                  │                    └── formats/            └── converters       └── mappers     ├── JSON
                  │                                            (date/decimal/                     ├── CSV
                  │                                             codepage/EBCDIC)                   ├── NDJSON
                  └── auto-detect                                                                  ├── Parquet
                                                                                                   └── SQL inserts
```

**Key decision: schema-first.** Formats are never hardcoded. The YAML describes
the file; the parser is generic. Every company has its own variant of "JD
Edwards fixed-width" — the schema *is* the portable knowledge.

> Note: the pipeline above shows the full target architecture. The current
> implementation (Phases 0-4 core, minus hosted SaaS) runs:
> `[parser] → [converters] → [validator] → [writer]` with JSON, CSV, NDJSON,
> SQL, Parquet, Excel, and Singer output; heuristic auto-detection
> (`core/detector.py`); schema inference (`core/generator.py`); parallel
> validation (`core/parallel.py`); business rules (`core/rules.py`); audit
> sidecars (`core/audit.py`); a zero-dependency web UI (`core/webui.py`); and
> Pandas/Polars/Spark integration (`core/io.py`). Deep-dive internals:
> [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md).

## Modules

| Module              | Responsibility                                                                 |
|---------------------|--------------------------------------------------------------------------------|
| `cli.py`            | Entry point, argparse, exit codes (0 ok / 1 error / 2 invalid schema / 3 validation) |
| `core/schema.py`    | Load/validate YAML (start, length, type, format, codepage, version)            |
| `core/parser.py`    | Fixed-width field slicing, streaming line by line                              |
| `core/detector.py`  | Heuristic auto-detection against the schema library (Phase 1)                 |
| `core/converters.py`| Dates (YYYYMMDD→ISO), decimals (scale), codepages (CP850/EBCDIC→UTF-8)         |
| `core/validator.py` | Cumulative errors (does not stop at the first), line-numbered report           |
| `core/writer.py`    | JSON/CSV/NDJSON/SQL + Parquet/Excel (optional deps)                           |
| `core/generator.py` | Schema inference from example files (delimited) (Phase 2)                     |
| `core/parallel.py`  | Chunked multiprocessing with deterministic ordering (Phase 2)                 |
| `core/io.py`        | `read_erp()` → Pandas/Polars/Spark DataFrames (Phase 2/4)         |
| `core/rules.py`     | Business rules: sum / balance, O(1) memory (Phase 3)                          |
| `core/audit.py`     | SHA-256 hashes + conversion summary sidecar (Phase 3)                         |
| `core/webui.py`     | Zero-dependency web UI: schema generation + preview (Phase 3/4)               |
| `formats/`          | Built-in schema library (JDE AR, SAP batch, etc.)                              |
| `plugins/`          | Hooks for custom parsers (rare binary formats) — future phase                  |

## Design rules

- **Streaming** — multi-GB files must not fit in RAM. Process line by line.
- **Idempotent / deterministic** — same input → same output. Critical for audit.
- **No network, no database** — runs in air-gapped environments. This is the vendor.
- **`--dry-run` + report** — validate without generating output; record/error counts.
- **100% portable schemas** — one shared YAML = the exact same parser at another company.

## Schema format

```yaml
format: jde_fixed_width
record_length: 44
codepage: cp850
version: 1.0.0
fields:
  - {name: id,       start: 0,  length: 10}
  - {name: type,     start: 10, length: 15}
  - {name: date,     start: 25, length: 8,  type: date,    format: YYYYMMDD}
  - {name: amount,   start: 33, length: 8,  type: decimal, scale: 2, align: right}
  - {name: currency, start: 41, length: 3}
```

`version` is required: schemas are versioned artifacts, and the version is part
of the audit trail for regulated users (banking, health).

## Users: beneficiaries vs. affected

### Beneficiaries (in order of value)

- **Data engineers / BI** — integrate legacy ERPs with Snowflake/BigQuery/Airbyte.
  Today they write ad-hoc parsers that break; this tool is repeatable and testable.
- **Migration teams** — leaving old JDE/SAP requires exporting everything to a
  modern format. Phase 1 of the project.
- **Implementation consultants** — bill by integration; a free tool means less
  code to write and more margin.
- **Finance / reconciliation** — clean audit of exports, sum-equals-sum validation.
- **SMBs without APIs** — their ERP only exports `.txt`. This is their only door
  to modernity.
- **Regulated industries** (banking, health) — need traceability: what was
  converted, when, and with which schema version.

### Affected / risk

- Consultancies selling proprietary converters — direct free competition. Not
  your problem.
- **Misuse = financial data loss** — misparse a decimal and books don't balance.
  → This is where validation becomes the flagship feature.
- **Regulated users need guarantees** → versioned schemas + checksum + report =
  audit evidence.

## Evolution phases

- **Phase 0 — MVP (2 days).** Fixed-width → JSON/CSV, YAML schema, tests,
  example. Impact: individual developers. *(Implemented.)*
- **Phase 1 — Adoption (2–4 weeks).** NDJSON, Parquet, Excel, SQL inserts;
  heuristic format auto-detection; built-in schema library (JDE AR/AP, SAP
  batch); verbose per-line diagnostics. Impact: full data teams. *(Implemented.)*
- **Phase 2 — Scale (1–2 months).** Parallelism for GBs, batch globbing,
  automatic schema inference from example files, Pandas/Polars integration.
  Impact: real enterprise ETLs. *(Implemented.)*
- **Phase 3 — Network effects (2–4 months).** Central schema registry (community
  shares formats), semantic business rules (debits=credits, totals, cross
  references), checksums + conversion summary (audit evidence), web UI for
  schema generation. Impact: the niche becomes "critical". *(Implemented:
  rules, audit sidecars, local registry verification, web UI. A centralized
  community registry remains future.)*
- **Phase 4 — Ecosystem (6+ months).** Airbyte/Singer source connector,
  Databricks/Spark connector, cloud/SaaS for non-developers, schema marketplace.
  Impact: de facto standard for legacy flat files. *(In progress: Singer tap
  output and the Spark `read_erp` backend implemented; hosted SaaS and the
  marketplace remain.)*

## Why this roadmap wins

Each phase raises the ceiling: developer → team → company → standard. Phase 3 is
where the defensible position forms: semantic validation + registry = "the
ecosystem silently depends on this". Credibility signals (CI, shared schemas,
real issues) emerge naturally from development.