"""Tests for ledger_coherence_lint — semantic write-time defence for the ledger.

Regression shapes come from the two live incidents found in the 2026-09
audit of predictions/2026-09.jsonl:

* the September FOMC pair: "cut?" p=0.97 with market_ref "no cuts"
  (inverted polarity), plus "hike?" p=0.45 vs "any hike this year" p=0.38
  (monotonicity violation across nested events — recorded via group-sum
  once grouped);
* market_implied numbers with no market_ref at all.
"""

from __future__ import annotations

import unittest

from real_information_analysis.scoring import (
    Prediction,
    ledger_coherence_lint,
)


def _prediction(**overrides: object) -> Prediction:
    base: dict[str, object] = {
        "question": "Will the Fed cut in September 2026?",
        "probability": 0.10,
    }
    base.update(overrides)
    return Prediction(**base)  # type: ignore[arg-type]


class GroupSumTests(unittest.TestCase):
    def test_exclusive_group_summing_above_one_is_error(self) -> None:
        # The live FOMC pair: 0.97 + 0.45 = 1.42 over exclusive outcomes.
        result = ledger_coherence_lint([
            _prediction(question="September cut?", probability=0.97,
                        mutually_exclusive_group="fomc-26sep"),
            _prediction(question="September hike?", probability=0.45,
                        mutually_exclusive_group="fomc-26sep"),
        ])
        self.assertFalse(result.ok)
        errors = [i for i in result.issues if i.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "group-sum")
        self.assertIn("1.42", errors[0].message)

    def test_group_within_tolerance_is_clean(self) -> None:
        # Real books sum slightly above 1 (spread + vig).
        result = ledger_coherence_lint([
            _prediction(question="cut?", probability=0.95,
                        mutually_exclusive_group="fomc-26sep"),
            _prediction(question="hold?", probability=0.035,
                        mutually_exclusive_group="fomc-26sep"),
            _prediction(question="hike?", probability=0.025,
                        mutually_exclusive_group="fomc-26sep"),
        ])
        errors = [i for i in result.issues if i.severity == "error"]
        self.assertEqual(errors, [])

    def test_ungrouped_entries_are_not_summed(self) -> None:
        # Without a group id there is no claim of exclusivity — nested
        # events ("September hike" vs "any 2026 hike") legitimately coexist.
        result = ledger_coherence_lint([
            _prediction(question="September hike?", probability=0.45),
            _prediction(question="Any hike in 2026?", probability=0.38),
        ])
        errors = [i for i in result.issues if i.severity == "error"]
        self.assertEqual(errors, [])


class PolarityTests(unittest.TestCase):
    def test_negated_market_ref_on_plain_question_warns(self) -> None:
        # The live FOMC entry: question "cut?" but the price came from the
        # "no cuts" sub-market.
        result = ledger_coherence_lint([
            _prediction(question="Will the Fed cut in September 2026?",
                        probability=0.97, market_implied=0.9985,
                        market_ref="Will no Fed rate cuts happen in 2026?"),
        ])
        warnings = [i for i in result.issues if i.severity == "warning"]
        self.assertTrue(any(i.code == "negation-polarity" for i in warnings))
        # A warning alone does not fail the lint.
        self.assertTrue(result.ok)

    def test_negated_question_with_negated_ref_is_clean(self) -> None:
        # "Will there be no cuts?" legitimately quotes the "no cuts" leg.
        result = ledger_coherence_lint([
            _prediction(question="Will there be no Fed rate cuts in 2026?",
                        probability=0.93, market_implied=0.9275,
                        market_ref="Will no Fed rate cuts happen in 2026?"),
        ])
        warnings = [i for i in result.issues if i.code == "negation-polarity"]
        self.assertEqual(warnings, [])

    def test_chinese_negation_token_detected(self) -> None:
        result = ledger_coherence_lint([
            _prediction(question="2026 年 9 月 FOMC 会降息吗?",
                        probability=0.97, market_implied=0.9985,
                        market_ref="不会降息(维持 3.50%-3.75%)"),
        ])
        warnings = [i for i in result.issues if i.code == "negation-polarity"]
        self.assertEqual(len(warnings), 1)


class TraceabilityTests(unittest.TestCase):
    def test_market_implied_without_ref_warns(self) -> None:
        result = ledger_coherence_lint([
            _prediction(question="hike?", probability=0.38, market_implied=0.375),
        ])
        warnings = [i for i in result.issues if i.severity == "warning"]
        self.assertTrue(any(i.code == "market-implied-untraceable" for i in warnings))

    def test_market_implied_with_ref_is_clean(self) -> None:
        result = ledger_coherence_lint([
            _prediction(question="hike?", probability=0.78,
                        market_implied=0.785, market_ref="KXFED-26DEC-T3.75"),
        ])
        self.assertEqual(result.issues, ())

    def test_no_market_implied_needs_no_ref(self) -> None:
        result = ledger_coherence_lint([
            _prediction(question="recession?", probability=0.06),
        ])
        self.assertEqual(result.issues, ())


class EmptyLedgerTests(unittest.TestCase):
    def test_empty_ledger_is_ok(self) -> None:
        # Composition check fails on empty (no loop at all); coherence has
        # nothing to contradict — its contract is per-entry/per-group.
        result = ledger_coherence_lint([])
        self.assertTrue(result.ok)
        self.assertEqual(result.total, 0)


if __name__ == "__main__":
    unittest.main()
