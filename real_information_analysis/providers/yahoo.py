"""Yahoo Finance price history provider.

Fetches OHLCV price history directly from Yahoo Finance's public chart API
(``query1.finance.yahoo.com/v8/finance/chart``) using only the Python
standard library — no ``yfinance`` dependency.

The chart API tolerates unauthenticated requests, so unlike the options
chain endpoint it needs no cookie/crumb handshake.  Supports stocks, ETFs,
futures (``GC=F``), forex (``EURUSD=X``) and indices.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.client import HTTPException as _HttpConnError
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .base import ProviderError, ProviderParseError, SignalProvider
from .prices import PriceBar, PriceHistory, PriceHistoryQuery

# Map PriceHistoryQuery interval codes to Yahoo chart API interval strings
_INTERVAL_MAP = {"d": "1d", "w": "1wk", "m": "1mo"}


def _limit_to_period(limit: int | None, interval: str) -> str:
    """Convert a bar *limit* into a Yahoo chart API ``range`` string.

    Yahoo's chart API uses ``range`` strings like ``"1mo"``, ``"6mo"`` etc.
    rather than explicit row counts, so we approximate conservatively.
    """
    if limit is None or limit <= 0:
        return "max"

    if interval in ("w", "1wk"):
        days = limit * 7
    elif interval in ("m", "1mo"):
        days = limit * 31
    else:
        days = limit

    # Add generous padding so we never under-fetch
    days = int(days * 1.5) + 10

    if days <= 7:
        return "5d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    if days <= 1825:
        return "5y"
    if days <= 3650:
        return "10y"
    return "max"


# ---------------------------------------------------------------------------
# Fetcher protocol (for testability)
# ---------------------------------------------------------------------------


class PriceFetcher(Protocol):
    """Abstracts raw price data retrieval so tests can supply a fake."""

    def fetch_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> list[dict[str, Any]]: ...


class _DirectYahooPriceFetcher:
    """Default fetcher backed by Yahoo Finance's public chart API.

    Hits ``query1.finance.yahoo.com/v8/finance/chart`` directly with
    ``urllib``.  The chart endpoint tolerates unauthenticated requests, so
    no cookie/crumb handshake is needed (unlike the options endpoint).
    Returns rows shaped like ``yfinance``'s output (``Date``, ``Open``,
    ``High``, ``Low``, ``Close``, ``Volume``) so the downstream parsing in
    :meth:`YahooPriceProvider.get_history` is unchanged.
    """

    _BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    # A short generic UA is deliberately used here: Yahoo's chart API
    # tolerates it, while realistic full-browser User-Agents (and TLS
    # fingerprinting libraries like curl_cffi) are flagged and rate
    # limited (429). Verified empirically — keep this minimal.
    _USER_AGENT = "Mozilla/5.0"
    _TIMEOUT = 20.0
    _RETRIES = 3
    _RETRY_DELAY = 1.0

    def fetch_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> list[dict[str, Any]]:
        params = urlencode({"range": period, "interval": interval})
        url = f"{self._BASE_URL}/{symbol}?{params}"
        data = self._get_json(url)

        results = data.get("chart", {}).get("result")
        if not results:
            err = data.get("chart", {}).get("error", {})
            msg = err.get("description") if isinstance(err, dict) else None
            raise ProviderError(
                f"Yahoo chart API returned no result for {symbol!r}"
                + (f": {msg}" if msg else "")
            )

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        rows: list[dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            # Skip bars with null OHLC (Yahoo pads with empty slots).
            o = _array_get(opens, i)
            h = _array_get(highs, i)
            low = _array_get(lows, i)
            c = _array_get(closes, i)
            if o is None and h is None and low is None and c is None:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            rows.append(
                {
                    "Date": dt,
                    "Open": o,
                    "High": h,
                    "Low": low,
                    "Close": c,
                    "Volume": _array_get(volumes, i),
                }
            )
        return rows

    def _get_json(self, url: str) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self._RETRIES + 1):
            try:
                req = Request(url, headers={"User-Agent": self._USER_AGENT})
                with urlopen(req, timeout=self._TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                # 4xx other than transient 429 is a hard failure — don't retry.
                if exc.code == 429 and attempt < self._RETRIES:
                    last_exc = exc
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise ProviderError(f"Yahoo chart API request failed: {url}") from exc
            except (URLError, TimeoutError, _HttpConnError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < self._RETRIES:
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise ProviderError(
                    f"Yahoo chart API request failed: {url}"
                ) from exc
        raise ProviderError(f"Yahoo chart API request failed: {url}") from last_exc


def _array_get(arr: list[Any] | None, i: int) -> Any:
    """Return ``arr[i]`` or ``None`` if out of range / NaN."""
    if not arr or i >= len(arr):
        return None
    val = arr[i]
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class YahooPriceProvider(SignalProvider):
    """Yahoo Finance OHLCV price history provider.

    Fetches directly from Yahoo Finance's chart API (no ``yfinance``
    dependency — pure stdlib).  Uses Yahoo Finance symbols (e.g. ``GC=F``
    for gold, ``CL=F`` for crude oil, ``SPY`` for S&P 500 ETF,
    ``EURUSD=X`` for EUR/USD forex).
    """

    provider_id = "yahoo"
    display_name = "Yahoo Finance Prices"
    capabilities = ("price_history",)

    def __init__(self, *, fetcher: PriceFetcher | None = None) -> None:
        self._fetcher: PriceFetcher = fetcher or _DirectYahooPriceFetcher()

    def get_history(self, query: PriceHistoryQuery) -> PriceHistory:
        interval = query.interval.lower().strip()
        yf_interval = _INTERVAL_MAP.get(interval)
        if yf_interval is None:
            raise ValueError(
                f"unsupported interval: {query.interval!r} (use 'd', 'w', or 'm')"
            )

        symbol = query.symbol.strip()
        period = _limit_to_period(query.limit, interval)

        raw_rows = self._fetcher.fetch_history(
            symbol, period=period, interval=yf_interval
        )

        bars: list[PriceBar] = []
        for row in raw_rows:
            dt = row.get("Date")
            if dt is None:
                continue

            # Format date as YYYY-MM-DD string
            date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]

            if query.start_date and date_str < query.start_date:
                continue
            if query.end_date and date_str > query.end_date:
                continue

            open_price = row.get("Open")
            high_price = row.get("High")
            low_price = row.get("Low")
            close_price = row.get("Close")

            if any(v is None for v in (open_price, high_price, low_price, close_price)):
                continue

            volume_raw = row.get("Volume")
            volume = float(volume_raw) if volume_raw is not None else None

            bars.append(
                PriceBar(
                    date=date_str,
                    open=float(open_price),
                    high=float(high_price),
                    low=float(low_price),
                    close=float(close_price),
                    volume=volume,
                )
            )

        if query.limit is not None and query.limit >= 0:
            bars = bars[-query.limit:]

        return PriceHistory(
            symbol=symbol,
            raw_symbol=query.symbol,
            interval=interval,
            provider_id=self.provider_id,
            bars=tuple(bars),
        )
