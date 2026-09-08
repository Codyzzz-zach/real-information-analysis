from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from real_information_analysis.scoring import (
    PredictionLogError,
    ledger_coherence_lint,
    ledger_composition_check,
    load_predictions,
    render_report,
    score_predictions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score the prediction ledger: Brier score + calibration."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["predictions"],
        help="JSONL files or directories of them (default: predictions/).",
    )
    parser.add_argument(
        "--buckets",
        type=int,
        default=10,
        help="Number of calibration buckets (default: 10).",
    )
    parser.add_argument(
        "--check-composition",
        action="store_true",
        help="Also lint the fast/slow horizon mix (>=50%% of entries must "
        "resolve within 180 days) and exit non-zero when it fails.",
    )
    parser.add_argument(
        "--check-coherence",
        action="store_true",
        help="Also lint semantic coherence (exclusive-group sums, "
        "polarity traces, market_implied traceability). Errors exit "
        "non-zero; warnings are printed but do not fail.",
    )
    args = parser.parse_args()

    if args.buckets < 1:
        print(f"[error] --buckets must be >= 1, got {args.buckets}", file=sys.stderr)
        return 1

    try:
        predictions = load_predictions(args.paths)
    except PredictionLogError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(render_report(score_predictions(predictions, bucket_count=args.buckets)))

    exit_code = 0

    if args.check_coherence:
        coherence = ledger_coherence_lint(predictions)
        if coherence.issues:
            print(f"\nCoherence: {len(coherence.issues)} finding(s)")
            for issue in coherence.issues:
                print(f"  [{issue.severity}] {issue.code}: {issue.message}")
        else:
            print("\nCoherence: clean")
        if not coherence.ok:
            exit_code = 1

    if args.check_composition:
        composition = ledger_composition_check(predictions)
        status = "OK" if composition.ok else "FAIL — calibration loop too slow"
        print(
            f"\nComposition: {composition.fast}/{composition.total} fast (<=180d), "
            f"{composition.slow} slow, {composition.undated} undated — {status}"
        )
        if not composition.ok:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
