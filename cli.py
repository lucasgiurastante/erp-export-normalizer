"""erp-export-normalizer: CLI entry point.

Pipeline: input ─► [parser fixed-width] ─► [converters] ─► [validator] ─► [writer]
Streaming línea a línea; validación acumulativa con reporte.

Exit codes: 0 ok / 1 error runtime / 2 schema inválido / 3 errores de validación.
"""
from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext

from core import parser, schema as schema_mod, validator, writer

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SCHEMA = 2
EXIT_VALIDATION = 3


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="erp-normalize",
        description="Convierte flat-files legacy (fixed-width) a JSON/CSV usando schema YAML.",
    )
    ap.add_argument("--schema", required=True, help="ruta al schema YAML")
    ap.add_argument("--input", required=True, help="archivo plano de entrada")
    ap.add_argument("--output", required=True, help="ruta de salida ('-' = stdout)")
    ap.add_argument("--format", choices=["json", "csv"], default="json")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="valida sin generar salida; solo reporte de errores",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        sch = schema_mod.load_schema(args.schema)
    except (OSError, schema_mod.SchemaError) as exc:
        print(f"error de schema: {exc}", file=sys.stderr)
        return EXIT_SCHEMA

    val = validator.Validator(sch)
    stats = validator.Stats()

    if args.dry_run:
        out = None
        out_fh = nullcontext(sys.stdout)
    else:
        try:
            out_fh = open(args.output, "w", encoding="utf-8", newline="")
        except OSError as exc:
            print(f"error de salida: {exc}", file=sys.stderr)
            return EXIT_ERROR
        out = writer.make_writer(args.format, sch, out_fh)

    with out_fh:
        reader = parser.FixedWidthReader(sch, args.input)
        try:
            for lineno, record in reader.records():
                result = val.validate_record(lineno, record)
                stats.add(result)
                if result.ok and out is not None:
                    out.write(result)
        except OSError as exc:
            print(f"error de entrada: {exc}", file=sys.stderr)
            return EXIT_ERROR
        if out is not None:
            out.finish()

    report = stats.report()
    print(
        f"registros: {report['total']} | ok: {report['ok']} | "
        f"errores: {report['errors']}"
    )
    for err in report["error_lines"]:
        print(f"  línea {err['line']}: {'; '.join(err['errors'])}", file=sys.stderr)
    if report["errors"]:
        return EXIT_VALIDATION
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())