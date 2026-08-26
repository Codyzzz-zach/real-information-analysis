#!/usr/bin/env python3
"""P2 main experiment — Step 4: fetch data for the e2e question.

Question: 中美 AI 以后是否会发展出统一的生态？
Runs the signal set selected via SKILL.md Step 2/3 with gather().
Does NOT modify any skill code. Output: e2e_experiment/main_data.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from real_information_analysis import (
    PolymarketProvider, PolymarketEventQuery,
    KalshiProvider, KalshiMarketQuery,
    DeribitProvider, DeribitFuturesCurveQuery,
    USTreasuryProvider, YieldCurveQuery,
    EdgarProvider, EdgarInsiderQuery,
    BisProvider, BisRateQuery,
    WorldBankProvider, WorldBankQuery,
    FearGreedProvider,
    gather,
)

edgar = EdgarProvider(user_email="ria-e2e-main@example.com")

US_RD_TICKERS = ["NVDA", "AMD", "INTC", "AVGO", "QCOM", "MRVL", "ASML", "AMAT", "KLAC", "LRCX", "MSFT", "GOOGL", "META", "AMZN"]
CN_RD_TICKERS = ["BABA", "BIDU", "PDD", "JD", "NTES", "BILI"]


def _cap_trend_to_dict(t):
    return {
        "ticker": t.ticker,
        "company_name": t.company_name,
        "concept": t.concept,
        "currency": t.currency,
        "latest_fiscal_year": t.latest_fiscal_year,
        "latest_value": t.latest_value,
        "yoy_growth_pct": t.yoy_growth_pct,
        "history": [{"fy": p.fiscal_year, "value": p.value, "period_end": p.period_end} for p in t.history],
    }


def _insider_to_dict(t):
    return {
        "reporting_owner": t.reporting_owner,
        "transaction_label": t.transaction_label,
        "is_purchase": t.is_purchase,
        "is_sale": t.is_sale,
        "shares": t.shares,
        "price_per_share": t.price_per_share,
        "transaction_date": t.transaction_date,
    }


def _yield_curve_to_dict(v):
    return {"curve_kind": v.curve_kind, "date": v.date,
            "points": [{"tenor": p.tenor, "value": p.value} for p in v.points]}


def _bis_rates_to_dict(v):
    return [{"country": r.country, "period": r.period, "rate": r.rate} for r in v]


def _wb_to_dict(v):
    return [{"country_code": p.country_code, "date": p.date, "value": p.value} for p in v.points]


def _deribit_to_dict(v):
    return {"currency": v.currency,
            "points": [{"instrument_name": p.instrument_name, "is_perpetual": p.is_perpetual,
                        "mid_price": p.mid_price, "mark_price": p.mark_price,
                        "annualized_basis": p.annualized_basis_vs_perpetual} for p in v.points]}


def main() -> None:
    t0 = time.time()
    tasks = {
        "pm_ai_events": lambda: PolymarketProvider().list_events(PolymarketEventQuery(slug_contains="artificial-intelligence", limit=8)),
        "kalshi_markets": lambda: KalshiProvider().list_markets(KalshiMarketQuery(limit=20)),
        "us_rd_capex": lambda: edgar.get_capital_trends(tickers=US_RD_TICKERS, concept="R&D", years=5),
        "cn_rd_capex": lambda: edgar.get_capital_trends(tickers=CN_RD_TICKERS, concept="R&D", years=5),
        "insider_nvda": lambda: edgar.get_insider_transactions_detail(EdgarInsiderQuery(ticker="NVDA", limit=10)),
        "insider_baba": lambda: edgar.get_insider_transactions_detail(EdgarInsiderQuery(ticker="BABA", limit=10)),
        "treasury_yield": lambda: USTreasuryProvider().latest_yield_curve(),
        "bis_rates": lambda: BisProvider().get_policy_rates(BisRateQuery(countries=("US", "CN"), start_year=2023)),
        "wb_gdp": lambda: WorldBankProvider().get_indicator(WorldBankQuery(indicator="NY.GDP.MKTP.KD.ZG", countries=("US", "CN"))),
        "btc_basis": lambda: DeribitProvider().get_futures_term_structure(DeribitFuturesCurveQuery(currency="BTC")),
        "fear_greed": lambda: FearGreedProvider().get_index(),
    }

    result = gather(tasks, timeout_seconds=300, max_workers=11)
    elapsed = time.time() - t0

    payload: dict = {"probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "elapsed_seconds": round(elapsed, 1)}
    for key in sorted(tasks.keys()):
        if key in result.errors:
            err = result.errors[key]
            payload[key] = {"status": "MISSING", "error": f"{type(err).__name__}: {err}"}
            print(f"MISSING {key}: {type(err).__name__}: {err}")
        else:
            v = result.results[key]
            if key == "pm_ai_events":
                payload[key] = {"status": "OK", "events": [{"title": e.title, "slug": e.slug} for e in v]}
                print(f"OK pm_ai_events: {len(v)} events")
            elif key == "kalshi_markets":
                payload[key] = {"status": "OK", "markets": [{"ticker": m.ticker, "title": m.title} for m in v]}
                print(f"OK kalshi_markets: {len(v)} markets")
            elif key in ("us_rd_capex", "cn_rd_capex"):
                payload[key] = {"status": "OK", "companies": [_cap_trend_to_dict(t) for t in v]}
                print(f"OK {key}: {len(v)} companies")
            elif key in ("insider_nvda", "insider_baba"):
                payload[key] = {"status": "OK", "trades": [_insider_to_dict(t) for t in v]}
                print(f"OK {key}: {len(v)} trades")
            elif key == "treasury_yield":
                payload[key] = {"status": "OK", **(_yield_curve_to_dict(v) if v else {})}
                print(f"OK treasury_yield: {len(v.points) if v else 0} pts")
            elif key == "bis_rates":
                payload[key] = {"status": "OK", "points": _bis_rates_to_dict(v)}
                print(f"OK bis_rates: {len(v)} pts")
            elif key == "wb_gdp":
                payload[key] = {"status": "OK", "points": _wb_to_dict(v)}
                print(f"OK wb_gdp: {len(v.points)} pts")
            elif key == "btc_basis":
                payload[key] = {"status": "OK", **_deribit_to_dict(v)}
                print(f"OK btc_basis: {len(v.points)} pts")
            elif key == "fear_greed":
                payload[key] = {"status": "OK", "score": v.score, "rating": v.rating}
                print(f"OK fear_greed: {v.score:.1f} {v.rating}")

    out_path = Path(__file__).resolve().parent / "main_data.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"\nSaved: {out_path} (elapsed {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
