"""erp-export-normalizer - CLI entry point.

Pipeline: input -> [parser] -> [converters] -> [validator] -> [writer]
Streaming, line by line; cumulative validation with a line-numbered report.

The schema can be supplied explicitly (--schema) or auto-detected against
the built-in schema library (formats/).

Exit codes: 0 success / 1 runtime error / 2 invalid schema / 3 validation errors.
"""
from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext

from core import detector, parser, schema as schema_mod, validator, writer

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SCHEMA = 2
EXIT_VALIDATION = 3

TEXT_FORMATS = {"json", "csv", "ndjson", "sql"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="erp-normalize",
        description=(
            "Convert legacy flat files (fixed-width) to "
            "JSON/CSV/NDJSON/SQL/Parquet/Excel using a YAML schema."
        ),
    )
    ap.add_argument(
        "--schema",
        help="path to the YAML schema (omit for auto-detection against formats/)",
    )
    ap.add_argument(
        "--formats-dir",
        default=detector.DEFAULT_FORMATS_DIR,
        help="schema library directory used for auto-detection",
    )
    ap.add_argument("--input", required=True, help="input flat file")
    ap.add_argument("--output", required=True, help="output path ('-' = stdout, text formats only)")
    ap.add_argument(
        "--format",
        choices=["json", "csv", "ndjson", "sql", "parquet", "excel"],
        default="json",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="print per-line diagnostics to stderr (OK/ERR per record)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="validate only, produce no output; print error report",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.schema:
        try:
            sch = schema_mod.load_schema(args.schema)
        except (OSError, schema_mod.SchemaError) as exc:
            print(f"schema error: {exc}", file=sys.stderr)
            return EXIT_SCHEMA
    else:
        try:
            found = detector.Detector(args.formats_dir).detect(args.input)
        except OSError as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if found is None:
            print(
                "no schema matched the input; pass --schema or extend formats/",
                file=sys.stderr,
            )
            return EXIT_ERROR
        sch = found.schema
        print(f"detected schema: {found.source_path} (score {found.score})")

    val = validator.Validator(sch)
    stats = validator.Stats()

    if args.dry_run:
        out = None
        out_fh = nullcontext(sys.stdout)
    elif args.format in TEXT_FORMATS:
        try:
            out_fh = open(args.output, "w", encoding="utf-8", newline="")
        except OSError as exc:
            print(f"output error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        try:
            out = writer.make_writer(args.format, sch, out_fh)
        except ValueError as exc:
            print(f"output error: {exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        out_fh = nullcontext(sys.stdout)
        try:
            out = writer.make_writer(args.format, sch, args.output)
        except (OSError, ValueError) as exc:
            print(f"output error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    with out_fh:
        reader = parser.FixedWidthReader(sch, args.input)
        try:
            for lineno, record in reader.records():
                result = val.validate_record(lineno, record)
                stats.add(result)
                if args.verbose:
                    status = "OK" if result.ok else "ERR"
                    details = f" [{'; '.join(result.errors)}]" if result.errors else ""
                    print(f"  line {lineno}: {status}{details}", file=sys.stderr)
                if result.ok and out is not None:
                    out.write(result)
        except OSError as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if out is not None:
            out.finish()

    report = stats.report()
    print(
        f"records: {report['total']} | ok: {report['ok']} | "
        f"errors: {report['errors']}"
    )
    for err in report["error_lines"]:
        print(f"  line {err['line']}: {'; '.join(err['errors'])}", file=sys.stderr)
    if report["errors"]:
        return EXIT_VALIDATION
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())