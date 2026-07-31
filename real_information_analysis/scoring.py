"""Prediction track record: load JSONL prediction logs and measure calibration.

Every report the skill produces ends with structured probability estimates
(SKILL.md Step 6). Those estimates are only a claim until they are scored.
This module loads the prediction ledger (``predictions/*.jsonl``) and computes:

* Brier score — mean squared error of probability forecasts (0 = perfect)
* baselines — Brier score of the recorded base rates / market-implied
  probabilities on the same resolved records, so skill can be measured
  against "just trust the market" and "just use base rates"
* calibration buckets — mean predicted probability vs observed frequency

Pure stdlib, no network, no external dependencies.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .providers._coerce import _coerce_float

__all__ = [
    "CalibrationBucket",
    "Prediction",
    "PredictionLogError",
    "ScoreReport",
    "load_predictions",
    "parse_prediction",
    "render_report",
    "score_predictions",
]


class PredictionLogError(ValueError):
    """Raised when a prediction ledger line cannot be parsed."""


def _parse_probability(value: object, *, field_name: str, line_ref: str) -> float:
    # bool is a subclass of int — guard it explicitly (matching _coerce_float convention)
    if isinstance(value, bool):
        raise PredictionLogError(f"{line_ref}: {field_name} must be a number in [0, 1], got bool")
    # Delegate to the established coercion utility (returns None for NaN, Inf, and unparseable values)
    parsed = _coerce_float(value)
    if parsed is None:
        detail = repr(value) if isinstance(value, str) else type(value).__name__
        raise PredictionLogError(
            f"{line_ref}: {field_name} must be a number in [0, 1], got {detail}"
        ) from None
    if not 0.0 <= parsed <= 1.0:
        raise PredictionLogError(f"{line_ref}: {field_name} out of range [0, 1]: {parsed}")
    return parsed


def _parse_optional_probability(
    record: Mapping[str, Any], field_name: str, *, line_ref: str
) -> float | None:
    value = record.get(field_name)
    if value is None:
        return None
    return _parse_probability(value, field_name=field_name, line_ref=line_ref)


_OUTCOME_ALIASES = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
}


def _parse_outcome(value: object, *, line_ref: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        outcome = _OUTCOME_ALIASES.get(value.strip().lower())
        if outcome is not None:
            return outcome
    raise PredictionLogError(
        f"{line_ref}: outcome must be true/false, 1/0, yes/no, or null (pending), got {value!r}"
    )


def _required_str(record: Mapping[str, Any], field_name: str, *, line_ref: str) -> str | None:
    """Like ``_optional_str`` but raises on non-string non-None values.

    Consistent with ``_parse_probability`` and ``_parse_outcome``: invalid types
    in a ledger record are always errors, not silent drops.
    """
    value = record.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PredictionLogError(
            f"{line_ref}: {field_name} must be a string, got {type(value).__name__}"
        )
    return value


@dataclass(frozen=True)
class Prediction:
    """One scored probability estimate from a report.

    *outcome* is ``None`` while the prediction is pending, ``True``/``False``
    once resolved against its resolution_criteria.
    """

    question: str
    probability: float
    scenario: str | None = None
    market_implied: float | None = None
    base_rate: float | None = None
    created_at: str | None = None
    resolve_by: str | None = None
    resolution_criteria: str | None = None
    outcome: bool | None = None
    resolved_at: str | None = None
    notes: str | None = None

    @property
    def resolved(self) -> bool:
        return self.outcome is not None


def parse_prediction(record: Mapping[str, Any], *, line_ref: str) -> Prediction:
    question = record.get("question")
    if not isinstance(question, str) or not question.strip():
        raise PredictionLogError(f"{line_ref}: missing or empty 'question'")
    if "probability" not in record:
        raise PredictionLogError(f"{line_ref}: missing 'probability'")
    return Prediction(
        question=question,
        probability=_parse_probability(record["probability"], field_name="probability", line_ref=line_ref),
        scenario=_required_str(record, "scenario", line_ref=line_ref),
        market_implied=_parse_optional_probability(record, "market_implied", line_ref=line_ref),
        base_rate=_parse_optional_probability(record, "base_rate", line_ref=line_ref),
        created_at=_required_str(record, "created_at", line_ref=line_ref),
        resolve_by=_required_str(record, "resolve_by", line_ref=line_ref),
        resolution_criteria=_required_str(record, "resolution_criteria", line_ref=line_ref),
        outcome=_parse_outcome(record.get("outcome"), line_ref=line_ref),
        resolved_at=_required_str(record, "resolved_at", line_ref=line_ref),
        notes=_required_str(record, "notes", line_ref=line_ref),
    )


def load_predictions(paths: str | Path | Iterable[str | Path]) -> list[Prediction]:
    """Load predictions from JSONL files and/or directories of them.

    Directories are scanned for ``*.jsonl`` (sorted, non-recursive).
    Blank lines and lines starting with ``#`` are ignored. Malformed lines
    raise :class:`PredictionLogError` naming the file and line number.

    Missing paths emit a warning to stderr (to avoid silent no-ops on typos).
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]

    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("*.jsonl")))
        elif path.is_file():
            files.append(path)
        else:
            print(f"[scoring] path not found, skipping: {path}", file=sys.stderr)

    predictions: list[Prediction] = []
    for file_path in files:
        with file_path.open() as fh:
            for line_no, line in enumerate(fh, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                line_ref = f"{file_path}:{line_no}"
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise PredictionLogError(f"{line_ref}: invalid JSON: {exc}") from exc
                if not isinstance(record, Mapping):
                    raise PredictionLogError(f"{line_ref}: expected a JSON object")
                predictions.append(parse_prediction(record, line_ref=line_ref))
    return predictions


def _brier(pairs: Iterable[tuple[float, bool]]) -> float | None:
    """Mean squared error. 0 = perfect, 0.25 = coin-flip at 50%."""
    total = 0.0
    count = 0
    for probability, outcome in pairs:
        total += (probability - float(outcome)) ** 2
        count += 1
    return total / count if count else None


def _brier_skill_score(ours: float | None, baseline: float | None) -> float | None:
    """Brier skill: 1 - ours / baseline.

    Positive = better than baseline. Returns ``None`` when undefined
    (no data, or baseline is literally perfect — the latter is rendered
    specially by ``render_report`` so the user can tell the two apart).
    """
    if ours is None or baseline is None or baseline == 0.0:
        return None
    return 1.0 - ours / baseline


@dataclass(frozen=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_frequency: float | None


@dataclass(frozen=True)
class ScoreReport:
    total: int
    resolved: int
    pending: int
    brier: float | None
    base_rate_brier: float | None
    market_brier: float | None
    brier_skill_vs_base_rate: float | None
    brier_skill_vs_market: float | None
    buckets: tuple[CalibrationBucket, ...]


def score_predictions(predictions: Iterable[Prediction], *, bucket_count: int = 10) -> ScoreReport:
    """Score a set of predictions.

    Baselines are computed on the subset of resolved records that carry the
    corresponding fields (base_rate / market_implied), so comparisons are
    always like-for-like on the same questions.
    """
    if bucket_count < 1:
        raise ValueError(f"bucket_count must be >= 1, got {bucket_count}")

    total = 0  # type: ignore[assignment]
    resolved: list[Prediction] = []
    for p in predictions:
        total += 1
        if p.resolved:
            resolved.append(p)

    brier = _brier((p.probability, p.outcome) for p in resolved)  # type: ignore[misc]

    # Single-pass extraction per baseline — compute base-rate and market
    # subsets once each, then feed into _brier for both the baseline and
    # the matched ours-on-same-subset computation.
    base_resolved: list[tuple[float, float, bool]] = [
        (p.probability, p.base_rate, p.outcome)  # type: ignore[arg-type]
        for p in resolved
        if p.base_rate is not None
    ]
    market_resolved: list[tuple[float, float, bool]] = [
        (p.probability, p.market_implied, p.outcome)  # type: ignore[arg-type]
        for p in resolved
        if p.market_implied is not None
    ]

    base_rate_brier = _brier((br, o) for _, br, o in base_resolved) if base_resolved else None
    market_brier = _brier((mi, o) for _, mi, o in market_resolved) if market_resolved else None
    our_brier_on_base_subset = _brier((prob, o) for prob, _, o in base_resolved) if base_resolved else None
    our_brier_on_market_subset = _brier((prob, o) for prob, _, o in market_resolved) if market_resolved else None

    # O(N) single-pass bucketing: compute index directly to avoid the
    # floating-point boundary comparison bug (e.g. 6 * 0.1 ≠ 0.6 in IEEE-754).
    width = 1.0 / bucket_count
    bucket_members: list[list[Prediction]] = [[] for _ in range(bucket_count)]
    for p in resolved:
        idx = min(int(p.probability * bucket_count), bucket_count - 1)
        bucket_members[idx].append(p)

    buckets: list[CalibrationBucket] = []
    for idx, members in enumerate(bucket_members):
        lower = idx * width
        upper = lower + width
        if members:
            mean_predicted = sum(p.probability for p in members) / len(members)
            observed = sum(float(p.outcome) for p in members) / len(members)
        else:
            mean_predicted = None
            observed = None
        buckets.append(
            CalibrationBucket(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_predicted=mean_predicted,
                observed_frequency=observed,
            )
        )

    return ScoreReport(
        total=total,
        resolved=len(resolved),
        pending=total - len(resolved),
        brier=brier,
        base_rate_brier=base_rate_brier,
        market_brier=market_brier,
        brier_skill_vs_base_rate=_brier_skill_score(our_brier_on_base_subset, base_rate_brier),
        brier_skill_vs_market=_brier_skill_score(our_brier_on_market_subset, market_brier),
        buckets=tuple(buckets),
    )


def _format_baseline_line(label: str, baseline_brier: float | None, skill: float | None) -> str | None:
    """Render one baseline comparison line.

    Distinguishes three cases the old code collapsed: no data (``None``),
    baseline was literally perfect (brier == 0.0, division undefined), and
    a computable skill score.
    """
    if baseline_brier is None:
        return None
    if baseline_brier == 0.0:
        skill_str = "baseline perfect"
    elif skill is not None:
        skill_str = f"{skill:+.2%}"
    else:
        skill_str = "n/a"
    return f"  vs {label} baseline: {baseline_brier:.4f}  skill {skill_str}"


def render_report(report: ScoreReport) -> str:
    """Render a ScoreReport as a plain-text summary with a calibration table."""
    lines = [
        f"Predictions: {report.total} total, {report.resolved} resolved, {report.pending} pending",
    ]
    if report.brier is None:
        lines.append("Brier score: n/a (no resolved predictions yet)")
    else:
        lines.append(f"Brier score: {report.brier:.4f} (0 = perfect, 0.25 = coin-flip at 50%)")

    base_line = _format_baseline_line("base-rate", report.base_rate_brier, report.brier_skill_vs_base_rate)
    if base_line is not None:
        lines.append(base_line)
    market_line = _format_baseline_line("market-implied", report.market_brier, report.brier_skill_vs_market)
    if market_line is not None:
        lines.append(market_line)

    lines.append("")
    lines.append("Calibration (resolved only):")
    lines.append("  bucket       n   predicted   observed      gap")
    for bucket in report.buckets:
        if bucket.count == 0:
            continue
        if bucket.mean_predicted is None or bucket.observed_frequency is None:
            raise RuntimeError(
                f"calibration bucket [{bucket.lower:.2f}-{bucket.upper:.2f}]"
                f" has {bucket.count} members but null stats — invariant violation"
            )
        gap = bucket.observed_frequency - bucket.mean_predicted
        lines.append(
            f"  {bucket.lower:.2f}-{bucket.upper:.2f}"
            f"  {bucket.count:>3}"
            f"      {bucket.mean_predicted:.3f}"
            f"      {bucket.observed_frequency:.3f}"
            f"   {gap:+.3f}"
        )
    return "\n".join(lines)
