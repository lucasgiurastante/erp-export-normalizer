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
import json
import os
import sys
from contextlib import ExitStack, nullcontext

import yaml

from core import (
    audit,
    copybook,
    crosscheck,
    detector,
    generator,
    parallel,
    parser,
    plugins,
    rules,
    validator,
    writer,
)
from core import (
    diff as diff_mod,
)
from core import (
    schema as schema_mod,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SCHEMA = 2
EXIT_VALIDATION = 3

TEXT_FORMATS = {"json", "csv", "ndjson", "sql", "singer"}
OUTPUT_EXT = {"excel": "xlsx"}
DEFAULT_CODEPAGE_HINT = "ebcdic-cp037"


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
        "--plugins-dir",
        default=plugins.DEFAULT_PLUGINS_DIR,
        help="directory of custom parser plugins (for schemas with 'parser')",
    )
    ap.add_argument(
        "--input", help="input flat file or glob pattern (e.g. 'exports/*.txt')"
    )
    ap.add_argument("--output", help="output path ('-' = stdout, text formats only)")
    ap.add_argument("--output-dir", help="directory for batch (glob) conversions")
    ap.add_argument(
        "--format",
        choices=["json", "csv", "ndjson", "sql", "parquet", "excel", "singer"],
        default="json",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel validation processes (deterministic output)",
    )
    ap.add_argument(
        "--chunk-lines",
        type=int,
        default=parallel.CHUNK_LINES,
        help="lines per parallel chunk (only with --workers > 1)",
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

    df = sub.add_parser(
        "diff",
        help="row-level diff between two conversions, keyed",
    )
    df.add_argument(
        "old",
        metavar="OLD_SCHEMA=OLD_INPUT",
        help="'schema.yaml=input.txt' of the baseline export",
    )
    df.add_argument(
        "new",
        metavar="NEW_SCHEMA=NEW_INPUT",
        help="'schema.yaml=input.txt' of the new export",
    )
    df.add_argument(
        "--key",
        action="append",
        default=[],
        dest="key_fields",
        metavar="FIELD",
        help="key column (repeatable); the composite identifies a row",
    )
    df.add_argument(
        "--fields",
        default="",
        help="comma-separated fields to compare (default: all fields)",
    )
    df.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        help="max sample entries printed per change kind (default 20)",
    )
    df.add_argument(
        "--max-keys",
        type=int,
        help="fail instead of indexing more distinct keys than this",
    )
    df.add_argument("--json", action="store_true", help="emit the diff as JSON")

    xc = sub.add_parser(
        "crosscheck",
        help="reconcile two or more conversions (sums, counts, key sets)",
    )
    xc.add_argument(
        "inputs",
        nargs="+",
        metavar="SCHEMA=INPUT",
        help="one or more 'schema.yaml=input.txt' pairs (2+ required)",
    )
    xc.add_argument(
        "--key",
        action="append",
        default=[],
        dest="key_fields",
        metavar="FIELD",
        help="key column (repeatable); the composite identifies a row",
    )
    xc.add_argument(
        "--sum",
        action="append",
        default=[],
        dest="sum_fields",
        metavar="FIELD",
        help="numeric column to total per file (repeatable)",
    )
    xc.add_argument(
        "--checks",
        default="count",
        help=(
            "comma-separated checks: count, unique, sum:<field>, "
            "missing[:extra] (default: count)"
        ),
    )
    xc.add_argument(
        "--max-keys",
        type=int,
        help="fail instead of indexing more distinct keys than this",
    )
    xc.add_argument("--json", action="store_true", help="emit the report as JSON")

    cpy = sub.add_parser(
        "copybook",
        help="import a COBOL copybook (FD/PIC clauses) as a YAML schema",
    )
    cpy.add_argument("input", help="copybook source file (.cpy/.cbl/.txt)")
    cpy.add_argument("--output", required=True, help="YAML schema to write")
    cpy.add_argument(
        "--record", help="01 record name when the copybook declares several"
    )
    cpy.add_argument("--name", help="optional SQL table name for the schema")
    cpy.add_argument(
        "--codepage",
        default=copybook.DEFAULT_CODEPAGE,
        help=f"record codepage (default {DEFAULT_CODEPAGE_HINT})",
    )
    cpy.add_argument(
        "--no-date",
        action="store_true",
        help="do not infer PIC 9(8) as a date",
    )

    srv = sub.add_parser("serve", help="start the zero-dependency web UI (air-gapped)")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
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


def make_reader(sch, input_path: str, plugins_dir: str):
    """Build the record reader: custom plugin reader or the fixed-width one."""
    if sch.parser:
        return plugins.load_reader(sch.parser, sch, input_path, plugins_dir)
    return parser.FixedWidthReader(sch, input_path)


