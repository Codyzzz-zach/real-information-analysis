"""FRED (Federal Reserve Economic Data) provider.

Zero external dependencies (pure stdlib).  Requires a free FRED API key
from https://fredaccount.stlouisfed.org/apikeys .

Replaces multiple web-search-based data points with structured, direct
API access: VIX, high-yield OAS, TED spread, breakeven inflation,
yield curve spreads, margin debt, and more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from ..http import JsonHttpClient, UrllibJsonClient

from ._coerce import _coerce_float, _coerce_int
from .base import ProviderParseError, SignalProvider

FRED_BASE_URL = "https://api.stlouisfed.org/fred"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FredSeriesQuery:
    """Query parameters for fetching a FRED time series."""

    series_id: str  # e.g. "VIXCLS", "BAMLH0A0HYM2"
    observation_start: str | None = None  # YYYY-MM-DD
    observation_end: str | None = None  # YYYY-MM-DD
    limit: int | None = None
    sort_order: str = "desc"  # "asc" or "desc"
    frequency: str | None = None  # e.g. "m" for monthly


@dataclass(frozen=True)
class FredObservation:
    """A single observation from a FRED time series."""

    date: str  # YYYY-MM-DD
    value: float


@dataclass
class FredSeries:
    """A FRED time series with metadata and observations."""

    series_id: str
    title: str | None
    frequency: str | None  # e.g. "Daily", "Monthly"
    units: str | None  # e.g. "Percent", "Index"
    observations: tuple[FredObservation, ...]
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def latest(self) -> FredObservation | None:
        """Return the most recent observation.

        Correct regardless of ``sort_order``: dates are ISO ``YYYY-MM-DD``
        strings, so lexicographic max equals chronological max.
        """
        if not self.observations:
            return None
        return max(self.observations, key=lambda obs: obs.date)

    @property
    def latest_value(self) -> float | None:
        """Return the most recent value."""
        obs = self.latest
        return obs.value if obs else None


@dataclass(frozen=True)
class FredSearchQuery:
    """Query for searching FRED series by keyword."""

    search_text: str
    limit: int = 20


@dataclass(frozen=True)
class FredSeriesInfo:
    """Search result describing a FRED time series."""

    series_id: str
    title: str
    frequency: str | None
    units: str | None
    observation_start: str | None
    observation_end: str | None
    popularity: int | None


# ---------------------------------------------------------------------------
# Common series IDs (curated shortlist)
# ---------------------------------------------------------------------------

FRED_SERIES = {
    # Volatility & risk
    "VIX": "VIXCLS",           # CBOE Volatility Index: VIX
    # "MOVE" series has been removed from FRED — use VIX as proxy for bond vol
    "TED": "TEDRATE",          # TED Spread
    # Credit spreads
    "HY_OAS": "BAMLH0A0HYM2",  # ICE BofA US High Yield Index Option-Adjusted Spread
    "IG_OAS": "BAMLC0A0CM",    # ICE BofA US Corporate Master Option-Adjusted Spread
    # Yield curve
    "T10Y2Y": "T10Y2Y",        # 10-Year Treasury Constant Maturity Minus 2-Year Treasury
    "T10Y3M": "T10Y3M",        # 10-Year Treasury Minus 3-Month Treasury
    # Inflation
    "T10YIE": "T10YIE",        # 10-Year Breakeven Inflation Rate
    "T5YIFR": "T5YIFR",        # 5-Year Forward Inflation Expectation Rate
    # Macro
    "CPI": "CPIAUCSL",          # Consumer Price Index for All Urban Consumers
    "GDP": "GDP",               # Gross Domestic Product
    "ICSA": "ICSA",             # Initial Jobless Claims
    "UNRATE": "UNRATE",         # Unemployment Rate
    # Money & credit
    "FEDFUNDS": "FEDFUNDS",     # Federal Funds Effective Rate
    "MARGIN_DEBT": "BOGZ1FL663067003Q",  # Margin Debt
    "WALCL": "WALCL",           # Fed Balance Sheet - Total Assets
    # Commodity proxies
    # "GOLD" series (GOLDAMGBD228NLBR) has been removed from FRED (returns 400
    # "series does not exist") — use YahooPriceProvider GC=F for gold prices.
    "OIL": "DCOILWTICO",         # WTI Crude Oil Spot Price
}


def resolve_series_id(name: str) -> str:
    """Resolve a shortcut name (e.g. "VIX", "HY_OAS") to a FRED series ID."""
    return FRED_SERIES.get(name.upper(), name)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class FredProvider(SignalProvider):
    """FRED economic data provider.

    Requires a free API key from https://fredaccount.stlouisfed.org/apikeys .
    Construct as ``FredProvider(api_key="your_key_here")``.

    If *api_key* is not provided, reads from the ``FRED_API_KEY``
    environment variable as a fallback.
    """

    provider_id = "fred"
    display_name = "FRED (Federal Reserve Economic Data)"
    capabilities = ("economic_series", "series_search")

    def __init__(
        self,
        api_key: str | None = None,
        http_client: JsonHttpClient | None = None,
    ) -> None:
        import os

        self.api_key = api_key or os.environ.get("FRED_API_KEY")
        if not self.api_key:
            raise ValueError(
                "FRED API key required.\n"
                "  option 1: FredProvider(api_key='your_key')\n"
                "  option 2: export FRED_API_KEY='your_key'\n"
                "  get a free key: https://fredaccount.stlouisfed.org/apikeys"
            )
        self.http_client = http_client or UrllibJsonClient()

    # -- get_series ----------------------------------------------------------

    def get_series(self, query: FredSeriesQuery) -> FredSeries:
        """Fetch observations for a FRED time series.

        Also fetches series metadata (title, frequency, units) via a
        concurrent detail call.
        """
        sid = query.series_id.strip().upper()

        # Fetch series metadata (title, frequency, units)
        meta = self.http_client.get_json(
            f"{FRED_BASE_URL}/series",
            params={
                "series_id": sid,
                "api_key": self.api_key,
                "file_type": "json",
            },
        )
        if not isinstance(meta, dict):
            raise ProviderParseError("expected FRED series metadata to be an object")

        # Some responses wrap in "seriess"
        series_meta = meta.get("seriess")
        if isinstance(series_meta, list) and series_meta:
            series_meta = series_meta[0]
        elif not isinstance(series_meta, dict):
            series_meta = meta

        title = series_meta.get("title") if isinstance(series_meta, dict) else None
        frequency = series_meta.get("frequency") if isinstance(series_meta, dict) else None
        units = series_meta.get("units") if isinstance(series_meta, dict) else None

        # Fetch observations
        params: dict[str, object] = {
            "series_id": sid,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": query.sort_order,
        }
        if query.observation_start:
            params["observation_start"] = query.observation_start
        if query.observation_end:
            params["observation_end"] = query.observation_end
        if query.frequency:
            params["frequency"] = query.frequency
        if query.limit is not None:
            params["limit"] = query.limit

        payload = self.http_client.get_json(
            f"{FRED_BASE_URL}/series/observations",
            params=params,
        )

        if not isinstance(payload, dict):
            raise ProviderParseError("expected FRED observations payload to be an object")

        raw_obs = payload.get("observations")
        if not isinstance(raw_obs, list):
            raise ProviderParseError("expected FRED observations to be a list")

        observations: list[FredObservation] = []
        for row in raw_obs:
            if not isinstance(row, dict):
                continue
            date = str(row.get("date", ""))
            raw_value = row.get("value", ".")

            # "." means missing/NA value in FRED
            if raw_value == "." or raw_value is None:
                continue

            value = _coerce_float(raw_value)
            if value is None:
                continue

            observations.append(FredObservation(date=date, value=value))

        return FredSeries(
            series_id=sid,
            title=title,
            frequency=frequency,
            units=units,
            observations=tuple(observations),
            raw=payload,
        )

    # -- get_latest ----------------------------------------------------------

    def get_latest(self, series_id: str) -> FredObservation | None:
        """Return only the most recent observation for a series."""
        sid = series_id.strip().upper()
        series = self.get_series(FredSeriesQuery(series_id=sid, limit=1))
        return series.latest

    # -- search_series -------------------------------------------------------

    def search_series(self, query: FredSearchQuery) -> list[FredSeriesInfo]:
        """Search FRED series by keyword."""
        payload = self.http_client.get_json(
            f"{FRED_BASE_URL}/series/search",
            params={
                "search_text": query.search_text,
                "api_key": self.api_key,
                "file_type": "json",
                "limit": query.limit,
            },
        )

        if not isinstance(payload, dict):
            raise ProviderParseError("expected FRED search payload to be an object")

        raw_series = payload.get("seriess")
        if not isinstance(raw_series, list):
            raise ProviderParseError("expected FRED search results to be a list")

        results: list[FredSeriesInfo] = []
        for item in raw_series:
            if not isinstance(item, dict):
                continue
            results.append(
                FredSeriesInfo(
                    series_id=str(item.get("id", "")),
                    title=str(item.get("title", "")),
                    frequency=item.get("frequency") if isinstance(item.get("frequency"), str) else None,
                    units=item.get("units") if isinstance(item.get("units"), str) else None,
                    observation_start=item.get("observation_start") if isinstance(item.get("observation_start"), str) else None,
                    observation_end=item.get("observation_end") if isinstance(item.get("observation_end"), str) else None,
                    popularity=_coerce_int(item.get("popularity")),
                )
            )

        return results
