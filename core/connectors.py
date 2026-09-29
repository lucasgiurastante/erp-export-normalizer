"""Destination connectors: load records straight into a target.

Files on disk are a good intermediate, but between "converted" and "loaded"
there is usually another script someone wrote by hand. This module makes the
target a first-class destination.

Two properties are non-negotiable and are what separates this from a toy:

- **Determinism.** Records are inserted in input order in fixed-size batches,
  never in parallel, so the same file always produces the same table state.
  Re-running is the caller's decision (`truncate` makes it explicit).
- **Audit trail.** The run records what it wrote, and a failure part-way
  through says exactly how many rows made it, instead of leaving a half-loaded
  table nobody can reason about.

Drivers are optional extras. Importing this module must never require
`psycopg`, and tests exercise the SQL/batch logic against a fake connection
rather than a live database.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import json
from collections.abc import Iterable, Sequence

from .schema import Schema
from .validator import RecordResult

BATCH_SIZE = 1000


class ConnectorError(RuntimeError):
    """The target could not be written to."""


class DependencyMissing(ConnectorError):
    """An optional driver is not installed."""


@dataclasses.dataclass
class LoadReport:
    """What a load actually did. Written even when it fails."""

    target: str
    table: str
    rows_written: int = 0
    rows_committed: int = 0
    batches: int = 0
    truncated: bool = False
    started: str = ""

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "table": self.table,
            "rows_written": self.rows_written,
            "rows_committed": self.rows_committed,
            "batches": self.batches,
            "truncated": self.truncated,
            "started": self.started,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _pg_type(f: str) -> str:
    """Map a schema field type to a Postgres type.

    Money stays `numeric`, never `float`: this tool exists to move financial
    amounts, and a float round-trip would quietly move them.
    """
    return {
        "decimal": "numeric",
        "packed": "numeric",
        "date": "date",
        "string": "text",
    }.get(f, "text")


def _sql_ident(name: str) -> str:
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise ConnectorError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def create_table_sql(schema: Schema, table: str) -> str:
    cols = ",\n  ".join(
        f"{_sql_ident(f.name)} {_pg_type(f.type)}"
        + ("" if f.type == "date" or f.scale == 0 else f"({f.scale})")
        for f in schema.fields
    )
    return f"CREATE TABLE IF NOT EXISTS {_sql_ident(table)} (\n  {cols}\n)"


def insert_sql(schema: Schema, table: str) -> str:
    names = ", ".join(_sql_ident(f.name) for f in schema.fields)
    marks = ", ".join(["%s"] * len(schema.fields))
    return f"INSERT INTO {_sql_ident(table)} ({names}) VALUES ({marks})"


def _adapt(value: object) -> object:
    """Make a validated value bindable by a driver.

    Dates are already ISO strings from the validator, which every driver
    accepts; decimals are passed through so the target stores them exactly.
    """
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def batched(
    rows: Iterable[Sequence[object]], size: int = BATCH_SIZE
) -> Iterable[list[Sequence[object]]]:
    """Group rows into fixed batches without holding the whole stream."""
    if size <= 0:
        raise ConnectorError("batch size must be > 0")
    batch: list[Sequence[object]] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def load(
    connection,
    schema: Schema,
    results: Iterable[RecordResult],
    table: str | None = None,
    batch_size: int = BATCH_SIZE,
    truncate: bool = False,
) -> LoadReport:
    """Stream validated records into `connection` in order.

    `connection` only has to provide `execute(sql, params)` and, for
    `truncate`, be a context manager. That is deliberately minimal: it keeps
    the real driver out of the test suite while still exercising the batch
    boundaries, the SQL and the row accounting.
    """
    target_table = table or schema.table
    if not target_table:
        raise ConnectorError("no table: set schema 'table' or pass --table")
    _sql_ident(target_table)  # validate before touching the connection

    report = LoadReport(
        target="postgres",
        table=target_table,
        truncated=truncate,
        started=datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
    )
    statement = insert_sql(schema, target_table)
    try:
        if truncate:
            connection.execute(f"TRUNCATE TABLE {_sql_ident(target_table)}")
        cursor = connection.cursor()
        cursor.execute(create_table_sql(schema, target_table))
        cursor.close()
    except AttributeError as exc:
        raise ConnectorError(
            "connection must provide .execute(sql, params) and .cursor()"
        ) from exc
    except Exception as exc:  # driver errors are not ours to type
        raise ConnectorError(f"cannot prepare target: {exc}") from exc

    def rows():
        for result in results:
            if not result.ok:
                continue
            report.rows_written += 1
            yield [_adapt(fv.value) for fv in result.fields]

    try:
        for batch in batched(rows(), batch_size):
            params = list(batch)
            connection.execute(statement, params)
            report.batches += 1
        connection.commit()
    except Exception as exc:
        # Report what actually landed: a partial load that hides its size is
        # worse than one that fails loudly with a number attached.
        raise ConnectorError(
            f"{report.rows_written} of {report.rows_written} rows in flight, "
            f"batch {report.batches + 1} failed: {exc}"
        ) from exc
    return report


class PostgresWriter:
    """Streaming `Writer` that loads records into Postgres as they arrive.

    Implementing the same `write`/`finish` protocol as the file writers keeps
    the pipeline unchanged and, more importantly, keeps memory bounded: a
    batch is flushed every `batch_size` records and never grows. Collecting
    the results to insert them at the end would undo the streaming promise.

    Rows go in input order, sequentially. Parallel insertion would make the
    same file produce a different table state between runs, and this tool
    promises determinism everywhere, not just in the file output.
    """

    def __init__(
        self,
        connection,
        schema: Schema,
        table: str | None = None,
        batch_size: int = BATCH_SIZE,
        truncate: bool = False,
    ):
        target = table or schema.table
        if not target:
            raise ConnectorError("no table: set schema 'table' or pass --table")
        _sql_ident(target)
        self._connection = connection
        self._schema = schema
        self._table = target
        self._batch_size = batch_size
        self._buffer: list[list[object]] = []
        self.report = LoadReport(target="postgres", table=target, truncated=truncate)
        self._statement = insert_sql(schema, target)
        self._prepare()

    def _prepare(self) -> None:
        try:
            if self.report.truncated:
                self._connection.execute(f"TRUNCATE TABLE {_sql_ident(self._table)}")
            cursor = self._connection.cursor()
            cursor.execute(create_table_sql(self._schema, self._table))
            cursor.close()
        except AttributeError as exc:
            raise ConnectorError(
                "connection must provide .execute(sql, params) and .cursor()"
            ) from exc
        except Exception as exc:
            raise ConnectorError(f"cannot prepare target: {exc}") from exc

    def _flush(self) -> None:
        if not self._buffer:
            return
        params = self._buffer
        self._buffer = []
        try:
            self._connection.execute(self._statement, params)
        except Exception as exc:
            # Say exactly how many rows are actually in the target. Reporting
            # the number of rows we *enqueued* would be a lie: the failed
            # batch never landed, and rows_committed is the truth.
            in_flight = (
                self.report.rows_written - self.report.rows_committed - len(params)
            )
            raise ConnectorError(
                f"{self.report.rows_committed} rows committed, "
                f"{len(params)} in the failed batch, "
                f"{in_flight} never sent: {exc}"
            ) from exc
        self.report.batches += 1
        self.report.rows_committed += len(params)

    def write(self, result: RecordResult) -> None:
        self._buffer.append([_adapt(fv.value) for fv in result.fields])
        self.report.rows_written += 1
        if len(self._buffer) >= self._batch_size:
            self._flush()

    def finish(self) -> None:
        self._flush()
        try:
            self._connection.commit()
        except Exception as exc:
            raise ConnectorError(
                f"commit failed after {self.report.rows_written} rows: {exc}"
            ) from exc


def connect_postgres(dsn: str, **kwargs):
    """Open a psycopg connection. The driver is an optional extra.

    A refused or unreachable server is a normal operational event, not a
    bug: it comes back as a `ConnectorError` with a message, not a driver
    traceback on the user's terminal.
    """
    try:
        import psycopg  # noqa: PLC0415 - optional dependency
    except ImportError as exc:
        raise DependencyMissing(
            "postgres output needs psycopg: "
            "pip install 'erp-export-normalizer[postgres]'"
        ) from exc
    try:
        return psycopg.connect(dsn, **kwargs)
    except Exception as exc:
        raise ConnectorError(
            f"cannot connect to Postgres: {exc}. Check the DSN, the network "
            "and that the server is reachable."
        ) from exc
