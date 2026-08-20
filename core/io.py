"""DataFrame integration: read ERP exports as Pandas or Polars DataFrames.

`read_erp()` runs the same validated pipeline as the CLI and returns an
in-memory DataFrame for interactive analysis.
"""
from __future__ import annotations

from . import detector as detector_mod
from .parser import FixedWidthReader
from .validator import Validator


class ErpValidationError(ValueError):
    """Input contained records that failed schema validation."""


def read_erp(
    path: str,
    schema=None,
    formats_dir: str = detector_mod.DEFAULT_FORMATS_DIR,
    backend: str = "pandas",
    on_error: str = "raise",
):
    """Convert a flat file to a DataFrame.

    Arguments:
        path: input flat file.
        schema: Schema object or YAML path; None = auto-detect.
        formats_dir: schema library for auto-detection.
        backend: "pandas" or "polars".
        on_error: "raise" (default) or "ignore" (drop invalid records).
    """
    if isinstance(schema, str):
        from .schema import load_schema

        schema = load_schema(schema)
    elif schema is None:
        found = detector_mod.Detector(formats_dir).detect(path)
        if found is None:
            raise ErpValidationError("no schema matched the input; pass schema explicitly")
        schema = found.schema

    val = Validator(schema)
    rows: list[dict] = []
    first_error: tuple[int, list[str]] | None = None
    for lineno, record in FixedWidthReader(schema, path).records():
        result = val.validate_record(lineno, record)
        if result.ok:
            rows.append({fv.name: fv.value for fv in result.fields})
        elif first_error is None:
            first_error = (lineno, result.errors)

    if first_error is not None and on_error == "raise":
        line, errors = first_error
        raise ErpValidationError(
            f"line {line}: {'; '.join(errors)} "
            f"(use on_error='ignore' to drop invalid records)"
        )

    if backend == "pandas":
        import pandas as pd

        return pd.DataFrame(rows)
    if backend == "polars":
        import polars as pl

        return pl.DataFrame(rows)
    raise ValueError(f"unsupported backend: {backend!r}")