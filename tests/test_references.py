"""Referential integrity: every detail key exists in the master."""

from __future__ import annotations

import contextlib
import decimal
import io
import os
import tempfile
import unittest

import yaml

from cli import main
from core.crosscheck import CrossCheckError, run_checks, summarize
from core.schema import build_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MASTER_SCHEMA = build_schema(
    {
        "format": "t",
        "version": "1.0.0",
        "record_length": 20,
        "fields": [
            {"name": "customer_id", "start": 0, "length": 12},
            {"name": "customer_name", "start": 12, "length": 8},
        ],
    }
)
DETAIL_SCHEMA = build_schema(
    {
        "format": "t",
        "version": "1.0.0",
        "record_length": 20,
        "fields": [
            {"name": "invoice_id", "start": 0, "length": 10},
            {"name": "customer_id", "start": 10, "length": 8},
            {"name": "amount", "start": 18, "length": 2, "type": "decimal"},
        ],
    }
)


def master_rows(*ids: str) -> list[dict]:
    return [{"customer_id": i, "customer_name": f"N{i[-1]}"} for i in ids]


def detail_rows(*pairs: tuple[str, str]) -> list[dict]:
    return [
        {
            "invoice_id": f"INV{n:04d}",
            "customer_id": c,
            "amount": decimal.Decimal("10.00"),
        }
        for n, c in enumerate(pairs, start=1)
    ]


def totals(master, detail, **kwargs):
    return [
        summarize(
            "master.txt",
            master,
            MASTER_SCHEMA,
            ["customer_id"],
            [],
            foreign_field="customer_id",
            **kwargs,
        ),
        summarize(
            "detail.txt",
            detail,
            DETAIL_SCHEMA,
            ["customer_id"],
            [],
            foreign_field="customer_id",
            **kwargs,
        ),
    ]


def refs(field: str = "customer_id") -> tuple[dict, ...]:
    return ({"type": "references", "field": field, "name": f"references:{field}"},)


class TestReferentialIntegrity(unittest.TestCase):
    def test_all_present_passes(self):
        (result,) = run_checks(
            totals(master_rows("C1", "C2"), detail_rows("C1", "C2")),
            refs(),
        )
        self.assertTrue(result.ok)
        self.assertIn("exists in", result.message)

    def test_orphan_fails(self):
        (result,) = run_checks(
            totals(master_rows("C1"), detail_rows("C1", "C99")),
            refs(),
        )
        self.assertFalse(result.ok)
        self.assertIn("C99", " ".join(result.details))
        self.assertIn("1 distinct", " ".join(result.details))

    def test_empty_key_counts_as_an_orphan(self):
        """A detail row pointing at nothing is a defect, not a row to skip."""
        (result,) = run_checks(
            totals(master_rows("C1"), detail_rows("C1", "")),
            refs(),
        )
        self.assertFalse(result.ok)
        self.assertIn("(empty)", " ".join(result.details))

    def test_whitespace_only_key_is_an_orphan(self):
        (result,) = run_checks(totals(master_rows("C1"), detail_rows("   ")), refs())
        self.assertFalse(result.ok)

    def test_repeated_orphans_report_the_exact_count(self):
        rows = detail_rows(*[f"C9{n}" for n in range(5)])
        (result,) = run_checks(totals(master_rows("C1"), rows), refs())
        self.assertIn("5 distinct", " ".join(result.details))

    def test_samples_are_capped_but_the_count_is_exact(self):
        rows = detail_rows(*[f"C9{n}" for n in range(30)])
        (result,) = run_checks(totals(master_rows("C1"), rows), refs(), sample_limit=3)
        orphans = [d for d in result.details if "orphan" in d]
        self.assertEqual(len(orphans), 3)
        self.assertIn("30 distinct", " ".join(result.details))

    def test_many_to_one_is_the_default(self):
        """The same customer appearing in many invoices is normal."""
        (result,) = run_checks(
            totals(master_rows("C1"), detail_rows("C1", "C1")), refs()
        )
        self.assertTrue(result.ok)

    def test_duplicate_master_key_is_flagged(self):
        (result,) = run_checks(
            totals(master_rows("C1", "C1"), detail_rows("C1")), refs()
        )
        self.assertIn("repeats customer_id", " ".join(result.details))


