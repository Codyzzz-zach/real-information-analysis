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

__all__ = [
    "ReliabilityVerdict",
    "probability_reliability",
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
