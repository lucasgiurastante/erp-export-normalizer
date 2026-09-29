"""Postgres destination: SQL, batching, determinism and failure reporting.

No live database and no `psycopg`: the tests drive a fake connection that
records statements and parameters, which is enough to prove the batch
boundaries, the row accounting and the SQL that would be sent.
"""

from __future__ import annotations

import contextlib
import decimal
import io
import os
import tempfile
import unittest

from cli import main
from core import connectors
from core.connectors import ConnectorError, PostgresWriter
from core.schema import build_schema
from core.validator import FieldValue, RecordResult

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(REPO_ROOT, "core", "formats", "jde_ar.yaml")
FIXTURE = os.path.join(REPO_ROOT, "examples", "data", "jde_ar.txt")

SCHEMA_OBJ = build_schema(
    {
        "format": "t",
        "version": "1.0.0",
        "table": "t_export",
        "record_length": 24,
        "fields": [
            {"name": "s", "start": 0, "length": 6},
            {
                "name": "d",
                "start": 6,
                "length": 8,
                "type": "date",
                "format": "YYYYMMDD",
            },
            {"name": "n", "start": 14, "length": 10, "type": "decimal", "scale": 2},
        ],
    }
)


def result(line: int = 1) -> RecordResult:
    return RecordResult(
        line=line,
        ok=True,
        errors=[],
        fields=[
            FieldValue("s", f"r{line:03d}", ""),
            FieldValue("d", "2024-08-15", ""),
            FieldValue("n", decimal.Decimal("1234.56"), ""),
        ],
    )


def failed_result(line: int = 1) -> RecordResult:
    r = result(line)
    r.ok = False
    r.errors = ["field 'n': not a number"]
    return r


class FakeCursor:
    def __init__(self, conn: FakeConnection):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.statements.append(("cursor", sql, params))

    def close(self):
        pass


class FakeConnection:
    """Records what a real driver would have been asked to do."""

    def __init__(self, fail_on: int | None = None):
        self.statements: list[tuple] = []
        self.commits = 0
        self.fail_on = fail_on
        self.inserts = 0

    def execute(self, sql, params=None):
        if sql.lstrip().upper().startswith("INSERT"):
            self.inserts += 1
            if self.fail_on is not None and self.inserts == self.fail_on:
                raise RuntimeError("connection reset")
        self.statements.append(("execute", sql, params))

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


class TestSql(unittest.TestCase):
    def test_decimal_maps_to_numeric_never_float(self):
        """Money must not round-trip through a float."""
        sql = connectors.create_table_sql(SCHEMA_OBJ, "t")
        self.assertIn('"n" numeric(2)', sql)
        self.assertNotIn("float", sql)
        self.assertNotIn("real", sql)
        self.assertNotIn("double", sql)

    def test_date_maps_to_date(self):
        self.assertIn('"d" date', connectors.create_table_sql(SCHEMA_OBJ, "t"))

    def test_packed_maps_to_numeric(self):
        sch = build_schema(
            {
                "format": "t",
                "version": "1.0.0",
                "record_length": 5,
                "fields": [
                    {"name": "p", "start": 0, "length": 5, "type": "packed", "scale": 2}
                ],
            }
        )
        self.assertIn('"p" numeric(2)', connectors.create_table_sql(sch, "t"))

    def test_insert_uses_one_placeholder_per_field(self):
        sql = connectors.insert_sql(SCHEMA_OBJ, "t")
        self.assertEqual(sql.count("%s"), 3)
        self.assertIn('INSERT INTO "t"', sql)

    def test_identifier_injection_is_refused(self):
        for bad in ('t"; DROP TABLE x; --', "1t", "", "a-b"):
            with self.assertRaises(ConnectorError, msg=bad):
                connectors._sql_ident(bad)


class TestBatched(unittest.TestCase):
    def test_exact_division(self):
        self.assertEqual([len(b) for b in connectors.batched(range(6), 2)], [2, 2, 2])

    def test_remainder_is_kept(self):
        self.assertEqual([len(b) for b in connectors.batched(range(5), 2)], [2, 2, 1])

    def test_does_not_hold_everything(self):
        """The batch generator must not materialise the stream."""
        stream = (i for i in range(1000))
        first = next(connectors.batched(stream, 10))
        self.assertEqual(len(first), 10)

    def test_rejects_non_positive_size(self):
        with self.assertRaises(ConnectorError):
            list(connectors.batched(range(3), 0))


