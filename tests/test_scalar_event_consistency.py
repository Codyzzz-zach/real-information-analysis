"""Tests for scalar_event_consistency — multi-outcome event coherence.

Grounded in the two live incidents the function exists to catch:

* the "92.75% -> 5.8% impossible flip" — both prices were real, but from
  different sub-markets of the same 13-market "How many Fed rate cuts"
  event (sum-to-1 check on exclusive outcomes);
* the ledger entry that read a December >=4.00% strike (0.365) and recorded
  it as "any hike in 2026" (~0.785 at the T3.75 leg) — the ladder
  monotonicity check flags a leg that belongs to a different question.
"""

from __future__ import annotations

import unittest

from real_information_analysis.interpretation import scalar_event_consistency


class ScalarEventConsistencyTests(unittest.TestCase):
    def test_live_fed_cuts_ladder_is_consistent(self) -> None:
        # 13 exclusive YES legs of the real "How many Fed rate cuts in
        # 2026?" event (v1.3.4 live observation): sum 1.0115, within
        # tolerance. Exclusive semantics (default) => sum check only.
        ladder = {
            "no cuts": 0.9275, "1 cut": 0.0535, "2 cuts": 0.0190,
            "3 cuts": 0.0060, "4 cuts": 0.0025, "5 cuts": 0.000375,
            "6 cuts": 0.000375, "7 cuts": 0.000375, "8 cuts": 0.000375,
            "9 cuts": 0.000375, "10 cuts": 0.000375, "11 cuts": 0.000375,
            "12+ cuts": 0.000375,
        }
        result = scalar_event_consistency(ladder)
        self.assertTrue(result.ok, result.detail)
        self.assertAlmostEqual(result.probability_sum or 0.0, 1.0115, places=4)
        self.assertEqual(result.priced_legs, 13)

    def test_sum_exceeding_tolerance_is_flagged(self) -> None:
        # Mutually exclusive outcomes summing to ~1.4 cannot all be priced
        # by the same coherent book — at least one leg is mislabeled.
        result = scalar_event_consistency({"cut": 0.97, "hike": 0.45})
        self.assertFalse(result.ok)
        self.assertIn("sum-exceeds-1", result.flags)

    def test_sum_below_tolerance_is_flagged(self) -> None:
        result = scalar_event_consistency({"a": 0.10, "b": 0.20})
        self.assertFalse(result.ok)
        self.assertIn("sum-below-1", result.flags)

    def test_live_kalshi_kxfed_sep_ladder_is_consistent(self) -> None:
        # Real KXFED-26SEP "above X%" ladder, observed live 2026-09-08:
        # non-increasing in the strike. Nested semantics (descending=True)
        # => monotonicity check only; the plain sum (~4.5) is meaningless
        # because the legs overlap.
        result = scalar_event_consistency(
            {"2.75%": 0.995, "3.00%": 0.995, "3.25%": 0.995, "3.50%": 0.995,
             "3.75%": 0.535, "4.00%": 0.015, "4.25%": 0.005},
            descending=True,
        )
        self.assertTrue(result.ok, result.detail)
        self.assertNotIn("sum-exceeds-1", result.flags)

    def test_monotonicity_break_flags_misread_leg(self) -> None:
        # Real KXFED-26DEC legs (observed live 2026-09-08): fine ladder.
        result = scalar_event_consistency(
            {"3.50%": 0.925, "3.75%": 0.785, "4.00%": 0.365, "4.25%": 0.06},
            descending=True,
        )
        self.assertTrue(result.ok, result.detail)

        # One leg jumps back up mid-descent — the classic signature of a
        # price taken from a different sub-market than its label.
        broken = scalar_event_consistency(
            {"3.50%": 0.925, "3.75%": 0.785, "4.00%": 0.865, "4.25%": 0.06},
            descending=True,
        )
        self.assertFalse(broken.ok)
        self.assertIn("monotonicity-break", broken.flags)

    def test_monotonicity_tolerance_covers_quoting_noise(self) -> None:
        # Thin legs quoted at 0.00/0.01 jitter by a cent — not a misread.
        result = scalar_event_consistency(
            {"a": 0.01, "b": 0.02, "c": 0.00}, descending=True
        )
        self.assertTrue(result.ok, result.detail)

    def test_unpriced_legs_are_excluded(self) -> None:
        result = scalar_event_consistency({"a": 0.5, "b": 0.5, "c": None, "d": None})
        self.assertTrue(result.ok)
        self.assertEqual(result.priced_legs, 2)
        self.assertEqual(result.total_legs, 4)

    def test_all_unpriced_fails_closed(self) -> None:
        # A book you cannot verify is not a book you verified — zero priced
        # legs cannot be blessed as consistent.
        result = scalar_event_consistency({"a": None, "b": None})
        self.assertFalse(result.ok)
        self.assertEqual(result.flags, ("unpriced",))

    def test_single_ladder_leg_is_trivially_consistent(self) -> None:
        # One nested-ladder leg: no neighbour to compare against, and the
        # sum check does not apply — nothing to contradict.
        result = scalar_event_consistency({"only": 0.42}, descending=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.flags, ())

    def test_single_exclusive_leg_is_flagged(self) -> None:
        # One exclusive outcome at 0.42 leaves 0.58 of probability mass
        # unaccounted for by any leg.
        result = scalar_event_consistency({"only": 0.42})
        self.assertFalse(result.ok)
        self.assertEqual(result.flags, ("sum-below-1",))


if __name__ == "__main__":
    unittest.main()
