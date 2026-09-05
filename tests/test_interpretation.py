"""Tests for real_information_analysis.interpretation — probability_reliability."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_information_analysis import ReliabilityVerdict, probability_reliability


class BehaviourMatrixTests(unittest.TestCase):
    """The behaviour matrix from PRODUCTIZATION_PLAN.md §R1 is the contract."""

    def test_beyond_24h_is_calibrated(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=86_401)
        self.assertEqual(verdict.label, "calibrated")
        self.assertEqual(verdict.discount_factor, 1.0)
        self.assertEqual(verdict.flags, ())

    def test_exactly_24h_is_late_life(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=86_400)
        self.assertEqual(verdict.label, "late-life")
        self.assertEqual(verdict.discount_factor, 0.8)

    def test_between_10min_and_24h_is_late_life(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=601)
        self.assertEqual(verdict.label, "late-life")
        self.assertEqual(verdict.discount_factor, 0.8)

    def test_final_10_minutes_is_expiry_regime(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=600)
        self.assertEqual(verdict.label, "expiry-regime")
        self.assertEqual(verdict.discount_factor, 0.5)

    def test_expired_contract_is_expiry_regime(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=-5)
        self.assertEqual(verdict.label, "expiry-regime")
        self.assertEqual(verdict.discount_factor, 0.5)


class FlagTests(unittest.TestCase):
    def test_parlay_flag_applies_regardless_of_time(self) -> None:
        for seconds in (10 * 86_400, 3_600, 30):
            with self.subTest(seconds=seconds):
                verdict = probability_reliability(
                    seconds_to_expiry=seconds, product_type="parlay"
                )
                self.assertIn("systematic-markup", verdict.flags)

    def test_product_type_is_case_insensitive(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=10_000_000, product_type="Multi-Leg")
        self.assertIn("systematic-markup", verdict.flags)

    def test_binary_product_has_no_markup_flag(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=10_000_000, product_type="BINARY")
        self.assertNotIn("systematic-markup", verdict.flags)

    def test_thin_market_flag_below_100k(self) -> None:
        verdict = probability_reliability(
            seconds_to_expiry=10_000_000, volume_usd=99_999.0
        )
        self.assertIn("thin-market", verdict.flags)

    def test_no_thin_market_flag_at_or_above_100k(self) -> None:
        verdict = probability_reliability(
            seconds_to_expiry=10_000_000, volume_usd=100_000.0
        )
        self.assertNotIn("thin-market", verdict.flags)

    def test_unknown_volume_adds_no_flag(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=10_000_000, volume_usd=None)
        self.assertEqual(verdict.flags, ())

    def test_flags_combine(self) -> None:
        verdict = probability_reliability(
            seconds_to_expiry=120, product_type="combo", volume_usd=500.0
        )
        self.assertEqual(verdict.label, "expiry-regime")
        self.assertEqual(verdict.flags, ("systematic-markup", "thin-market"))


class ContractTests(unittest.TestCase):
    def test_verdict_is_frozen(self) -> None:
        verdict = probability_reliability(seconds_to_expiry=10_000)
        with self.assertRaises(AttributeError):
            verdict.label = "x"  # type: ignore[misc]

    def test_returns_reliability_verdict_type(self) -> None:
        self.assertIsInstance(probability_reliability(seconds_to_expiry=1), ReliabilityVerdict)

    def test_verdict_is_deterministic(self) -> None:
        # T2-style reproducibility assertion: same input, identical output.
        kwargs = {"seconds_to_expiry": 4_321, "product_type": "parlay", "volume_usd": 12_345.0}
        self.assertEqual(
            probability_reliability(**kwargs), probability_reliability(**kwargs)
        )


if __name__ == "__main__":
    unittest.main()