class TestPostgresWriter(unittest.TestCase):
    def test_creates_table_before_inserting(self):
        conn = FakeConnection()
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=10)
        w.write(result())
        w.finish()
        first_sql = conn.statements[0][1]
        self.assertIn("CREATE TABLE", first_sql)
        self.assertTrue(
            any("INSERT" in s[1] for s in conn.statements),
            "records were never inserted",
        )

    def test_batches_at_the_configured_size(self):
        conn = FakeConnection()
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=2)
        for i in range(5):
            w.write(result(i + 1))
        w.finish()
        inserts = [s for s in conn.statements if "INSERT" in s[1]]
        self.assertEqual([len(s[2]) for s in inserts], [2, 2, 1])

    def test_buffer_never_exceeds_the_batch(self):
        """Bounded memory: the buffer flushes as it fills."""
        conn = FakeConnection()
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=3)
        for i in range(10):
            w.write(result(i + 1))
            self.assertLessEqual(len(w._buffer), 3)
        w.finish()

    def test_insert_order_is_input_order(self):
        conn = FakeConnection()
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=100)
        for i in range(5):
            w.write(result(i + 1))
        w.finish()
        rows = [r for s in conn.statements if "INSERT" in s[1] for r in s[2]]
        self.assertEqual([r[0] for r in rows], ["r001", "r002", "r003", "r004", "r005"])

    def test_same_input_gives_the_same_statements(self):
        """Determinism: two runs, identical parameter sequence."""
        runs = []
        for _ in range(2):
            conn = FakeConnection()
            w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=2)
            for i in range(6):
                w.write(result(i + 1))
            w.finish()
            runs.append([s[2] for s in conn.statements if "INSERT" in s[1]])
        self.assertEqual(runs[0], runs[1])

    def test_decimal_stays_exact(self):
        conn = FakeConnection()
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=1)
        w.write(result())
        w.finish()
        row = [s for s in conn.statements if "INSERT" in s[1]][0][2][0]
        self.assertIsInstance(row[2], decimal.Decimal)
        self.assertEqual(str(row[2]), "1234.56")

    def test_report_counts(self):
        conn = FakeConnection()
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=2)
        for i in range(5):
            w.write(result(i + 1))
        w.finish()
        self.assertEqual(w.report.rows_written, 5)
        self.assertEqual(w.report.batches, 3)
        self.assertEqual(conn.commits, 1)

    def test_truncate_runs_first(self):
        conn = FakeConnection()
        PostgresWriter(conn, SCHEMA_OBJ, truncate=True).finish()
        self.assertIn("TRUNCATE", conn.statements[0][1])

    def test_no_truncate_by_default(self):
        conn = FakeConnection()
        PostgresWriter(conn, SCHEMA_OBJ).finish()
        self.assertFalse(any("TRUNCATE" in s[1] for s in conn.statements))

    def test_table_override(self):
        conn = FakeConnection()
        PostgresWriter(conn, SCHEMA_OBJ, table="other").finish()
        self.assertIn('"other"', conn.statements[0][1])

    def test_missing_table_is_an_error(self):
        sch = build_schema(
            {
                "format": "t",
                "version": "1.0.0",
                "record_length": 4,
                "fields": [{"name": "a", "start": 0, "length": 4}],
            }
        )
        with self.assertRaises(ConnectorError) as ctx:
            PostgresWriter(FakeConnection(), sch)
        self.assertIn("no table", str(ctx.exception))

    def test_failure_reports_how_many_landed(self):
        """A partial load must say its size, not vanish quietly."""
        conn = FakeConnection(fail_on=2)
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=2)
        with self.assertRaises(ConnectorError) as ctx:
            for i in range(6):
                w.write(result(i + 1))
            w.finish()
        message = str(ctx.exception)
        # 2 batches of 2 landed; the third failed
        self.assertIn("2 rows committed", message)
        self.assertIn("2 in the failed batch", message)

    def test_committed_count_is_not_the_written_count(self):
        """rows_committed must not count rows that never arrived.

        With batch_size=2 the third batch fails during `write`, so only four
        rows were ever enqueued and two of them committed.
        """
        conn = FakeConnection(fail_on=2)
        w = PostgresWriter(conn, SCHEMA_OBJ, batch_size=2)
        with self.assertRaises(ConnectorError):
            for i in range(6):
                w.write(result(i + 1))
            w.finish()
        self.assertEqual(w.report.rows_written, 4)
        self.assertEqual(w.report.rows_committed, 2)

    def test_driver_errors_become_connector_errors(self):
        class Broken:
            def execute(self, sql, params=None):
                raise RuntimeError("down")

            def cursor(self):
                return FakeCursor(self)

        with self.assertRaises(ConnectorError) as ctx:
            PostgresWriter(Broken(), SCHEMA_OBJ, truncate=True)
        self.assertIn("cannot prepare target", str(ctx.exception))

    def test_driver_absent_is_a_clear_message(self):
        import inspect

        src = inspect.getsource(connectors.connect_postgres)
        self.assertIn("erp-export-normalizer[postgres]", src)


class TestCliPostgres(unittest.TestCase):
    def _run(self, argv: list[str]) -> int:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return main(argv)

    def test_missing_driver_returns_error(self):
        code = self._run(
            [
                "--schema",
                SCHEMA,
                "--input",
                FIXTURE,
                "--output",
                "-",
                "--postgres-dsn",
                "postgresql://x@y/z",
            ]
        )
        self.assertEqual(code, 1)

    def test_sidecar_is_json_for_a_db_target(self):
        """A database load gets a load report, not a file checksum sidecar."""

        fake = FakeConnection()
        loaded = {}

        def fake_connect(dsn, **kwargs):
            loaded["dsn"] = dsn
            return fake

        original = connectors.connect_postgres
        connectors.connect_postgres = fake_connect
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out.json")
                err = io.StringIO()
                with (
                    contextlib.redirect_stderr(err),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = main(
                        [
                            "--schema",
                            SCHEMA,
                            "--input",
                            FIXTURE,
                            "--output",
                            out,
                            "--format",
                            "json",
                            "--postgres-dsn",
                            "postgresql://x@y/z",
                            "--batch-size",
                            "2",
                        ]
                    )
                self.assertEqual(code, 0)
                self.assertEqual(loaded["dsn"], "postgresql://x@y/z")
                inserts = [s for s in fake.statements if "INSERT" in s[1]]
                self.assertEqual([len(s[2]) for s in inserts], [2, 1])
                self.assertIn("rows_written", err.getvalue())
                # the file is not written when a database is the target
                self.assertFalse(os.path.exists(out))
        finally:
            connectors.connect_postgres = original


if __name__ == "__main__":
    unittest.main()