class TestSpecificationErrors(unittest.TestCase):
    def test_requires_a_field(self):
        with self.assertRaises(CrossCheckError) as ctx:
            run_checks(
                totals(master_rows("C1"), detail_rows("C1")),
                ({"type": "references"},),
            )
        self.assertIn("requires 'field'", str(ctx.exception))

    def test_requires_two_files(self):
        with self.assertRaises(CrossCheckError) as ctx:
            run_checks(
                [
                    summarize(
                        "m",
                        master_rows("C1"),
                        MASTER_SCHEMA,
                        ["customer_id"],
                        [],
                        foreign_field="customer_id",
                    )
                ],
                refs(),
            )
        self.assertIn("master file", str(ctx.exception))

    def test_unknown_field_is_refused(self):
        with self.assertRaises(CrossCheckError) as ctx:
            run_checks(totals(master_rows("C1"), detail_rows("C1")), refs("nope"))
        self.assertIn("not in any of the schemas", str(ctx.exception))

    def test_foreign_key_must_be_indexed(self):
        plain = [
            summarize(
                "master.txt", master_rows("C1"), MASTER_SCHEMA, ["customer_id"], []
            ),
            summarize(
                "detail.txt",
                detail_rows("C1"),
                DETAIL_SCHEMA,
                ["customer_id"],
                [],
            ),
        ]
        with self.assertRaises(CrossCheckError) as ctx:
            run_checks(plain, refs())
        self.assertIn("needs the foreign key", str(ctx.exception))


class TestMemoryBound(unittest.TestCase):
    def test_max_keys_still_applies(self):
        with self.assertRaises(CrossCheckError) as ctx:
            totals(master_rows("C1", "C2", "C3"), detail_rows("C1"), max_keys=2)
        self.assertIn("distinct key limit", str(ctx.exception))

    def test_foreign_index_is_opt_in(self):
        """Without `keep_records` a run stays bounded; asking must be explicit."""
        bounded = summarize("m", master_rows("C1"), MASTER_SCHEMA, ["customer_id"], [])
        self.assertIsNone(bounded.foreign)
        held = summarize(
            "m",
            master_rows("C1"),
            MASTER_SCHEMA,
            ["customer_id"],
            [],
            foreign_field="customer_id",
        )
        self.assertEqual(held.foreign, {"C1"})


class TestCrosscheckCli(unittest.TestCase):
    def _files(self, tmp: str):
        master = os.path.join(tmp, "master.csv")
        detail = os.path.join(tmp, "detail.csv")
        with open(master, "w", encoding="utf-8") as fh:
            fh.write("CUST0001,ACME\nCUST0002,GLOBEX\n")
        with open(detail, "w", encoding="utf-8") as fh:
            fh.write("INV00001,CUST0001,100.00\nINV00002,CUST9999,50.00\n")
        m_schema = os.path.join(tmp, "m.yaml")
        d_schema = os.path.join(tmp, "d.yaml")
        with open(m_schema, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {
                    "format": "delimited",
                    "version": "1.0.0",
                    "delimiter": ",",
                    "fields": [
                        {"name": "customer_id", "type": "string"},
                        {"name": "customer_name", "type": "string"},
                    ],
                },
                fh,
            )
        with open(d_schema, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {
                    "format": "delimited",
                    "version": "1.0.0",
                    "delimiter": ",",
                    "fields": [
                        {"name": "invoice_id", "type": "string"},
                        {"name": "customer_id", "type": "string"},
                        {"name": "amount", "type": "decimal", "scale": 2},
                    ],
                },
                fh,
            )
        return m_schema, master, d_schema, detail

    def test_orphan_exits_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, mf, d, df = self._files(tmp)
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "crosscheck",
                        f"{m}={mf}",
                        f"{d}={df}",
                        "--key",
                        "customer_id",
                        "--checks",
                        "references:customer_id",
                    ]
                )
            self.assertEqual(code, 3)
            self.assertIn("CUST9999", err.getvalue())

    def test_matching_files_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, mf, d, df = self._files(tmp)
            with open(df, "w", encoding="utf-8") as fh:
                fh.write("INV00001,CUST0001,100.00\n")
            with (
                contextlib.redirect_stderr(io.StringIO()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "crosscheck",
                        f"{m}={mf}",
                        f"{d}={df}",
                        "--key",
                        "customer_id",
                        "--checks",
                        "references:customer_id",
                    ]
                )
            self.assertEqual(code, 0)

    def test_json_report(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            m, mf, d, df = self._files(tmp)
            out = io.StringIO()
            with (
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                main(
                    [
                        "crosscheck",
                        f"{m}={mf}",
                        f"{d}={df}",
                        "--key",
                        "customer_id",
                        "--checks",
                        "references:customer_id",
                        "--json",
                    ]
                )
            report = json.loads(out.getvalue())
            self.assertFalse(report["ok"])
            check = report["checks"][0]
            self.assertEqual(check["kind"], "references")
            self.assertTrue(any("CUST9999" in d for d in check["details"]))

    def test_spec_without_field_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, mf, d, df = self._files(tmp)
            err = io.StringIO()
            with (
                contextlib.redirect_stderr(err),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "crosscheck",
                        f"{m}={mf}",
                        f"{d}={df}",
                        "--key",
                        "customer_id",
                        "--checks",
                        "references",
                    ]
                )
            self.assertEqual(code, 1)
            self.assertIn("requires a field", err.getvalue())


if __name__ == "__main__":
    unittest.main()
