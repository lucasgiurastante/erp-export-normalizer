# Technical Design — erp-export-normalizer

A schema-driven, streaming converter for legacy ERP flat files. This document
describes how the tool actually works: the data model, the pipeline, the
semantics of every conversion, and the reasoning behind each design decision.

---

## 1. Design goals

The tool exists to solve one recurring problem: legacy ERPs (JD Edwards, SAP,
mainframe COBOL dumps) export data as flat files whose layout is known only to
the engineers who wrote the parser that year. The layout drifts, the parser
breaks, and the finance team cannot reconcile the books.

Requirements that follow from that problem:

| Goal                    | Consequence in the design                                     |
|-------------------------|---------------------------------------------------------------|
| Replace ad-hoc parsers  | Schema is data (YAML), parser is generic — never hardcoded    |
| Multi-GB files          | Streaming: constant memory, line by line                      |
| Audit / regulated users | Deterministic output, checksums, versioned schemas            |
| Air-gapped environments | No network, no database, no hidden state at runtime           |
| Financial correctness   | Exact decimals, cumulative validation, business rules         |

## 2. Pipeline

```
input ──► [detector] ──► [parser] ──► [converters] ──► [validator] ──► [writer]
             │              │              │                │              │
        schema library   YAML schema   date/decimal/    cumulative     7 formats
        (optional)                     codepage         error report
```

Two entry modes:

- **Schema-first** (`--schema path.yaml`): layout known, validation strict.
- **Auto-detect** (no `--schema`): the detector scores every schema in the
  built-in library against the file and picks the best match; a perfect
  record match wins over all heuristics.

## 3. Data model

### Schema (`core/schema.py`)

A `Schema` is a validated YAML document. Validation is aggressive at load
time — a bad schema fails fast with exit code 2 before reading any input:

- required keys: `format`, `version`
- fields: non-empty list, unique names, no overlaps
- fixed-width: `start`/`length` ints, no overflow past `record_length`
- delimited: no `start`/`length` allowed, `delimiter` non-empty
- codepage ∈ `{utf-8, cp850, cp1252, latin-1, cp037}`
- `table` (SQL target) validated as a SQL identifier
- `rules` validated structurally (type, field, expected values)

### Field (`Field`)

```
name, start, length, type, format, scale, align, codepage_override
```

`start`/`length` are byte offsets, not character offsets (see §4). `scale`
uses *scaleb semantics*: the raw text is the integer mantissa, and the
decimal point is placed `scale` digits from the right — exactly how
mainframe exports encode money.

### Record result (`validator.RecordResult`)

```
line: int          — source line number (1-based, physical)
ok: bool
errors: list[str]  — accumulated, one per problem
fields: list[FieldValue]  — converted values + raw text (for reports)
```

Validation is **cumulative**: a bad date and a short line and a bad decimal in
the same record all get reported, and processing continues to the next record.
The tool never stops at the first error — that is what makes it usable for
10 GB exports with a few thousand bad rows.

## 4. Parsing internals (`core/parser.py`)

Fixed-width records are sliced by **byte offset**, not character offset:

```python
raw = record[f.start : f.start + f.length]
```

Then each slice is decoded **per field** with the field's codepage
(schema-level default, per-field override). Two consequences:

1. Byte offsets are stable regardless of codepage — CP850 and EBCDIC
   multi-byte characters cannot shift the layout.
2. A record is parsed *lazily and destructively*: bytes are consumed once,
   decoded once, and never retained. The only objects alive at any moment are
   the current record's converted fields.

The reader (`FixedWidthReader.records()`) yields `(lineno, bytes)` pairs,
splitting on the platform line terminator and stripping it. Delimited records
are split on the delimiter after decoding the line — the delimiter is a
character, so it must be decoded first.

## 5. Type conversion semantics (`core/converters.py`)

### Dates

`YYYYMMDD` and `YYYY-MM-DD` → ISO `YYYY-MM-DD`. Out-of-range dates
(month 13, day 32) are conversion errors, reported per line. Dates are kept
as strings in every output format — no timezone, no epoch, no local-time
surprises. A date is data, not a `datetime` object.

### Decimals

Two encodings, both from mainframe practice:

- **Implied scale (scaleb)**: text `"00012345"` with `scale: 2` → `123.45`.
  The point is placed by position; no re-scaling.
