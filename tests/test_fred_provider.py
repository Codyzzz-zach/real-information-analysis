"""Tests for FredProvider — FRED economic data.

Follows the project's FakeHttpClient pattern (no real network).  The
provider's ``get_series`` issues two HTTP calls (series metadata, then
observations), so the fake client routes by URL substring.
"""

from __future__ import annotations

import os
import unittest
from typing import Any, Mapping
from unittest.mock import patch

from digital_oracle.providers.base import ProviderParseError
from digital_oracle.providers.fred import (
    FRED_SERIES,
    FredProvider,
    FredSearchQuery,
    FredSeriesQuery,
    resolve_series_id,
)


# ---------------------------------------------------------------------------
# Sample FRED JSON payloads (shape matches the live API)
# ---------------------------------------------------------------------------

SAMPLE_META_JSON: dict[str, Any] = {
    "seriess": [
        {
            "id": "VIXCLS",
            "title": "CBOE Volatility Index: VIX",
            "frequency": "Daily, Close",
            "units": "Index",
        }
    ]
}

SAMPLE_OBSERVATIONS_JSON: dict[str, Any] = {
    "observations": [
        {"date": "2026-04-10", "value": "19.23"},
        {"date": "2026-04-09", "value": "21.05"},
        {"date": "2026-04-08", "value": "."},  # FRED missing value — must be skipped
        {"date": "2026-04-07", "value": "22.10"},
    ]
}

SAMPLE_EMPTY_OBSERVATIONS_JSON: dict[str, Any] = {"observations": []}

SAMPLE_SEARCH_JSON: dict[str, Any] = {
    "seriess": [
        {
            "id": "VIXCLS",
            "title": "CBOE Volatility Index: VIX",
            "frequency": "Daily",
            "units": "Index",
            "observation_start": "1990-01-02",
            "observation_end": "2026-04-10",
            "popularity": 95,
        },
        {
            "id": "VIX9D",
            "title": "CBOE Volatility Index: VIX (9-Day)",
            "frequency": "Daily, Close",
            "units": "Index",
            "observation_start": "2021-01-04",
            "observation_end": "2026-04-10",
            "popularity": 31,
        },
    ]
}

SAMPLE_SEARCH_EMPTY_JSON: dict[str, Any] = {"seriess": []}


# ---------------------------------------------------------------------------
# Fake HTTP client — routes by URL
# ---------------------------------------------------------------------------


class _RoutingFakeClient:
    """Returns canned JSON based on which FRED endpoint the URL targets."""

    def __init__(
        self,
        *,
        meta: dict[str, Any] = SAMPLE_META_JSON,
        observations: dict[str, Any] = SAMPLE_OBSERVATIONS_JSON,
        search: dict[str, Any] = SAMPLE_SEARCH_JSON,
    ) -> None:
        self._responses = {
            "/series/observations": observations,
            "/series/search": search,
            "/series": meta,  # metadata call (bare /series, not /series/...)
        }
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    def get_json(
        self, url: str, *, params: Mapping[str, object] | None = None
    ) -> Any:
        self.calls.append((url, params))
        # Order matters: "/series/observations" and "/series/search" must be
        # checked before the bare "/series" metadata route.
        for suffix, payload in (
            ("/series/observations", self._responses["/series/observations"]),
            ("/series/search", self._responses["/series/search"]),
            ("/series", self._responses["/series"]),
        ):
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"unexpected URL in fake client: {url}")


# ---------------------------------------------------------------------------
# get_series
# ---------------------------------------------------------------------------


class FredProviderGetSeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = _RoutingFakeClient()
        self.provider = FredProvider(api_key="TEST_KEY", http_client=self.fake)

    def test_parses_observations_and_skips_missing_values(self) -> None:
        series = self.provider.get_series(FredSeriesQuery(series_id="VIXCLS", limit=30))

        self.assertEqual(series.series_id, "VIXCLS")
        self.assertEqual(series.title, "CBOE Volatility Index: VIX")
        self.assertEqual(series.frequency, "Daily, Close")
        self.assertEqual(series.units, "Index")
        # 4 rows in sample, 1 is "." (missing) → 3 observations.
        self.assertEqual(len(series.observations), 3)
        self.assertEqual(series.observations[0].date, "2026-04-10")
        self.assertAlmostEqual(series.observations[0].value, 19.23)

    def test_latest_returns_newest_observation(self) -> None:
        # sort_order defaults to "desc" → observations[0] is newest.
        series = self.provider.get_series(FredSeriesQuery(series_id="VIXCLS"))
        self.assertEqual(series.latest.date, "2026-04-10")
        self.assertAlmostEqual(series.latest_value, 19.23)

    def test_empty_observations_returns_empty_tuple(self) -> None:
        fake = _RoutingFakeClient(observations=SAMPLE_EMPTY_OBSERVATIONS_JSON)
        provider = FredProvider(api_key="TEST_KEY", http_client=fake)
        series = provider.get_series(FredSeriesQuery(series_id="VIXCLS"))
        self.assertEqual(series.observations, ())
        self.assertIsNone(series.latest)
        self.assertIsNone(series.latest_value)

    def test_api_key_passed_to_both_requests(self) -> None:
        self.provider.get_series(FredSeriesQuery(series_id="VIXCLS"))
        # Two calls: metadata + observations.
        self.assertEqual(len(self.fake.calls), 2)
        for _url, params in self.fake.calls:
            assert params is not None
            self.assertEqual(params["api_key"], "TEST_KEY")
            self.assertEqual(params["file_type"], "json")

    def test_limit_and_sort_order_passed_to_observations_request(self) -> None:
        self.provider.get_series(
            FredSeriesQuery(series_id="VIXCLS", limit=50, sort_order="asc")
        )
        # The observations call is the one whose URL ends with /series/observations.
        obs_call = next(
            (p for url, p in self.fake.calls if url.endswith("/series/observations"))
        )
        assert obs_call is not None
        self.assertEqual(obs_call["limit"], 50)
        self.assertEqual(obs_call["sort_order"], "asc")

    def test_observation_start_end_and_frequency_passed(self) -> None:
        self.provider.get_series(
            FredSeriesQuery(
                series_id="VIXCLS",
                observation_start="2026-01-01",
                observation_end="2026-04-10",
                frequency="m",
            )
        )
        obs_call = next(
            (p for url, p in self.fake.calls if url.endswith("/series/observations"))
        )
        assert obs_call is not None
        self.assertEqual(obs_call["observation_start"], "2026-01-01")
        self.assertEqual(obs_call["observation_end"], "2026-04-10")
        self.assertEqual(obs_call["frequency"], "m")

    def test_non_dict_payload_raises(self) -> None:
        fake = _RoutingFakeClient(observations=["not", "a", "dict"])  # type: ignore[arg-type]
        provider = FredProvider(api_key="TEST_KEY", http_client=fake)
        with self.assertRaises(ProviderParseError):
            provider.get_series(FredSeriesQuery(series_id="VIXCLS"))


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------


class FredProviderGetLatestTests(unittest.TestCase):
    def test_returns_only_newest_observation(self) -> None:
        fake = _RoutingFakeClient()
        provider = FredProvider(api_key="TEST_KEY", http_client=fake)
        latest = provider.get_latest("VIXCLS")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.date, "2026-04-10")  # type: ignore[union-attr]

    def test_get_latest_passes_limit_one(self) -> None:
        fake = _RoutingFakeClient()
        provider = FredProvider(api_key="TEST_KEY", http_client=fake)
        provider.get_latest("vixcls")  # lower-case input — should be normalised
        obs_call = next(
            (p for url, p in fake.calls if url.endswith("/series/observations"))
        )
        assert obs_call is not None
        self.assertEqual(obs_call["limit"], 1)
        self.assertEqual(obs_call["series_id"], "VIXCLS")


