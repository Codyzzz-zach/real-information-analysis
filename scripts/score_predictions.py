from __future__ import annotations

import argparse
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from real_information_analysis.scoring import (
    PredictionLogError,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
