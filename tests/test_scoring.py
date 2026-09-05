from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_information_analysis.scoring import (
    Prediction,
    PredictionLogError,
    ledger_composition_check,
    load_predictions,
    parse_prediction,
    render_report,
    score_predictions,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")


class TestParsePrediction(unittest.TestCase):

    def test_minimal_record(self):
        prediction = parse_prediction(
            {"question": "Will X happen?", "probability": 0.3}, line_ref="test:1"
        )
        self.assertEqual(prediction.question, "Will X happen?")
        self.assertEqual(prediction.probability, 0.3)
        self.assertIsNone(prediction.outcome)
        self.assertFalse(prediction.resolved)

    def test_full_record(self):
        prediction = parse_prediction(
            {
                "question": "US recession in 2026?",
                "scenario": "NBER declares recession",
                "probability": 0.25,
                "market_implied": 0.2,
                "base_rate": 0.15,
                "created_at": "2026-07-29",
                "resolve_by": "2027-01-31",
                "resolution_criteria": "NBER announcement",
                "outcome": True,
                "resolved_at": "2026-12-01",
                "notes": "test",
            },
            line_ref="test:1",
        )
        self.assertTrue(prediction.resolved)
        self.assertEqual(prediction.market_implied, 0.2)
        self.assertEqual(prediction.base_rate, 0.15)

    def test_missing_question_raises(self):
        with self.assertRaises(PredictionLogError):
            parse_prediction({"probability": 0.5}, line_ref="test:1")

    def test_missing_probability_raises(self):
        with self.assertRaises(PredictionLogError):
            parse_prediction({"question": "Q?"}, line_ref="test:1")

    def test_probability_out_of_range_raises(self):
        with self.assertRaises(PredictionLogError):
            parse_prediction({"question": "Q?", "probability": 1.5}, line_ref="test:1")

    def test_probability_bool_raises(self):
        with self.assertRaises(PredictionLogError):
            parse_prediction({"question": "Q?", "probability": True}, line_ref="test:1")

    def test_outcome_aliases(self):
        for raw, expected in (
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("true", True),
            ("false", False),
            ("yes", True),
            ("no", False),
            ("YES", True),
            (None, None),
        ):
            prediction = parse_prediction(
                {"question": "Q?", "probability": 0.5, "outcome": raw}, line_ref="test:1"
            )
            self.assertEqual(prediction.outcome, expected, msg=f"outcome={raw!r}")

    def test_outcome_invalid_raises(self):
        with self.assertRaises(PredictionLogError):
            parse_prediction(
                {"question": "Q?", "probability": 0.5, "outcome": "maybe"}, line_ref="test:1"
            )


class TestLoadPredictions(unittest.TestCase):

    def test_load_file_skips_blank_and_comment_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-07.jsonl"
            path.write_text(
                "# ledger for July\n"
                "\n"
                + json.dumps({"question": "A?", "probability": 0.4, "outcome": True})
                + "\n"
            )
            predictions = load_predictions(path)
        self.assertEqual(len(predictions), 1)
        self.assertTrue(predictions[0].resolved)

    def test_load_directory_reads_all_jsonl_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_jsonl(root / "2026-06.jsonl", [{"question": "A?", "probability": 0.5}])
            _write_jsonl(root / "2026-07.jsonl", [{"question": "B?", "probability": 0.6}])
            (root / "notes.txt").write_text("ignored")
            predictions = load_predictions(root)
        self.assertEqual([p.question for p in predictions], ["A?", "B?"])

    def test_missing_path_is_skipped(self):
        self.assertEqual(load_predictions("does/not/exist"), [])

    def test_invalid_json_raises_with_line_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text('{"question": "A?", "probability": 0.5}\nnot json\n')
            with self.assertRaises(PredictionLogError) as ctx:
                load_predictions(path)
        self.assertIn("bad.jsonl:2", str(ctx.exception))

    def test_non_object_line_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text("[1, 2, 3]\n")
            with self.assertRaises(PredictionLogError):
                load_predictions(path)


class TestScorePredictions(unittest.TestCase):

    def test_brier_known_values(self):
        predictions = [
            Prediction(question="A", probability=0.9, outcome=True),
            Prediction(question="B", probability=0.1, outcome=False),
        ]
        report = score_predictions(predictions)
        self.assertAlmostEqual(report.brier, 0.01)
        self.assertEqual(report.total, 2)
        self.assertEqual(report.resolved, 2)
        self.assertEqual(report.pending, 0)

    def test_brier_none_without_resolved(self):
        report = score_predictions([Prediction(question="A", probability=0.5)])
        self.assertIsNone(report.brier)
        self.assertEqual(report.pending, 1)

    def test_skill_vs_base_rate(self):
        predictions = [
            Prediction(question="A", probability=0.9, base_rate=0.5, outcome=True),
            Prediction(question="B", probability=0.1, base_rate=0.5, outcome=False),
        ]
        report = score_predictions(predictions)
        self.assertAlmostEqual(report.base_rate_brier, 0.25)
        # our brier on same subset = 0.01 → skill = 1 - 0.01/0.25 = 0.96
        self.assertAlmostEqual(report.brier_skill_vs_base_rate, 0.96)

    def test_skill_vs_market(self):
        predictions = [
            Prediction(question="A", probability=0.8, market_implied=0.5, outcome=True),
        ]
        report = score_predictions(predictions)
        self.assertAlmostEqual(report.market_brier, 0.25)
        self.assertAlmostEqual(report.brier_skill_vs_market, 1.0 - 0.04 / 0.25)

    def test_baselines_use_like_for_like_subset(self):
        # record without base_rate must not pollute the base-rate baseline
        predictions = [
            Prediction(question="A", probability=0.9, base_rate=0.5, outcome=True),
            Prediction(question="B", probability=0.6, outcome=False),
        ]
        report = score_predictions(predictions)
        self.assertAlmostEqual(report.base_rate_brier, 0.25)
        self.assertIsNone(report.market_brier)

    def test_calibration_buckets(self):
        predictions = [
            Prediction(question="A", probability=0.05, outcome=False),
            Prediction(question="B", probability=0.95, outcome=True),
            Prediction(question="C", probability=1.0, outcome=True),
        ]
        report = score_predictions(predictions, bucket_count=10)
        self.assertEqual(len(report.buckets), 10)
        first = report.buckets[0]
        self.assertEqual(first.count, 1)
        self.assertAlmostEqual(first.mean_predicted, 0.05)
        self.assertAlmostEqual(first.observed_frequency, 0.0)
        last = report.buckets[-1]
        self.assertEqual(last.count, 2)  # 0.95 and 1.0 both land in the last bucket
        self.assertAlmostEqual(last.observed_frequency, 1.0)

    def test_empty_buckets_have_no_stats(self):
        report = score_predictions([Prediction(question="A", probability=0.5, outcome=True)])
        empty = [b for b in report.buckets if b.count == 0]
        self.assertTrue(empty)
        for bucket in empty:
            self.assertIsNone(bucket.mean_predicted)
            self.assertIsNone(bucket.observed_frequency)


class TestRenderReport(unittest.TestCase):

    def test_render_with_resolved(self):
        predictions = [
            Prediction(question="A", probability=0.9, base_rate=0.5, outcome=True),
            Prediction(question="B", probability=0.5),
        ]
        text = render_report(score_predictions(predictions))
        self.assertIn("2 total, 1 resolved, 1 pending", text)
        self.assertIn("Brier score: 0.0100", text)
        self.assertIn("base-rate baseline", text)
        self.assertIn("Calibration", text)

    def test_render_without_resolved(self):
        text = render_report(score_predictions([Prediction(question="A", probability=0.5)]))
        self.assertIn("no resolved predictions", text)


class TestGoldenLedger(unittest.TestCase):
    """Hand-computed acceptance fixture — PRODUCTIZATION_PLAN.md §R5."""

    def test_brier_exact_value(self) -> None:
        predictions = [
            Prediction(question="A", probability=0.7, outcome=True),   # (0.3)^2 = 0.09
            Prediction(question="B", probability=0.2, outcome=False),  # (0.2)^2 = 0.04
            Prediction(question="C", probability=0.6, outcome=True),   # (0.4)^2 = 0.16
        ]
        report = score_predictions(predictions)
        self.assertAlmostEqual(report.brier, 0.29 / 3)  # ≈ 0.0967
        self.assertEqual(report.resolved, 3)


class TestLedgerCompositionCheck(unittest.TestCase):
    """Fast/slow lint — a calibration loop must actually close (§R5)."""

    @staticmethod
    def _prediction(created_at=None, resolve_by=None):
        return Prediction(
            question="Q",
            probability=0.5,
            created_at=created_at,
            resolve_by=resolve_by,
        )

    def test_all_fast_passes(self):
        predictions = [
            self._prediction("2026-01-01", "2026-04-01"),
            self._prediction("2026-02-01", "2026-07-31"),  # exactly 180 days
        ]
        composition = ledger_composition_check(predictions)
        self.assertTrue(composition.ok)
        self.assertEqual(composition.fast, 2)

    def test_current_ledger_style_all_slow_fails(self):
        # 5-year horizons — like the initial real ledger — must fail the lint.
        predictions = [self._prediction("2026-08-27", "2031-12-31") for _ in range(3)]
        composition = ledger_composition_check(predictions)
        self.assertFalse(composition.ok)
        self.assertEqual(composition.fast, 0)
        self.assertEqual(composition.slow, 3)

    def test_half_fast_is_exactly_at_threshold(self):
        predictions = [
            self._prediction("2026-01-01", "2026-04-01"),
            self._prediction("2026-01-01", "2031-01-01"),
        ]
        self.assertTrue(ledger_composition_check(predictions).ok)
        self.assertFalse(
            ledger_composition_check(predictions, min_fast_share=0.51).ok
        )

    def test_undated_counts_against_fail_closed(self):
        predictions = [
            self._prediction("2026-01-01", "2026-04-01"),
            self._prediction("2026-01-01", None),
            self._prediction(None, "2026-04-01"),
            self._prediction("not-a-date", "2026-04-01"),
        ]
        composition = ledger_composition_check(predictions)
        self.assertEqual(composition.fast, 1)
        self.assertEqual(composition.undated, 3)
        self.assertFalse(composition.ok)

    def test_unparseable_date_format_counts_as_undated(self):
        composition = ledger_composition_check([self._prediction("01/02/2026", "03/04/2026")])
        self.assertEqual(composition.undated, 1)

    def test_custom_max_days(self):
        predictions = [self._prediction("2026-01-01", "2026-04-01")]  # 90 days
        self.assertTrue(ledger_composition_check(predictions, max_days=90).ok)
        self.assertFalse(ledger_composition_check(predictions, max_days=30).ok)

    def test_empty_ledger_fails(self):
        composition = ledger_composition_check([])
        self.assertFalse(composition.ok)
        self.assertEqual(composition.total, 0)


if __name__ == "__main__":
    unittest.main()
