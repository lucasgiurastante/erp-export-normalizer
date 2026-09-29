"""CI performance profile: regression gate, determinism, report shape."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import perf_profile  # noqa: E402


def case(workers: int, chunk: int, seconds: float, md5: str = "d") -> dict:
    return {
        "workers": workers,
        "chunk_lines": chunk,
        "seconds": seconds,
        "md5": md5,
        "rows": 1000,
        "bytes": 1000,
    }


class TestCaseKey(unittest.TestCase):
    def test_key_is_stable(self):
        self.assertEqual(perf_profile._case_key(case(2, 10_000, 1.0)), "w2c10000")


class TestCompare(unittest.TestCase):
    def test_missing_baseline_is_new_not_failure(self):
        verdicts = perf_profile.compare([case(1, 10, 1.0)], [], 0.5)
        self.assertEqual(verdicts[0]["status"], "new")

    def test_same_speed_is_ok(self):
        verdicts = perf_profile.compare([case(1, 10, 1.0)], [case(1, 10, 1.0)], 0.5)
        self.assertEqual(verdicts[0]["status"], "ok")
        self.assertEqual(verdicts[0]["ratio"], 1.0)

    def test_faster_is_ok(self):
        verdicts = perf_profile.compare([case(1, 10, 0.5)], [case(1, 10, 1.0)], 0.5)
        self.assertEqual(verdicts[0]["status"], "ok")

    def test_slower_beyond_tolerance_is_regression(self):
        verdicts = perf_profile.compare([case(1, 10, 2.0)], [case(1, 10, 1.0)], 0.5)
        self.assertEqual(verdicts[0]["status"], "regression")
        self.assertEqual(verdicts[0]["ratio"], 2.0)
        self.assertEqual(verdicts[0]["baseline"], 1.0)

    def test_within_tolerance_is_ok(self):
        # 1.4x is under the 1.5x limit
        verdicts = perf_profile.compare([case(1, 10, 1.4)], [case(1, 10, 1.0)], 0.5)
        self.assertEqual(verdicts[0]["status"], "ok")

    def test_zero_baseline_time_is_treated_as_new(self):
        verdicts = perf_profile.compare([case(1, 10, 1.0)], [case(1, 10, 0.0)], 0.5)
        self.assertEqual(verdicts[0]["status"], "new")

    def test_every_case_is_verdicted(self):
        current = [case(1, 10, 1.0), case(2, 10, 1.0), case(4, 10, 1.0)]
        verdicts = perf_profile.compare(current, [case(1, 10, 1.0)], 0.5)
        self.assertEqual(len(verdicts), 3)


class TestMachineSignature(unittest.TestCase):
    def test_signature_uses_python_and_platform(self):
        a = perf_profile.machine_signature({"python": "3.12.0", "platform": "linux"})
        b = perf_profile.machine_signature({"python": "3.12.0", "platform": "linux"})
        c = perf_profile.machine_signature({"python": "3.12.0", "platform": "darwin"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_missing_fields_do_not_crash(self):
        self.assertTrue(perf_profile.machine_signature({}))


class TestEndToEnd(unittest.TestCase):
    """Small real runs: the script must be honest about determinism."""

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = perf_profile.main_perf(argv)
        return code, out.getvalue(), err.getvalue()

    def test_reports_identical_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "perf.json")
            code, out, _ = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "2",
                    "--chunks",
                    "1000",
                    "--output",
                    report,
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("md5 identical", out)
            with open(report, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertTrue(data["identical_outputs"])
            self.assertEqual(len(data["md5"]), 1)
            self.assertEqual(len(data["cases"]), 2)
            self.assertIn("w2", data["speedup_vs_serial"])

    def test_cli_summary_does_not_pollute_stdout(self):
        """The converter's per-run summary must not land in the report stream."""
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "perf.json")
            _, out, _ = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--output",
                    report,
                ]
            )
            self.assertNotIn("records:", out)
            with open(report, encoding="utf-8") as fh:
                json.load(fh)  # must be valid JSON

    def test_baseline_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "base.json")
            code, _, _ = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--update-baseline",
                    base,
                ]
            )
            self.assertEqual(code, 0)
            with open(base, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertIn("cases", data)
            # comparing against a fast baseline of the same run must pass
            # with a generous tolerance
            code, out, _ = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--baseline",
                    base,
                    "--tolerance",
                    "5.0",
                ]
            )
            self.assertEqual(code, 0, out)

    def test_regression_fails_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "base.json")
            self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--update-baseline",
                    base,
                ]
            )
            with open(base, encoding="utf-8") as fh:
                data = json.load(fh)
            for c in data["cases"]:
                c["seconds"] = 0.001  # claim it used to be 1000x faster
            with open(base, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            code, _, err = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--baseline",
                    base,
                    "--tolerance",
                    "0.5",
                ]
            )
            self.assertEqual(code, 1)
            self.assertIn("REGRESSION", err)

    def test_negative_tolerance_rejected(self):
        code, _, err = self._run(["--tolerance", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("tolerance", err)

    def test_baseline_from_another_machine_skips_the_gate(self):
        """A laptop baseline must not fail a CI build over hardware alone."""
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "base.json")
            self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--update-baseline",
                    base,
                ]
            )
            with open(base, encoding="utf-8") as fh:
                data = json.load(fh)
            data["platform"] = "some-other-machine"
            for c in data["cases"]:
                c["seconds"] = 0.001  # absurdly fast, as on different hardware
            with open(base, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            code, _, err = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--baseline",
                    base,
                    "--tolerance",
                    "0.5",
                ]
            )
            self.assertEqual(code, 0, err)
            self.assertIn("different machine", err)
            self.assertIn("SKIPPED", err)

    def test_require_same_machine_turns_the_skip_into_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "base.json")
            self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--update-baseline",
                    base,
                ]
            )
            with open(base, encoding="utf-8") as fh:
                data = json.load(fh)
            data["platform"] = "some-other-machine"
            for c in data["cases"]:
                c["seconds"] = 0.001
            with open(base, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            code, _, _ = self._run(
                [
                    "--lines",
                    "2000",
                    "--workers",
                    "1",
                    "--chunks",
                    "1000",
                    "--baseline",
                    base,
                    "--require-same-machine",
                ]
            )
            # the gate now applies and flags the slowdown
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
