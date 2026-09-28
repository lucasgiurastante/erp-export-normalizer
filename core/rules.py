"""Semantic business rules over validated rows (streaming accumulators).

Rules run in O(1) memory: totals accumulate per record and are checked at
the end. Deterministic by construction.

Supported rule types:
- sum: {type: sum, field: <name>, expected: <number>}
      total of `field` across all valid records must equal `expected`.
- balance: {type: balance, positive: <name>, negative: <name>}
      sum of `positive` must equal sum of `negative` (debits = credits).
"""

from __future__ import annotations

import dataclasses
import decimal

from .schema import Schema


@dataclasses.dataclass(frozen=True)
class RuleViolation:
    rule: dict
    message: str


class RuleEngine:
    def __init__(self, rules: tuple[dict, ...] | None):
        for i, spec in enumerate(rules or ()):
            rtype = spec.get("type")
            if rtype == "sum":
                if "field" not in spec or "expected" not in spec:
                    raise ValueError(f"rules[{i}]: 'sum' requires 'field'+'expected'")
            elif rtype == "balance":
                if "positive" not in spec or "negative" not in spec:
                    raise ValueError(f"rules[{i}]: 'balance' requires 'positive'+'negative'")
            else:
                raise ValueError(f"rules[{i}]: unsupported type {rtype!r}")
        self._rules = rules or ()
        self._totals: list[dict[str, decimal.Decimal]] = [{} for _ in self._rules]
        self._field_errors: list[RuleViolation] = []

    def observe(self, row: dict[str, object]) -> None:
        for spec, acc in zip(self._rules, self._totals, strict=True):
            rtype = spec["type"]
            if rtype == "sum":
                self._accumulate(acc, spec["field"], row, spec)
            elif rtype == "balance":
                self._accumulate(acc, spec["positive"], row, spec)
                self._accumulate(acc, spec["negative"], row, spec)

    def _accumulate(self, acc: dict, field: str, row: dict, spec: dict) -> None:
        value = row.get(field)
        if isinstance(value, decimal.Decimal):
            acc[field] = acc.get(field, decimal.Decimal(0)) + value
        elif isinstance(value, int):
            acc[field] = acc.get(field, decimal.Decimal(0)) + decimal.Decimal(value)
        elif isinstance(value, float):
            acc[field] = acc.get(field, decimal.Decimal(0)) + decimal.Decimal(str(value))
        else:
            self._field_errors.append(
                RuleViolation(spec, f"rule '{spec['type']}:{field}': non-numeric {value!r}")
            )

    def finalize(self) -> list[RuleViolation]:
        violations: list[RuleViolation] = list(self._field_errors)
        for spec, acc in zip(self._rules, self._totals, strict=True):
            rtype = spec["type"]
            if rtype == "sum":
                field = spec["field"]
                total = acc.get(field, decimal.Decimal(0))
                expected = decimal.Decimal(str(spec["expected"]))
                if total != expected:
                    violations.append(
                        RuleViolation(
                            spec,
                            f"rule 'sum:{field}': total {total} != expected {expected}",
                        )
                    )
            elif rtype == "balance":
                positive = spec["positive"]
                negative = spec["negative"]
                sp = acc.get(positive, decimal.Decimal(0))
                sn = acc.get(negative, decimal.Decimal(0))
                if sp != sn:
                    violations.append(
                        RuleViolation(
                            spec,
                            f"rule 'balance:{positive}/{negative}': "
                            f"{positive}={sp} != {negative}={sn} (diff {sp - sn})",
                        )
                    )
        return violations


def run_rules(schema: Schema, rows: list[dict]) -> list[RuleViolation]:
    """Convenience: apply all schema rules to an already-collected row list."""
    engine = RuleEngine(schema.rules)
    for row in rows:
        engine.observe(row)
    return engine.finalize()
