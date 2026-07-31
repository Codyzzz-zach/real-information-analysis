**English** | [中文](README.md)

# Real Information Analysis 📈

Real Information Analysis is an open-source Skill that lets AI Agents mine macro-event trends from massive financial data.

Works with OpenClaw / Claude Code / Cursor / Codex.

We live in an era of extreme noise. Social media is flooded with emotional predictions — someone says housing is about to crash, someone says gold is going to the moon, someone says war breaks out tomorrow. These opinions chase crowd sentiment rather than rational analysis grounded in objective data.

But trading data is different.

Price is absolutely rational — when someone puts real money on an outcome, they think a lot harder than when they post a short video.

But price is not truth. It does not contain all information, and it mixes in noise, hedging premiums, liquidity distortion, and policy intervention. Its value is this: **it is the least-noisy, most incentive-aligned, and most auditable information source we know of.**

Real Information Analysis turns this judgment into an executable tool. It plugs into 15 authoritative financial data sources — **from prediction markets like Polymarket and Kalshi, to US Treasury yield curves, CFTC institutional positioning, SEC insider trades, central bank rates, and crypto derivatives.**

It doesn't read newspapers, news articles, short videos, or podcasts. It answers questions about housing prices, gold trends, Bitcoin cycles, and military conflict probabilities purely through price signals mined from financial data — delivering structured probability estimates with full reasoning chains.

In a sense, it's an attempt to rebuild "real information" in an era of noise.

## What can it answer?

- "What's the probability of WW3?"
- "Will there be a US recession this year?"
- "Is AI in a bubble?"
- "Is now a good time to buy gold?"
- "Has Bitcoin bottomed?"
- "Is NVDA options premium overpriced?"

If there's a market pricing an outcome, Real Information Analysis can give you a probability estimate backed by trading data.

## Data Sources

| Provider | Data Type | Purpose |
|----------|-----------|---------|
| Polymarket | Prediction market contracts | Event probability pricing |
| Kalshi | SEC-regulated binary contracts | US political/economic events |
| Yahoo Finance (Price) | Stocks/ETFs/FX/Commodities | Price history and trends |
| Stooq | Stocks/ETFs/FX/Commodities (CSV) | European equities / independent price source |
| Deribit | Crypto derivatives | Futures term structure, options IV |
| US Treasury | Treasury yields | Yield curves, inflation expectations |
| FRED | Federal Reserve economic data | VIX, OAS, TED, CPI, GDP structured time series (free key) |
| CFTC COT | Futures positioning | Institutional direction (smart money) |
| CoinGecko | Crypto spot | BTC/ETH price, market cap |
| SEC EDGAR | Insider trades | Form 4 buy/sell signals |
| BIS | Central bank data | Policy rates, credit-to-GDP gaps |
| World Bank | Development indicators | GDP, population, trade |
| Yahoo Finance (Options) | US options chains | IV, Greeks, put/call ratio, max pain |
| Fear & Greed | Market sentiment | CNN 7-signal composite → 0-100 sentiment score |
| Web Search | Web search | CDS, BDI, and other supplementary data |

All 15 data sources are pure Python stdlib with zero external dependencies. FRED needs a free API key; the rest need no key.

## Installation

### OpenClaw

```bash
clawhub install real-information-analysis
```

### Other AI Agents (Claude Code / Cursor / Codex / ...)

Just tell your agent:

> Install this open-source project and read SKILL.md as your working instructions: https://github.com/komako-workshop/real-information-analysis

The agent will clone the repo, read the methodology, and call the providers on its own.

### Prerequisites

- [uv](https://docs.astral.sh/uv/) — Python package manager, used to run skill scripts at runtime
- All 15 data sources have zero external dependencies (pure Python stdlib). FredProvider needs a free FRED API key ([register](https://fredaccount.stlouisfed.org/apikeys)).

## How It Works

1. **Understand the question** — decompose into core variables, time window, and priceability
2. **Select signals** — pick 3+ signals from independent mechanisms based on question type
3. **Fetch in parallel** — use `gather()` to call multiple providers concurrently
4. **Contradiction analysis** — find disagreements between markets, explain why both can be right
5. **Output report** — structured multi-layer signal tables + probability estimates + scenario analysis

## Project Structure

```
real-information-analysis/
├── SKILL.md                # Skill definition (read by OpenClaw)
├── real_information_analysis/  # Python source code
│   ├── concurrent.py       # Parallel execution utilities
│   ├── http.py             # HTTP client abstraction
│   ├── snapshots.py        # HTTP response recording/replay (for tests)
│   ├── scoring.py          # Prediction scoring (Brier score + calibration)
│   └── providers/          # 15 data providers
├── predictions/            # Prediction ledger (jsonl — every report's estimates are logged here)
├── references/             # API reference
│   ├── providers.md        # Provider API docs
│   └── symbols.md          # Trading symbol directory
├── scripts/                # Demo scripts
└── tests/                  # Unit tests + fixtures
```

## Design Principles

- **Zero dependencies first** — all 15 providers use only the Python standard library, no `pip install` needed
- **Dependency injection** — all providers accept an optional `http_client` parameter for easy testing
- **Partial failure tolerance** — one data source going down doesn't break the rest
- **Snapshot testing** — record real HTTP responses, run tests offline in CI
- **Falsifiable** — every report's probability estimates are logged to `predictions/`, resolved when due, and judged by Brier score

## License

MIT © 2026 komako-workshop — see [LICENSE](LICENSE).