def _convert_file(ap: argparse.ArgumentParser, args) -> int:
    sch, code = _resolve_schema(args)
    if code is not None:
        return code
    assert sch is not None  # exit code above guarantees resolution

    rule_engine = rules.RuleEngine(sch.rules) if sch.rules else None
    stats = validator.Stats()

    stack = ExitStack()
    out_fh: object = nullcontext(sys.stdout)
    if args.dry_run:
        out = None
        out_fh = nullcontext(sys.stdout)
    elif args.format in TEXT_FORMATS:
        if args.output == "-":
            out_fh = nullcontext(sys.stdout)
            try:
                out = writer.make_writer(args.format, sch, sys.stdout)
            except ValueError as exc:
                stack.close()
                print(f"output error: {exc}", file=sys.stderr)
                return EXIT_ERROR
        else:
            try:
                # noqa: SIM115 - file kept open for streaming; closed by `with out_fh`
                out_fh = open(args.output, "w", encoding="utf-8", newline="")  # noqa: SIM115
            except OSError as exc:
                stack.close()
                print(f"output error: {exc}", file=sys.stderr)
                return EXIT_ERROR
            try:
                out = writer.make_writer(args.format, sch, out_fh)
            except ValueError as exc:
                stack.close()
                print(f"output error: {exc}", file=sys.stderr)
                return EXIT_ERROR
    else:
        out_fh = nullcontext(sys.stdout)
        try:
            out = writer.make_writer(args.format, sch, args.output)
        except (OSError, ValueError) as exc:
            stack.close()
            print(f"output error: {exc}", file=sys.stderr)
            return EXIT_ERROR

    try:
        reader = make_reader(sch, args.input, args.plugins_dir)
    except plugins.PluginError as exc:
        print(f"plugin error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        records = reader.records(skip_first=bool(sch.has_header))
    except (OSError, ValueError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    def serial_results():
        val = validator.Validator(sch)
        for lineno, record in records:
            yield val.validate_record(lineno, record)

    results = (
        parallel.validate_parallel(
            records, sch, args.workers, chunk_lines=args.chunk_lines
        )
        if args.workers and args.workers > 1
        else serial_results()
    )

    with out_fh:
        try:
            for result in results:
                stats.add(result)
                if args.verbose:
                    status = "OK" if result.ok else "ERR"
                    details = f" [{'; '.join(result.errors)}]" if result.errors else ""
                    print(f"  line {result.line}: {status}{details}", file=sys.stderr)
                if rule_engine is not None and result.ok:
                    rule_engine.observe({fv.name: fv.value for fv in result.fields})
                if result.ok and out is not None:
                    out.write(result)
        except (OSError, ValueError) as exc:
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
        f"records: {report['total']} | ok: {report['ok']} | errors: {report['errors']}"
    )
    if sch.rules:
        summary_line += f" | rule violations: {len(violations)}"
    print(summary_line, file=sys.stderr)
    for err in report["error_lines"]:
        # P1-3: err["errors"] ya trae "field 'X': <msg> | raw='...'"
        # (raw truncado a 50 chars en validator); prefijo "line N:" intacto
        # para scripts que lo parsean. "details" es estructurado opcional.
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
                os.path.splitext(os.path.basename(match))[0]
                + "."
                + _ext_for(args.format),
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
        print(
            f"generate-schema error: generated schema invalid: {exc}", file=sys.stderr
        )
        return EXIT_ERROR
    with open(args.output, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    print(f"schema written: {args.output}")
    return EXIT_OK


def _split_pair(pair: str, what: str) -> tuple[str, str]:
    if "=" not in pair:
        raise ValueError(f"{what}: expected SCHEMA=INPUT, got {pair!r}")
    schema_path, input_path = pair.split("=", 1)
    return schema_path, input_path


def diff_main(args) -> int:
    try:
        old_schema, old_input = _split_pair(args.old, "old")
        new_schema, new_input = _split_pair(args.new, "new")
    except ValueError as exc:
        print(f"diff error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    compare = [f.strip() for f in args.fields.split(",") if f.strip()]

    try:
        # Load both schemas up front so an invalid one fails before any work.
        schema_mod.load_schema(old_schema)
        schema_mod.load_schema(new_schema)
    except (OSError, schema_mod.SchemaError) as exc:
        print(f"diff error: {exc}", file=sys.stderr)
        return EXIT_SCHEMA

    old_label = os.path.basename(old_input)
    new_label = os.path.basename(new_input)
    try:
        result = diff_mod.diff_rows(
            old_label,
            _iter_converted(old_schema, old_input, args.plugins_dir),
            new_label,
            _iter_converted(new_schema, new_input, args.plugins_dir),
            key_fields=args.key_fields,
            compare_fields=compare or None,
            sample_limit=args.sample_limit,
            max_keys=args.max_keys,
        )
    except (OSError, ValueError) as exc:
        print(f"diff error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        payload = result.to_dict()
        payload["old"] = old_label
        payload["new"] = new_label
        print(json.dumps(payload, indent=2))
    else:
        for line in diff_mod.format_report(result, old_label, new_label):
            print(line)
    return EXIT_OK if result.identical_result else EXIT_VALIDATION


def _parse_checks(raw: str) -> tuple[dict, ...]:
    out: list[dict] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token in ("count", "unique"):
            out.append({"type": token, "name": token})
        elif token.startswith("sum:"):
            field = token.split(":", 1)[1].strip()
            if not field:
                raise ValueError("sum check requires a field, e.g. sum:amount")
            out.append({"type": "sum", "field": field, "name": f"sum:{field}"})
        elif token.startswith("missing"):
            out.append(
                {"type": "missing", "name": "missing", "extra": token.endswith("extra")}
            )
        else:
            raise ValueError(
                f"unknown check {token!r} (use count, unique, sum:<field>, missing)"
            )
    if not out:
        raise ValueError("no checks given")
    return tuple(out)


def _iter_converted(schema_path: str, input_path: str, plugins_dir: str):
    """Yield validated rows for one file, discarding rows that failed."""
    sch = schema_mod.load_schema(schema_path)
    reader = make_reader(sch, input_path, plugins_dir)
    val = validator.Validator(sch)
    for lineno, record in reader.records(skip_first=bool(sch.has_header)):
        result = val.validate_record(lineno, record)
        if result.ok:
            yield {fv.name: fv.value for fv in result.fields}


def crosscheck_main(args) -> int:
    if len(args.inputs) < 2:
        print("crosscheck error: need at least two SCHEMA=INPUT pairs", file=sys.stderr)
        return EXIT_ERROR
    try:
        spec = _parse_checks(args.checks)
    except ValueError as exc:
        print(f"crosscheck error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    sources: list[tuple[str, crosscheck.FileTotals]] = []
    for pair in args.inputs:
        if "=" not in pair:
            print(
                f"crosscheck error: expected SCHEMA=INPUT, got {pair!r}",
                file=sys.stderr,
            )
            return EXIT_ERROR
        schema_path, input_path = pair.split("=", 1)
        try:
            sch = schema_mod.load_schema(schema_path)
        except (OSError, schema_mod.SchemaError) as exc:
            print(f"crosscheck error: {exc}", file=sys.stderr)
            return EXIT_SCHEMA
        label = os.path.basename(input_path)
        try:
            rows = _iter_converted(schema_path, input_path, args.plugins_dir)
            totals = crosscheck.summarize(
                label,
                rows,
                sch,
                args.key_fields,
                args.sum_fields,
                max_keys=args.max_keys,
            )
        except (OSError, ValueError) as exc:
            print(f"crosscheck error: {exc}", file=sys.stderr)
            return EXIT_ERROR
        sources.append((label, totals))

    try:
        results = crosscheck.run_checks([t for _, t in sources], spec)
    except crosscheck.CrossCheckError as exc:
        print(f"crosscheck error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = {
        "files": [t.as_dict() for _, t in sources],
        "checks": [r.to_dict() for r in results],
        "ok": all(r.ok for r in results),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for label, totals in sources:
            print(
                f"  {label}: rows={totals.rows} keys={len(totals.keys)}"
                + (
                    f" sums={ {k: str(v) for k, v in sorted(totals.sums.items())} }"
                    if totals.sums
                    else ""
                )
            )
        for r in results:
            print(f"  [{'OK' if r.ok else 'FAIL'}] {r.message}")
            for detail in r.details:
                print(f"         {detail}", file=sys.stderr)
    return EXIT_OK if report["ok"] else EXIT_VALIDATION


def copybook_main(args) -> int:
    try:
        data = copybook.parse_copybook(
            args.input,
            record=args.record,
            table=args.name,
            codepage=args.codepage,
            want_date=not args.no_date,
        )
    except (OSError, ValueError) as exc:
        print(f"copybook error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        schema_mod.build_schema(data)  # validate before writing
    except schema_mod.SchemaError as exc:
        print(f"copybook error: generated schema invalid: {exc}", file=sys.stderr)
        return EXIT_ERROR
    try:
        with open(args.output, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
    except OSError as exc:
        print(f"copybook error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(
        f"schema written: {args.output} "
        f"({len(data['fields'])} fields, record_length {data['record_length']})"
    )
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


def serve_main(args) -> int:
    from core.webui import serve

    try:
        serve(host=args.host, port=args.port, formats_dir=args.formats_dir)
    except OSError as exc:
        print(f"serve error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.command == "generate-schema":
        return generate_main(args)
    if args.command == "copybook":
        return copybook_main(args)
    if args.command == "crosscheck":
        return crosscheck_main(args)
    if args.command == "diff":
        return diff_main(args)
    if args.command == "registry":
        return registry_main(args)
    if args.command == "serve":
        return serve_main(args)
    return convert_main(ap, args)


if __name__ == "__main__":
    sys.exit(main())
