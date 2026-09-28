"""P0-4 rules: non-numeric violation, int accumulation, spec validation."""

from __future__ import annotations

import unittest

from core.rules import RuleEngine


class TestSumNonNumeric(unittest.TestCase):
    def test_sum_string_field_reports_non_numeric(self):
        engine = RuleEngine(({"type": "sum", "field": "amount", "expected": 0},))
        engine.observe({"amount": "abc"})
        violations = engine.finalize()
        self.assertTrue(
            any("non-numeric" in v.message for v in violations),
            f"expected non-numeric violation, got: {violations}",
        )


class TestBalanceInt(unittest.TestCase):
    def test_balance_int_sums_without_violation(self):
        engine = RuleEngine(
            ({"type": "balance", "positive": "debit", "negative": "credit"},)
        )
        engine.observe({"debit": 100, "credit": 60})
        engine.observe({"debit": 20, "credit": 60})
        violations = engine.finalize()
        self.assertEqual(violations, [])


class TestInvalidSpec(unittest.TestCase):
    def test_sum_without_expected_raises(self):
        with self.assertRaises(ValueError):
            RuleEngine(({"type": "sum", "field": "amount"},))

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            RuleEngine(({"type": "unknown", "field": "amount"},))


if __name__ == "__main__":
    unittest.main()