- **Explicit point**: text `"00123.45"` → `123.45` — the point is honored
  *and* the declared `scale` is ignored, because the source already fixed the
  precision. (This was a deliberate bug fix: earlier versions re-scaled and
  produced `1.2345`.)

Trailing-sign notation (`"12345-"` = negative) is honored. The output type is
`decimal.Decimal` — never `float` — so JSON/NDJSON/Singer serialize exact
values and Parquet stores `decimal128(38, scale)`.

### Strings

Alignment padding is stripped (`align: right` for numbers, `left` otherwise).
No case folding, no whitespace trimming beyond padding — the source layout
said what it said.

## 6. Validation model (`core/validator.py`)

Two passes, both streaming:

1. **Per-record validation** — structural (length, field bounds) and semantic
   (type conversion). Errors accumulate into the record's `errors` list.
2. **End-of-run rule checks** (`core/rules.py`) — `sum` and `balance`
   constraints over all *valid* records, evaluated with O(1) accumulators
   (no second pass over the file, no buffering).

Exit codes encode the outcome: `0` success, `1` runtime/I-O error, `2`
invalid schema, `3` validation errors or rule violations. Exit code 3 is
machine-readable: CI pipelines and reconciliation scripts branch on it.

## 7. Determinism guarantees

Identical input + identical schema ⇒ byte-identical output, every run.

- Records are processed in source order in every mode — including parallel
  (`--workers N`), which reorders *only* which worker validates which chunk,
  never the emitted sequence (§9).
- Writers emit nothing time-dependent. Singer SCHEMA/RECORD/STATE lines
  contain no timestamps (a deliberate deviation from Singer conventions:
  reproducibility over convention).
- The one exception is the audit sidecar, which carries an explicit UTC
  timestamp — but the *data* outputs never do.
- Multiprocessing uses the default (deterministic) hash seed: no
  `PYTHONHASHSEED` dependence anywhere, since no dict iteration order ever
  reaches the output path.

## 8. Streaming and memory

The pipeline is a generator chain: `reader → converter → validator → writer`.
Only one record is materialized at a time.

| Output      | Streaming? | Notes                                            |
|-------------|-----------|--------------------------------------------------|
| JSON array  | Yes       | single `[...]` document, written incrementally   |
| CSV         | Yes       | `csv.writer` with `\r\n` terminator, header      |
| NDJSON      | Yes       | one object per line                              |
| SQL         | Yes       | `INSERT INTO "table" (...) VALUES (...);` batches|
| Parquet     | Buffered  | pyarrow `Table` in memory (scaled by `--workers`)|
| Excel       | Buffered  | openpyxl write-only mode, bounded cell memory    |
| Singer      | Yes       | SCHEMA once, RECORD per line, STATE at end       |

Parquet/Excel trade constant memory for random-access file formats — they
cannot be written as a stream. That is documented, not hidden: a 10 GB
export to Parquet is a job for `--workers`, not a single-process default.

## 9. Concurrency (`core/parallel.py`)

`--workers N` splits the record stream into chunks, validates each chunk in a
`ProcessPoolExecutor`, and re-emits results **in source order** by queueing
chunks and releasing them in order. Properties:

- Byte-identical output to serial mode (asserted by tests).
- Workers share nothing; each chunk is independent, so the accumulator-based
  rule engine runs on the merged result in the parent afterwards.
- Memory bounded by `workers × chunk_size` records in flight.

## 10. Detection and inference

### Detector (`core/detector.py`)

Scores each library schema against the first N records. A **perfect match**
(a record that parses cleanly, every field validated, correct length) wins
unconditionally; otherwise a weighted heuristic (length fit, decimal
conformance, date conformance) picks the best. Detection requires at least
one perfect record — conservative by design: better to fail with exit 1 and a
message than to silently misparse money.

### Generator (`core/generator.py`)

`generate-schema` infers a *delimited* schema from an example file: delimiter
(scores `,` `\t` `;` `|`), header detection, per-column name, and type
inference (date / decimal with scale / string) from non-empty values. The
output is a valid, loadable schema — round-trippable through the same
validator.

## 11. Output formats (`core/writer.py`)