# ---------------------------------------------------------------------------
# search_series
# ---------------------------------------------------------------------------


class FredProviderSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = _RoutingFakeClient()
        self.provider = FredProvider(api_key="TEST_KEY", http_client=self.fake)

    def test_parses_search_results(self) -> None:
        results = self.provider.search_series(FredSearchQuery(search_text="VIX", limit=5))
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].series_id, "VIXCLS")
        self.assertEqual(results[0].title, "CBOE Volatility Index: VIX")
        self.assertEqual(results[0].popularity, 95)
        self.assertEqual(results[1].series_id, "VIX9D")

    def test_empty_search_returns_empty_list(self) -> None:
        fake = _RoutingFakeClient(search=SAMPLE_SEARCH_EMPTY_JSON)
        provider = FredProvider(api_key="TEST_KEY", http_client=fake)
        results = provider.search_series(FredSearchQuery(search_text="zzz"))
        self.assertEqual(results, [])

    def test_search_passes_search_text_and_limit(self) -> None:
        self.provider.search_series(FredSearchQuery(search_text="gold", limit=10))
        url, params = self.fake.calls[0]
        self.assertTrue(url.endswith("/series/search"))
        assert params is not None
        self.assertEqual(params["search_text"], "gold")
        self.assertEqual(params["limit"], 10)


# ---------------------------------------------------------------------------
# Construction & key resolution
# ---------------------------------------------------------------------------


class FredProviderConstructionTests(unittest.TestCase):
    def test_missing_api_key_raises(self) -> None:
        # Ensure the env var is not set during this test.
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                FredProvider()
            self.assertIn("FRED API key", str(ctx.exception))

    def test_reads_key_from_env_var(self) -> None:
        with patch.dict(os.environ, {"FRED_API_KEY": "ENV_KEY"}):
            provider = FredProvider()
        self.assertEqual(provider.api_key, "ENV_KEY")

    def test_explicit_key_overrides_env_var(self) -> None:
        with patch.dict(os.environ, {"FRED_API_KEY": "ENV_KEY"}):
            provider = FredProvider(api_key="EXPLICIT_KEY")
        self.assertEqual(provider.api_key, "EXPLICIT_KEY")

    def test_describe(self) -> None:
        provider = FredProvider(api_key="TEST_KEY")
        meta = provider.describe()
        self.assertEqual(meta.provider_id, "fred")
        self.assertIn("economic_series", meta.capabilities)


# ---------------------------------------------------------------------------
# resolve_series_id / FRED_SERIES shortcuts
# ---------------------------------------------------------------------------


class ResolveSeriesIdTests(unittest.TestCase):
    def test_resolves_known_shortcut(self) -> None:
        self.assertEqual(resolve_series_id("VIX"), "VIXCLS")
        self.assertEqual(resolve_series_id("HY_OAS"), "BAMLH0A0HYM2")
        self.assertEqual(resolve_series_id("FEDFUNDS"), "FEDFUNDS")

    def test_case_insensitive(self) -> None:
        self.assertEqual(resolve_series_id("vix"), "VIXCLS")
        self.assertEqual(resolve_series_id("hy_oas"), "BAMLH0A0HYM2")

    def test_unknown_name_passed_through(self) -> None:
        # Unknown shortcuts are returned as-is (assumed to already be a series ID).
        self.assertEqual(resolve_series_id("DGS10"), "DGS10")

    def test_curated_series_dict_populated(self) -> None:
        # Sanity check that the shortcut table has the key entries SKILL.md advertises.
        for key in ("VIX", "TED", "HY_OAS", "T10Y2Y", "T10YIE", "CPI", "GDP", "FEDFUNDS"):
            self.assertIn(key, FRED_SERIES, f"missing curated shortcut: {key}")


if __name__ == "__main__":
    unittest.main()
