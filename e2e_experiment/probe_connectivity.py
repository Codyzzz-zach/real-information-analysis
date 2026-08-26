#!/usr/bin/env python3
"""P1 — connectivity probe for all 15 providers (real network, minimal requests).

One-shot probe script for the RIA e2e experiment. Does NOT modify any skill code.
Usage: python3 e2e_experiment/probe_connectivity.py
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
    YahooPriceProvider, PriceHistoryQuery,
    StooqProvider,
    DeribitProvider, DeribitFuturesCurveQuery,
    USTreasuryProvider, YieldCurveQuery,
    WebSearchProvider, WebSearchQuery,
    CftcCotProvider, CftcCotQuery,
    CoinGeckoProvider, CoinGeckoPriceQuery,
    EdgarProvider, EdgarInsiderQuery,
    BisProvider, BisRateQuery,
    WorldBankProvider, WorldBankQuery,
    YFinanceProvider, OptionsChainQuery,
    FearGreedProvider,
    FredProvider, FredSeriesQuery,
    gather,
)


def _summarize(key: str, val) -> str:
    if val is None:
        return "None"
    t = type(val).__name__
    if isinstance(val, list):
        return f"{t}[{len(val)}]"
    if hasattr(val, "bars"):
        return f"{t}({val.symbol}, {len(val.bars)} bars, last={val.bars[-1].close if val.bars else None})"
    if hasattr(val, "points"):
        return f"{t}({len(val.points)} pts)"
    if hasattr(val, "score"):
        return f"{t}(score={val.score}, {val.rating})"
    if hasattr(val, "snippets"):
        return f"{t}({len(val.snippets)} results)"
    if hasattr(val, "contracts"):
        return f"{t}({len(val.contracts)} contracts, atm_iv={val.atm_iv})"
    if hasattr(val, "observations"):
        return f"{t}({len(val.observations)} obs)"
    return f"{t}(...)"


def main() -> None:
    t0 = time.time()
    edgar = EdgarProvider(user_email="ria-e2e-probe@example.com")

    tasks = {
        "polymarket": lambda: PolymarketProvider().list_events(PolymarketEventQuery(slug_contains="ai", limit=3)),
        "kalshi": lambda: KalshiProvider().list_markets(KalshiMarketQuery(series_ticker="KXINX", limit=3)),
        "yahoo_price": lambda: YahooPriceProvider().get_history(PriceHistoryQuery(symbol="SPY", limit=3)),
        "stooq": lambda: StooqProvider().get_history(PriceHistoryQuery(symbol="spy.us", limit=3)),
        "deribit": lambda: DeribitProvider().get_futures_term_structure(DeribitFuturesCurveQuery(currency="BTC")),
        "treasury": lambda: USTreasuryProvider().latest_yield_curve(),
        "web_search": lambda: WebSearchProvider().search(WebSearchQuery(query="test probe", max_results=2)),
        "cftc": lambda: CftcCotProvider().list_reports(CftcCotQuery(commodity_name="GOLD", limit=2)),
        "coingecko": lambda: CoinGeckoProvider().get_prices(CoinGeckoPriceQuery(coin_ids=("bitcoin",))),
        "edgar_insider": lambda: edgar.get_insider_transactions_detail(EdgarInsiderQuery(ticker="NVDA", limit=3)),
        "bis": lambda: BisProvider().get_policy_rates(BisRateQuery(countries=("US", "CN"), start_year=2024)),
        "worldbank": lambda: WorldBankProvider().get_indicator(WorldBankQuery(indicator="NY.GDP.MKTP.CD", countries=("US", "CN"))),
        "yfinance_options": lambda: YFinanceProvider().get_chain(OptionsChainQuery(ticker="SPY", compute_greeks=False)),
        "fear_greed": lambda: FearGreedProvider().get_index(),
        "fred": lambda: FredProvider(api_key="INVALID_KEY_FOR_PROBE").get_series(FredSeriesQuery(series_id="VIXCLS", limit=3)),
    }

    result = gather(tasks, timeout_seconds=45, max_workers=15)
    elapsed = time.time() - t0

    rows = []
    print(f"\n{'Provider':<16}{'Status':<8}Detail")
    print("-" * 80)
    for key in sorted(tasks.keys()):
        if key in result.errors:
            err = result.errors[key]
            msg = f"{type(err).__name__}: {err}"
            print(f"{key:<16}FAIL    {msg[:100]}")
            rows.append({"provider": key, "status": "FAIL", "error": msg})
        else:
            summary = _summarize(key, result.results[key])
            print(f"{key:<16}OK      {summary}")
            rows.append({"provider": key, "status": "OK", "summary": summary})

    ok = sum(1 for r in rows if r["status"] == "OK")
    print(f"\nResult: {ok}/{len(rows)} reachable, elapsed {elapsed:.1f}s")

    out = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_seconds": round(elapsed, 1),
        "matrix": rows,
    }
    out_path = Path(__file__).resolve().parent / "probe_result.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
