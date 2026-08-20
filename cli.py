"""erp-export-normalizer - CLI entry point.

Commands:
  (default)         convert a flat file using a schema (--schema) or
                    auto-detection against formats/
  generate-schema   infer a delimited schema from an example file
  registry          validate a schema library directory

Pipeline: input -> [parser] -> [converters] -> [validator] -> [writer]
Streaming, line by line; cumulative validation with a line-numbered report.

Exit codes: 0 success / 1 runtime error / 2 invalid schema / 3 validation errors.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from contextlib import nullcontext

import yaml

from core import (
    audit,
    detector,
    generator,
    parallel,
    parser,
    rules,
    schema as schema_mod,
    validator,
    writer,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SCHEMA = 2
EXIT_VALIDATION = 3

TEXT_FORMATS = {"json", "csv", "ndjson", "sql"}
OUTPUT_EXT = {"excel": "xlsx"}


def _ext_for(fmt: str) -> str:
    return OUTPUT_EXT.get(fmt, fmt)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="erp-normalize",
        description=(
            "Convert legacy flat files (fixed-width or delimited) to "
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
    ap.add_argument(
        "--input", help="input flat file or glob pattern (e.g. 'exports/*.txt')"
    )
    ap.add_argument(
        "--output", help="output path ('-' = stdout, text formats only)"
    )
    ap.add_argument("--output-dir", help="directory for batch (glob) conversions")
    ap.add_argument(
        "--format",
        choices=["json", "csv", "ndjson", "sql", "parquet", "excel"],
        default="json",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel validation processes (deterministic output)",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="print per-line diagnostics to stderr (OK/ERR per record)",
    )
    ap.add_argument(
        "--checksum",
        action="store_true",
        help="write an audit summary sidecar (<output>.sha256)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="validate only, produce no output; print error report",
    )

    sub = ap.add_subparsers(dest="command")
    gen = sub.add_parser(
        "generate-schema",
        help="infer a delimited schema from an example file",
    )
    gen.add_argument("input", help="example flat file")
    gen.add_argument("--output", required=True, help="YAML schema to write")
    gen.add_argument("--name", help="optional SQL table name for the schema")
    gen.add_argument("--delimiter", help="force a delimiter (else auto-detect)")
    gen.add_argument("--codepage", default="utf-8")
    gen.add_argument("--no-header", action="store_true", help="first line is data")

    reg = sub.add_parser("registry", help="validate a schema library directory")
    reg.add_argument("dir", help="directory of *.yaml schemas")
    return ap


def _resolve_schema(args) -> tuple[schema_mod.Schema | None, int | None]:
    if args.schema:
        try:
            return schema_mod.load_schema(args.schema), None
        except (OSError, schema_mod.SchemaError) as exc:
            print(f"schema error: {exc}", file=sys.stderr)
            return None, EXIT_SCHEMA
    try:
        found = detector.Detector(args.formats_dir).detect(args.input)
    except OSError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return None, EXIT_ERROR
    if found is None:
        print(
            "no schema matched the input; pass --schema or extend formats/",
            file=sys.stderr,
        )
        return None, EXIT_ERROR
    print(f"detected schema: {found.source_path} (score {found.score})")
    return found.schema, None


def _convert_file(ap: argparse.ArgumentParser, args) -> int:
    sch, code = _resolve_schema(args)
    if code is not None:
        return code

    rule_engine = rules.RuleEngine(sch.rules) if sch.rules else None
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

    reader = parser.FixedWidthReader(sch, args.input)
    records = reader.records(skip_first=bool(sch.has_header))

    def serial_results():
        val = validator.Validator(sch)
        for lineno, record in records:
            yield val.validate_record(lineno, record)

    results = (
        parallel.validate_parallel(records, sch, args.workers)
        if args.workers and args.workers > 1
        else serial_results()
    )

    with out_fh:
        try:
            for result in results:
                stats.add(result)
                if args.verbose:
                    status = "OK" if result.ok else "ERR"
                    details = (
                        f" [{'; '.join(result.errors)}]" if result.errors else ""
                    )
                    print(f"  line {result.line}: {status}{details}", file=sys.stderr)
                if rule_engine is not None and result.ok:
                    rule_engine.observe({fv.name: fv.value for fv in result.fields})
                if result.ok and out is not None:
                    out.write(result)
        except OSError as exc:
            print(f"input error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if out is not None:
            out.finish()

    violations = rule_engine.finalize() if rule_engine is not None else []
    for violation in violations:
        print(f"  rule violation: {violation.message}", file=sys.stderr)

    if args.checksum and not args.dry_run and args.output != "-":
        try:
            sidecar = args.output + ".sha256"
            summary = audit.build_summary(
                args.input, args.output, sch, stats, violations
            )
            with open(sidecar, "w", encoding="utf-8") as fh:
                fh.write("\n".join(summary) + "\n")
            print(f"audit summary: {sidecar}")
        except OSError as exc:
            print(f"audit error: {exc}", file=sys.stderr)

    report = stats.report()
    summary_line = (
        f"records: {report['total']} | ok: {report['ok']} | "
        f"errors: {report['errors']}"
    )
    if sch.rules:
        summary_line += f" | rule violations: {len(violations)}"
    print(summary_line)
    for err in report["error_lines"]:
        print(f"  line {err['line']}: {'; '.join(err['errors'])}", file=sys.stderr)
    if violations or report["errors"]:
        return EXIT_VALIDATION
    return EXIT_OK


def convert_main(ap: argparse.ArgumentParser, args) -> int:
    if not args.input or not args.output:
        ap.error("--input and --output are required for conversion")

    has_glob = any(ch in args.input for ch in "*?[")
    patterns = glob.glob(args.input) if has_glob else [args.input]
    if not patterns:
        print(f"input error: no files match {args.input!r}", file=sys.stderr)
        return EXIT_ERROR

    if len(patterns) > 1:
        if not args.output_dir:
            print("multiple input files require --output-dir", file=sys.stderr)
            return EXIT_ERROR
        codes = []
        for match in sorted(patterns):
            sub = argparse.Namespace(**vars(args))
            sub.input = match
            sub.output = os.path.join(
                args.output_dir,
                os.path.splitext(os.path.basename(match))[0] + "." + _ext_for(args.format),
            )
            print(f"== {match} -> {sub.output}", file=sys.stderr)
            codes.append(_convert_file(ap, sub))
        for code in codes:
            if code == EXIT_VALIDATION:
                return EXIT_VALIDATION
        for code in codes:
            if code == EXIT_ERROR:
                return EXIT_ERROR
        for code in codes:
            if code == EXIT_SCHEMA:
                return EXIT_SCHEMA
        return EXIT_OK

    args.input = patterns[0]
    return _convert_file(ap, args)


def generate_main(args) -> int:
    try:
        data = generator.generate_schema(
            args.input,
            name=args.name,
            delimiter=args.delimiter,
            codepage=args.codepage,
            has_header=False if args.no_header else None,
        )
    except (OSError, ValueError) as exc:
        print(f"generate-schema error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        schema_mod.build_schema(data)  # validate before writing
    except schema_mod.SchemaError as exc:
        print(f"generate-schema error: generated schema invalid: {exc}", file=sys.stderr)
        return EXIT_ERROR
    with open(args.output, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    print(f"schema written: {args.output}")
    return EXIT_OK


def registry_main(args) -> int:
    paths = sorted(glob.glob(os.path.join(args.dir, "*.yaml")))
    if not paths:
        print(f"no schemas found in {args.dir}", file=sys.stderr)
        return EXIT_ERROR
    print(f"{'format':<22}{'version':<10}{'len':<6}{'table':<18}{'fields':<7}source")
    invalid = 0
    seen: dict[tuple[str, str], str] = {}
    for path in paths:
        try:
            sch = schema_mod.load_schema(path)
        except (OSError, schema_mod.SchemaError) as exc:
            print(f"INVALID {path}: {exc}", file=sys.stderr)
            invalid += 1
            continue
        key = (sch.format, sch.version)
        if key in seen:
            print(f"duplicate {key[0]}@{key[1]} (also {seen[key]})", file=sys.stderr)
        seen[key] = path
        length = sch.record_length if sch.record_length is not None else "n/a"
        print(
            f"{sch.format:<22}{sch.version:<10}{length:<6}"
            f"{(sch.table or ''):<18}{len(sch.fields):<7}{path}"
        )
    return EXIT_SCHEMA if invalid else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command == "generate-schema":
        return generate_main(args)
    if args.command == "registry":
        return registry_main(args)
    return convert_main(ap, args)


if __name__ == "__main__":
    sys.exit(main())