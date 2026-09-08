"""Interpretation helpers — turning raw market prices into usable signals.

Pure functions, zero dependencies, no I/O. Everything here encodes rules that
are documented in SKILL.md and grounded in the literature (see
PRODUCTIZATION_PLAN.md §R1):

* Prediction-market prices are probabilities only **mid-life**. A 23M-trade
  study of a major event-contract exchange (arXiv:2607.14430) shows quoted
  prices sit near perfect calibration in the middle of a contract's life but
  depart sharply as expiry approaches — the final stretch fits an
  insurance-demand (Prelec) curve. Combination products (parlays) carry a
  separate systematic markup on top of their legs.
* Thin books show a whale's position, not consensus (SKILL.md liquidity rule).

The function below therefore never transforms a price into "adjusted
probability" — it returns a *reliability verdict* the analyst (or LLM) must
apply as a confidence discount, keeping the judgement explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = [
    "ReliabilityVerdict",
    "ScalarEventConsistency",
    "probability_reliability",
    "scalar_event_consistency",
]


# Behaviour-matrix thresholds. The literature gives the qualitative shape
# (mid-life calibrated, late-life drifting, final minutes step-like); the
# exact cutoffs below are named constants so they can be tuned without
# touching the contract.
_EXPIRY_REGIME_SECONDS = 600  # final 10 minutes — calibration curve goes step-like
_LATE_LIFE_SECONDS = 86_400   # final 24 hours — calibration starts drifting

_THIN_MARKET_VOLUME_USD = 100_000.0

# Products whose price is a markup over the legs, not a standalone probability.
_MARKUP_PRODUCT_TYPES = frozenset({"parlay", "parlays", "combo", "combination", "multi-leg"})


@dataclass(frozen=True)
class ReliabilityVerdict:
    """How much a prediction-market price can be trusted as a probability.

    *label* is the calibration regime; *discount_factor* is the confidence
    weight the report should give the price (1.0 = take at face value);
    *flags* carry product- and book-level caveats.
    """

    label: str  # "calibrated" | "late-life" | "expiry-regime"
    discount_factor: float
    flags: tuple[str, ...]


def probability_reliability(
    *,
    seconds_to_expiry: int,
    product_type: str = "binary",
    volume_usd: float | None = None,
) -> ReliabilityVerdict:
    """Verdict on using a prediction-market price as a bare probability.

    Args:
        seconds_to_expiry: Seconds until the contract settles. Negative or
            zero (already expired) falls in the expiry regime.
        product_type: ``"binary"`` (default) or a combination product name
            (``"parlay"`` / ``"combo"`` / ``"multi-leg"`` — case-insensitive).
        volume_usd: Traded volume in USD, when known. Below
            ``_THIN_MARKET_VOLUME_USD`` adds the ``"thin-market"`` flag.

    Returns:
        :class:`ReliabilityVerdict` per the behaviour matrix:

        =============== ================== ================
        time to expiry  label              discount factor
        =============== ================== ================
        > 24 h          ``calibrated``     1.0
        10 min – 24 h   ``late-life``      0.8
        ≤ 10 min        ``expiry-regime``  0.5
        =============== ================== ================

        Combination products always add a ``"systematic-markup"`` flag
        regardless of time; thin books add ``"thin-market"``.
    """
    if seconds_to_expiry > _LATE_LIFE_SECONDS:
        label, factor = "calibrated", 1.0
    elif seconds_to_expiry > _EXPIRY_REGIME_SECONDS:
        label, factor = "late-life", 0.8
    else:
        label, factor = "expiry-regime", 0.5

    flags: list[str] = []
    if product_type.strip().lower() in _MARKUP_PRODUCT_TYPES:
        flags.append("systematic-markup")
    if volume_usd is not None and volume_usd < _THIN_MARKET_VOLUME_USD:
        flags.append("thin-market")

    return ReliabilityVerdict(label=label, discount_factor=factor, flags=tuple(flags))


# How far sub-market YES probabilities of one multi-outcome event may sum
# above 1 before the ladder is judged inconsistent. Real books sum to ~1.01
# (spread + vig); the 13-market Fed-cuts ladder observed live summed to
# 1.0115. Far beyond that, at least one leg was misread — usually a price
# taken from the wrong sub-market (the 92.75% -> 5.8% "impossible flip").
_SCALAR_SUM_TOLERANCE = 0.05

# How far any single leg may fall below its neighbours' sum path before the
# monotonicity break is flagged (noise floor for thin legs quoted 0.00/0.01).
_MONOTONICITY_TOLERANCE = 0.02


@dataclass(frozen=True)
class ScalarEventConsistency:
    """Verdict on whether one event's sub-market prices can all be true.

    Multi-outcome events price their legs under one of two semantics, and
    each has its own coherence invariant:

    * **exclusive outcomes** (Polymarket "exactly N cuts" scalar events) —
      legs are mutually exclusive and exhaustive, so YES probabilities
      must sum to ≈1;
    * **nested ladder** (Kalshi KXFED "above X%") — each leg implies the
      previous one, so probabilities must be non-increasing in X. The
      plain sum is meaningless here (legs overlap).

    A leg that breaks its invariant means the price came from a different
    sub-market than the label claims — the signature of both live
    misreading incidents this module exists to catch.
    """

    total_legs: int
    priced_legs: int
    probability_sum: float | None
    flags: tuple[str, ...]  # "sum-exceeds-1", "sum-below-1", "monotonicity-break", "unpriced"
    ok: bool

    @property
    def detail(self) -> str:
        if not self.flags:
            return f"consistent ({self.priced_legs}/{self.total_legs} legs priced)"
        return ", ".join(self.flags)


def scalar_event_consistency(
    probabilities: Mapping[str, float | None],
    *,
    order: Sequence[str] | None = None,
    descending: bool = False,
) -> ScalarEventConsistency:
    """Check that one multi-outcome event's sub-market prices cohere.

    Args:
        probabilities: label -> YES probability per sub-market (``None`` =
            unpriced leg, excluded from checks). Labels are the sub-market
            question text or ticker — anything the caller can order.
        order: explicit ordering of the labels for the ladder
            (monotonicity) check. When omitted, insertion order of the
            mapping is used. Pass an explicit ``order`` for unordered
            mappings.
        descending: which semantics the legs follow —

            * ``False`` (default) — **exclusive outcomes** ("exactly N
              cuts"): the sum check applies, monotonicity does not (the
              distribution over outcomes is typically unimodal, not flat).
            * ``True`` — **nested ladder** ("above X%"): the monotonicity
              check applies (non-increasing in X), the sum check does not
              (legs overlap, so any sum is legitimate).

    Returns:
        :class:`ScalarEventConsistency` — ``ok`` is ``True`` only when the
        applicable invariant holds (sum within
        :data:`_SCALAR_SUM_TOLERANCE` of 1, or ladder never rising by more
        than :data:`_MONOTONICITY_TOLERANCE`) and at least one leg is
        priced. An event with zero priced legs fails closed ("unpriced"):
        a book you cannot verify is not a book you verified.

    The flags are a *diagnostic*, not a probability adjustment: a flagged
    ladder means at least one leg was misread — re-address each sub-market
    by its own question text / ticker before quoting any leg as "the"
    probability (see SKILL.md multi-outcome addressing rule).
    """
    labels = list(order) if order is not None else list(probabilities)
    priced = [
        (label, probabilities[label])
        for label in labels
        if probabilities.get(label) is not None
    ]

    flags: list[str] = []
    if priced:
        total = sum(value for _, value in priced)
        if not descending:
            if total > 1.0 + _SCALAR_SUM_TOLERANCE:
                flags.append("sum-exceeds-1")
            elif total < 1.0 - _SCALAR_SUM_TOLERANCE:
                flags.append("sum-below-1")
    else:
        total = None
        flags.append("unpriced")

    if descending and len(priced) >= 2:
        for (_, prev), (_, curr) in zip(priced, priced[1:]):
            if curr > prev + _MONOTONICITY_TOLERANCE:
                flags.append("monotonicity-break")
                break

    return ScalarEventConsistency(
        total_legs=len(labels),
        priced_legs=len(priced),
        probability_sum=total,
        flags=tuple(flags),
        ok=not flags,
    )