| Format  | Escape/encode rules                                       |
|---------|-----------------------------------------------------------|
| JSON    | `ensure_ascii=True`, `Decimal` → exact string (never float)|
| CSV     | `QUOTE_MINIMAL`, `\r\n`, field values are strings         |
| NDJSON  | one JSON object per line, same decimal rule               |
| SQL     | identifiers double-quoted, values string-quoted, `''` doubled |
| Parquet | explicit `decimal128(38, scale)` schema; no float coercion|
| Excel   | write-only workbook, column widths set once               |
| Singer  | SCHEMA message (type, format, scale per field) → RECORD → STATE |

The Parquet schema is explicit (not inferred): `decimal128(38, scale)` for
`decimal` fields, `string` for everything else — including dates, which stay
ISO strings for cross-format consistency.

## 12. Audit & traceability (`core/audit.py`)

`--checksum` writes `<output>.sha256` beside the output:

```
input_sha256=...      output_sha256=...
schema=formats/jde_ar.yaml
schema_version=1.0.0  records=3  ok=1  errors=2
rules=ok              timestamp=2026-08-20T...Z
```

Contents: SHA-256 of input and output, schema path + version, record counts,
rule outcome, UTC timestamp. This is the audit artifact: a regulator can
recompute the input hash, re-run the conversion, and verify the output hash
matches. Deterministic data + explicit metadata = reproducible evidence.

## 13. Performance characteristics

| Operation          | Cost                          |
|--------------------|-------------------------------|
| Parse record       | O(fields) byte slices         |
| Validate record    | O(fields)                     |
| Rules accumulation | O(1) per record               |
| Memory             | O(record size), O(chunk) parallel |
| Detection          | O(1) schemas × O(N sampled)   |

The dominant cost is byte decoding (CP850/EBCDIC per field). Everything else
is linear in input size with a small constant.

## 14. Air-gap and security model

- Runtime performs **no network I/O**. Dependencies beyond the Python
  stdlib + PyYAML are optional and off by default (`parquet`, `excel`,
  `dataframe`, `spark` extras).
- The web UI (`serve`) binds to `127.0.0.1`, serves no remote content, and
  performs no filesystem writes outside the requests it is given.
- Output SQL is escaped, never interpolated raw; schema table names are
  validated as identifiers before use.
- The tool never executes schema-provided code: schemas are data, rules are
  a closed vocabulary (`sum`, `balance`).

## 15. DataFrame / Spark integration (`core/io.py`)

`read_erp()` validates the file first (raising `ErpValidationError` on
invalid records unless `on_error="ignore"`), then materializes:

- `backend="pandas"` (default) — `pandas.DataFrame`
- `backend="polars"` — `polars.DataFrame`
- `backend="spark"` — Spark DataFrame with an **explicit schema**:
  `DecimalType(38, scale)` for decimal fields, `StringType` for everything
  else (dates stay ISO strings, matching every other output format)

The Spark path requires a JVM at call time only — the module imports lazily,
so the core tool stays JVM-free.

## 16. Design decisions & tradeoffs

| Decision                        | Why, and what it costs                          |
|---------------------------------|-------------------------------------------------|
| Schema-first, not code-first    | Portable knowledge, shared across companies; costs: authoring YAML  |
| Byte-offset slicing             | Codepage-safe; costs: non-obvious to beginners  |
| Scaleb semantics                | Matches mainframe encodings exactly; costs: surprises if source is already scaled |
| Decimals never floats           | Financial correctness; costs: no numpy fast paths |
| Cumulative validation           | Usable on 10 GB files; costs: can't short-circuit |
| Deterministic by default        | Audit-grade reproducibility; costs: no "current time" in outputs |
| Optional heavy deps             | Core stays dependency-light; costs: feature discovery |
| Streaming until impossible      | Constant memory; costs: Parquet/Excel buffering is explicit |

## 17. Extensibility

- **New schema** — drop a YAML in `formats/` (or point `--schema` at any
  file). No code.
- **New format** — parse two lines of `formats/*.yaml` (`format:` + field
  layout). No code.
- **New output writer** — implement the `write(result)` / `finish()`
  protocol, register in `make_writer()`. One file.
- **New rule type** — extend the closed vocabulary in `core/rules.py`
  (accumulators stay O(1) by construction).

Future phases: transformer/mapper stages (field renames, joins, lookups),
a centralized community registry, and a hosted SaaS layer — all documented
in [ARCHITECTURE.md](ARCHITECTURE.md).