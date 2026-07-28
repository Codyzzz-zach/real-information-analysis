"""Tests for YahooPriceProvider and the direct chart-API fetcher.

The fetcher is covered via a stubbed HTTP layer (no real network); the
provider's ``get_history`` parsing is covered via a fake fetcher following
the same dependency-injection pattern the original tests used.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

from digital_oracle.providers.base import ProviderError
from digital_oracle.providers.yahoo import (
    YahooPriceProvider,
    _DirectYahooPriceFetcher,
    _array_get,
)
from digital_oracle.providers.prices import PriceHistoryQuery


# ---------------------------------------------------------------------------
# Sample Yahoo chart API payloads
# ---------------------------------------------------------------------------

# Two valid daily bars, one null-padded bar (should be skipped).
SAMPLE_CHART_JSON: dict[str, Any] = {
    "chart": {
        "result": [
            {
                "meta": {"symbol": "GC=F", "regularMarketPrice": 4070.8},
                "timestamp": [1784640000, 1784726400, 1784812800],
                "indicators": {
                    "quote": [
                        {
                            "open": [4050.0, 4100.0, None],
                            "high": [4080.0, 4150.0, None],
                            "low": [4040.0, 4090.0, None],
                            "close": [4071.1, 4146.9, None],
                            "volume": [87, 133, None],
                        }
                    ]
                },
            }
        ]
    }
}

SAMPLE_EMPTY_JSON: dict[str, Any] = {"chart": {"result": None, "error": {"description": "No data found"}}}


class _StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass


# ---------------------------------------------------------------------------
# _DirectYahooPriceFetcher parsing
# ---------------------------------------------------------------------------


class DirectYahooPriceFetcherTests(unittest.TestCase):
    def test_parses_ohlc_rows_and_skips_null_bars(self) -> None:
        fetcher = _DirectYahooPriceFetcher()
        with patch.object(fetcher, "_get_json", return_value=SAMPLE_CHART_JSON):
            rows = fetcher.fetch_history("GC=F", period="5d", interval="1d")

        # 3 timestamps but the 3rd is all-null -> skipped, leaving 2 rows.
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["Close"], 4071.1)
        self.assertEqual(rows[0]["Volume"], 87)
        self.assertIsInstance(rows[0]["Date"], datetime)
        # Timestamps are UTC.
        self.assertEqual(rows[0]["Date"].tzinfo, timezone.utc)

    def test_null_volume_becomes_none(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1784640000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [4050.0],
                                    "high": [4080.0],
                                    "low": [4040.0],
                                    "close": [4071.1],
                                    "volume": [None],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        fetcher = _DirectYahooPriceFetcher()
        with patch.object(fetcher, "_get_json", return_value=payload):
            rows = fetcher.fetch_history("GC=F", period="5d", interval="1d")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["Volume"])

    def test_no_result_raises_provider_error(self) -> None:
        fetcher = _DirectYahooPriceFetcher()
        with patch.object(fetcher, "_get_json", return_value=SAMPLE_EMPTY_JSON):
            with self.assertRaises(ProviderError):
                fetcher.fetch_history("BOGUS", period="5d", interval="1d")

    def test_builds_chart_api_url_with_range_and_interval(self) -> None:
        fetcher = _DirectYahooPriceFetcher()
        captured: dict[str, str] = {}

        def fake_get_json(url: str) -> dict[str, Any]:
            captured["url"] = url
            return SAMPLE_CHART_JSON

        with patch.object(fetcher, "_get_json", side_effect=fake_get_json):
            fetcher.fetch_history("SPY", period="1mo", interval="1wk")
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/SPY", captured["url"])
        self.assertIn("range=1mo", captured["url"])
        self.assertIn("interval=1wk", captured["url"])


class ArrayGetHelperTests(unittest.TestCase):
    def test_returns_none_for_nan(self) -> None:
        import math

        self.assertIsNone(_array_get([math.nan], 0))

    def test_returns_none_out_of_range(self) -> None:
        self.assertIsNone(_array_get([1.0], 5))
        self.assertIsNone(_array_get(None, 0))

    def test_returns_value(self) -> None:
        self.assertEqual(_array_get([1.0, 2.0], 1), 2.0)


# ---------------------------------------------------------------------------
# YahooPriceProvider.get_history via fake fetcher
# ---------------------------------------------------------------------------


class FakePriceFetcher:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, str]] = []

    def fetch_history(
        self, symbol: str, *, period: str, interval: str
    ) -> list[dict[str, Any]]:
        self.calls.append((symbol, period, interval))
        return list(self.rows)


def _make_row(date: str, o: float, h: float, low: float, c: float, vol: float | None) -> dict[str, Any]:
    return {
        "Date": datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc),
        "Open": o,
        "High": h,
        "Low": low,
        "Close": c,
        "Volume": vol,
    }


class YahooPriceProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _make_row("2026-07-20", 100.0, 102.0, 99.0, 101.0, 1000.0),
            _make_row("2026-07-21", 101.0, 103.0, 100.0, 102.5, 1200.0),
            _make_row("2026-07-22", 102.5, 104.0, 102.0, 103.0, None),
        ]
        self.fake = FakePriceFetcher(self.rows)
        self.provider = YahooPriceProvider(fetcher=self.fake)

    def test_get_history_parses_bars(self) -> None:
        h = self.provider.get_history(PriceHistoryQuery(symbol="SPY", limit=10))
        self.assertEqual(h.symbol, "SPY")
        self.assertEqual(len(h.bars), 3)
        self.assertEqual(h.bars[0].date, "2026-07-20")
        self.assertAlmostEqual(h.bars[0].close, 101.0)
        self.assertIsNone(h.bars[2].volume)
        # latest convenience
        self.assertEqual(h.latest.date, "2026-07-22")

    def test_limit_truncates_to_last_n(self) -> None:
        h = self.provider.get_history(PriceHistoryQuery(symbol="SPY", limit=2))
        self.assertEqual(len(h.bars), 2)
        self.assertEqual(h.bars[0].date, "2026-07-21")

    def test_interval_mapped_to_yahoo_code(self) -> None:
        self.provider.get_history(PriceHistoryQuery(symbol="SPY", interval="w"))
        self.assertEqual(self.fake.calls[0][2], "1wk")

    def test_unsupported_interval_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.provider.get_history(PriceHistoryQuery(symbol="SPY", interval="hourly"))

    def test_null_ohlc_row_skipped(self) -> None:
        self.fake.rows.append(
            _make_row("2026-07-23", None, None, None, None, None)
        )
        h = self.provider.get_history(PriceHistoryQuery(symbol="SPY"))
        self.assertEqual(len(h.bars), 3)


if __name__ == "__main__":
    unittest.main()
