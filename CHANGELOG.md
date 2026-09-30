# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semver](https://semver.org/).

## [0.3.0] - 2026-09-30

The COBOL and reconciliation round, plus a crash fix the tests found.

### Added

- **COBOL COMP-3 packed decimals.** New `type: packed` decodes mainframe BCD
  fields. `PIC S9(7)V99 COMP-3` is 5 bytes with `scale: 2`. Credit and debit
  suffixes (`1234.56CR`, `1234.56DB`) are understood, which is how JD Edwards
  writes signed amounts. EBCDIC overpunch is available per field with
  `overpunch: true`.
- **COBOL copybook import.** `erp-normalize copybook FILE.cpy --output
  schema.yaml` turns a copybook's `FD`/`PIC` clauses into a schema. `COMP-3`,
  `COMP`/`BINARY` and implied decimal points are resolved. Only `(n)` repeats a
  position, so the `99` in `S9(7)V99` is two decimals, not ninety-nine.
- **Cross-file reconciliation.** `crosscheck` gained a `references:<field>`
  check for referential integrity between a master and a detail file, plus
  `crosscheck` and `diff` commands that compare totals, row counts, key sets
  and field-level changes across two or more exports.
- **Resumable Singer tap.** `STATE` carries a real bookmark instead of `{}`.
  `--state` resumes an interrupted extract and the resumed output is
  byte-identical to an uninterrupted run.
- **Compressed input.** `.gz` and `.bz2` files are decompressed on read,
  detected by magic bytes rather than extension. A zip is detected and refused
  with instructions, because picking a member from it would be a guess.
- **Per-field text options.** `trim`, `case` and unicode `normalize`
  (NFC/NFD/NFKC/NFKD), and `mask: full | partial | hash` so fixtures can be
  shared without leaking data. A masked value is always written as a string.
- **Per-field constraints.** `required`, `min`, `max`, `pattern` and `enum`,
  validated when the schema loads. Conversion failures and constraint failures
  are reported in different words, so a corrupt file is distinguishable from an
  out-of-policy one. See [docs/RFC_FIELD_CONSTRAINTS.md](docs/RFC_FIELD_CONSTRAINTS.md).
- **Duplicate key detection.** `--key id` fails a run that repeats a key and
  reports the line plus the first sighting.
- **Empty input check.** `--fail-on-empty` exits 3 when no record is produced,
  and a warning always says whether the file was empty or held records that
  were all rejected.
- **Versioned schema registry.** `registry search` and `registry verify` over
  a local index with checksums, plus `registry.yaml` covering the ten built-in
  layouts. See [docs/RFC_REGISTRY.md](docs/RFC_REGISTRY.md).
- **Postgres destination.** `--postgres-dsn` streams records into a table in
  input order, `numeric` for amounts, with a load report that distinguishes
  rows written from rows committed.
- **Excel native types.** Dates and amounts land as real date and number cells
  instead of text, so `SUM()` works on the column.
- **Property-based tests.** 19 generators assert that no input, however odd,
  produces a traceback. See [tests/corpus/README.md](tests/corpus/README.md).

### Fixed

- `JsonWriter` accumulated every row in memory instead of streaming. A
  million-record JSON file no longer needs a million dicts.
- The schema auto-detector sampled 5 records and stopped at the first failure.
  It now samples 20, scores per field even when a record fails, and returns
  `None` with a message when two candidates are too close to call.
- Business rules silently ignored a `string` where a `Decimal` was expected.
  That is now a violation, and an invalid rule spec fails at load time.
- An undecodable byte raised a raw `UnicodeDecodeError` and killed the whole
  run. It is now a field error naming the byte and its offset.
- The docs listed `[lint]` as an install extra, which no longer existed.

## [0.2.0] - 2026-09-29

Plugin API v1, cross-file tooling, and the singer state work.

### Added

- **Plugin API v1, frozen.** `docs/PLUGIN_API_v1.md` fixes the contract:
  discovery, the mandatory `Reader(schema, path)` signature,
  `records(skip_first=False)` yielding `(lineno, bytes)`, raw-bytes semantics,
  the error contract and the semver rules. A `TypeError` from an incompatible
  constructor surfaces as `PluginError`.
- `crosscheck` and `diff` commands.
- `--chunk-lines` and `scripts/bench_parallel.py`.
- Built-in schemas `sap_fi_bseg`, `jde_gl_distinct` and `cobol_packed`.
- Error reports carry the field and the raw value, truncated to 50 characters,
  with the `line N:` prefix that existing scripts parse.
- A Spark test that needs no JVM, using a mocked `SparkSession`.

### Fixed

- A lint failure that would have broken the `lint` CI job: nine files from the
  previous round were never formatted.

## [0.1.0] - 2026-09-28

First public release. Fixed-width to JSON and CSV, YAML schemas, cumulative
validation with line numbers, NDJSON, Parquet, Excel, SQL inserts, auto
detection, the schema-generation web UI, a Pandas/Polars/Spark loader and the
first release on PyPI.

[0.3.0]: https://github.com/lucasgiurastante/erp-export-normalizer/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/lucasgiurastante/erp-export-normalizer/releases/tag/v0.2.0
[0.1.0]: https://github.com/lucasgiurastante/erp-export-normalizer/releases/tag/v0.1.0
