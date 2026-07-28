"""Yahoo Finance options chain provider.

Fetches US equity options chains directly from Yahoo Finance's v7 options
endpoint (no ``yfinance`` dependency — pure stdlib) and computes
Black-Scholes Greeks using only ``math.erf``.

The v7 options endpoint requires a ``crumb`` bound to a session cookie; the
fetcher manages that handshake internally (see :class:`_DirectYahooOptionsFetcher`).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from http.client import HTTPException as _HttpConnError
from typing import Any, Protocol, Sequence
from urllib.error import HTTPError, URLError

from ._coerce import _coerce_float, _coerce_int
from .base import ProviderError, ProviderParseError, SignalProvider


# ---------------------------------------------------------------------------
# Black-Scholes Greeks (pure stdlib, no scipy needed)
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using ``math.erf`` (exact)."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class OptionGreeks:
    """Black-Scholes Greeks for a single option contract."""

    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1% move in IV


def black_scholes_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str,
) -> OptionGreeks | None:
    """Compute Black-Scholes Greeks.

    Args:
        S: Underlying price.
        K: Strike price.
        T: Time to expiration in years.
        r: Risk-free rate (annualised, e.g. 0.045 for 4.5%).
        sigma: Implied volatility (annualised, e.g. 0.30 for 30%).
        option_type: ``"call"`` or ``"put"``.

    Returns:
        :class:`OptionGreeks` or ``None`` if inputs are invalid.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma * sigma / 2.0) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    pdf_d1 = _norm_pdf(d1)

    gamma = pdf_d1 / (S * sigma * sqrt_T)
    vega = S * pdf_d1 * sqrt_T / 100.0  # per 1pp IV move

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -S * pdf_d1 * sigma / (2.0 * sqrt_T)
            - r * K * math.exp(-r * T) * _norm_cdf(d2)
        ) / 365.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (
            -S * pdf_d1 * sigma / (2.0 * sqrt_T)
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
        ) / 365.0

    return OptionGreeks(delta=delta, gamma=gamma, theta=theta, vega=vega)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptionsChainQuery:
    """Query parameters for fetching an options chain."""

    ticker: str
    expiration: str | None = None  # YYYY-MM-DD; ``None`` → nearest expiration
    risk_free_rate: float = 0.045  # annualised; used for Greeks calculation
    compute_greeks: bool = True


@dataclass(frozen=True)
class OptionContract:
    """A single option contract with optional Greeks."""

    contract_symbol: str
    option_type: str  # "call" or "put"
    expiration: str  # YYYY-MM-DD
    strike: float
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None  # (bid + ask) / 2
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None
    in_the_money: bool | None = None
    greeks: OptionGreeks | None = None


@dataclass(frozen=True)
class OptionsExpirations:
    """Available expiration dates for a ticker."""

    ticker: str
    expirations: tuple[str, ...]


@dataclass
class OptionsChain:
    """Full options chain for a single expiration date."""

    ticker: str
    expiration: str
    underlying_price: float | None
    calls: tuple[OptionContract, ...]
    puts: tuple[OptionContract, ...]

    # -- convenience properties ------------------------------------------

    @property
    def atm_strike(self) -> float | None:
        """Strike nearest to the underlying price."""
        if self.underlying_price is None:
            return None
        all_strikes = [c.strike for c in self.calls]
        if not all_strikes:
            all_strikes = [p.strike for p in self.puts]
        if not all_strikes:
            return None
        return min(all_strikes, key=lambda s: abs(s - self.underlying_price))

    @property
    def atm_call(self) -> OptionContract | None:
        """Nearest ATM call."""
        strike = self.atm_strike
        if strike is None:
            return None
        return next((c for c in self.calls if c.strike == strike), None)

    @property
    def atm_put(self) -> OptionContract | None:
        """Nearest ATM put."""
        strike = self.atm_strike
        if strike is None:
            return None
        return next((p for p in self.puts if p.strike == strike), None)

    @property
    def atm_iv(self) -> float | None:
        """ATM implied volatility (average of ATM call and put IV)."""
        ivs = [
            c.implied_volatility
            for c in [self.atm_call, self.atm_put]
            if c is not None and c.implied_volatility is not None
        ]
        if not ivs:
            return None
        return sum(ivs) / len(ivs)

    def implied_move(self) -> float | None:
        """ATM straddle implied move as a fraction of the underlying.

        Multiply by 100 to get a percentage.
        """
        call = self.atm_call
        put = self.atm_put
        if call is None or put is None or self.underlying_price is None:
            return None
        call_mid = call.mid if call.mid is not None else call.last_price
        put_mid = put.mid if put.mid is not None else put.last_price
        if call_mid is None or put_mid is None or self.underlying_price <= 0:
            return None
        return (call_mid + put_mid) / self.underlying_price

    @property
    def put_call_volume_ratio(self) -> float | None:
        """Total put volume / total call volume."""
        call_vol = sum(c.volume for c in self.calls if c.volume is not None)
        put_vol = sum(p.volume for p in self.puts if p.volume is not None)
        if call_vol == 0:
            return None
        return put_vol / call_vol

    @property
    def put_call_oi_ratio(self) -> float | None:
        """Total put OI / total call OI."""
        call_oi = sum(c.open_interest for c in self.calls if c.open_interest is not None)
        put_oi = sum(p.open_interest for p in self.puts if p.open_interest is not None)
        if call_oi == 0:
            return None
        return put_oi / call_oi

    @property
    def total_volume(self) -> int:
        """Total volume across all contracts."""
        return sum(c.volume for c in self.calls if c.volume is not None) + sum(
            p.volume for p in self.puts if p.volume is not None
        )

    @property
    def total_open_interest(self) -> int:
        """Total open interest across all contracts."""
        return sum(
            c.open_interest for c in self.calls if c.open_interest is not None
        ) + sum(p.open_interest for p in self.puts if p.open_interest is not None)

    def max_pain(self) -> float | None:
        """Max pain strike – the strike where option writers would pay least.

        For each candidate strike, sums the intrinsic value that ITM options
        would pay out weighted by open interest and picks the strike that
        minimises the total.
        """
        all_strikes = sorted({c.strike for c in self.calls} | {p.strike for p in self.puts})
        if not all_strikes:
            return None

        min_pain = float("inf")
        max_pain_strike = all_strikes[0]

        for test_strike in all_strikes:
            total_pain = 0.0
            for c in self.calls:
                oi = c.open_interest or 0
                intrinsic = max(test_strike - c.strike, 0.0)
                total_pain += intrinsic * oi
            for p in self.puts:
                oi = p.open_interest or 0
                intrinsic = max(p.strike - test_strike, 0.0)
                total_pain += intrinsic * oi
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = test_strike

        return max_pain_strike


# ---------------------------------------------------------------------------
# Fetcher protocol (for testability)
# ---------------------------------------------------------------------------


class _ChainRows:
    """Container for raw option chain data (list-of-dicts)."""

    __slots__ = ("calls", "puts")

    def __init__(
        self, calls: list[dict[str, Any]], puts: list[dict[str, Any]]
    ) -> None:
        self.calls = calls
        self.puts = puts


class OptionsFetcher(Protocol):
    """Protocol that abstracts option data retrieval.

    The default implementation talks to Yahoo's v7 options endpoint
    directly (no ``yfinance``); tests can supply a fake.
    """

    def fetch_expirations(self, ticker: str) -> tuple[str, ...]: ...
    def fetch_chain(self, ticker: str, expiration: str) -> _ChainRows: ...
    def fetch_underlying_price(self, ticker: str) -> float | None: ...


class _DirectYahooOptionsFetcher:
    """Default fetcher backed by Yahoo Finance's v7 options API.

    Yahoo's options endpoint requires a ``crumb`` query parameter that is
    bound to a session cookie.  The crumb is *single-use per cookiejar* —
    fetching a second crumb on the same cookiejar invalidates the first.
    Therefore this fetcher lazily initialises one ``CookieJar`` + one crumb
    on first use and reuses them for the rest of its lifetime.  It never
    re-fetches the crumb.

    A short ``Mozilla/5.0`` User-Agent is used deliberately: realistic
    full-browser User-Agents are flagged by Yahoo and rate-limited (429),
    while the minimal UA is tolerated.  Same finding as the price-history
    chart endpoint.

    Requires only the Python standard library.
    """

    _OPTIONS_URL = "https://query1.finance.yahoo.com/v7/finance/options/{ticker}"
    _CONSENT_URL = "https://fc.yahoo.com"
    _CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
    _USER_AGENT = "Mozilla/5.0"
    _TIMEOUT = 25.0
    _RETRIES = 4
    _RETRY_DELAY = 1.0

    def __init__(self) -> None:
        import http.cookiejar
        import urllib.request

        self._cookiejar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookiejar)
        )
        self._opener.addheaders = [("User-Agent", self._USER_AGENT)]
        self._crumb: str | None = None

    # -- credential lifecycle -------------------------------------------

    def _ensure_crumb(self) -> str:
        """Return the session crumb, initialising it once on first call.

        Yahoo intermittently drops TLS connections (``SSL: UNEXPECTED_EOF``),
        so the consent + crumb handshake is retried up to ``_RETRIES`` times.
        HTTP 404 from the consent endpoint is expected — it still sets the
        cookies Yahoo expects — and is therefore not retried.
        """
        if self._crumb is not None:
            return self._crumb

        last_exc: Exception | None = None
        for attempt in range(1, self._RETRIES + 1):
            # Step 1: seed the cookiejar.  fc.yahoo.com returns 404 but still
            # sets the A1/A3 cookies Yahoo expects.
            try:
                self._opener.open(self._CONSENT_URL, timeout=self._TIMEOUT)
            except HTTPError:
                # 404 is expected — cookies are set regardless.
                pass
            except (URLError, TimeoutError, _HttpConnError) as exc:
                last_exc = exc
                if attempt < self._RETRIES:
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise ProviderError(
                    f"Yahoo consent endpoint unreachable: {exc}"
                ) from exc

            # Step 2: fetch the crumb bound to these cookies.
            try:
                body = self._opener.open(self._CRUMB_URL, timeout=self._TIMEOUT).read()
                crumb = body.decode("utf-8").strip()
            except HTTPError as exc:
                raise ProviderError(
                    f"Yahoo crumb endpoint returned HTTP {exc.code}; "
                    "options chain unavailable"
                ) from exc
            except (URLError, TimeoutError, _HttpConnError) as exc:
                last_exc = exc
                if attempt < self._RETRIES:
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise ProviderError(
                    f"Yahoo crumb endpoint unreachable: {exc}"
                ) from exc

            if not crumb:
                raise ProviderError(
                    "Yahoo returned an empty crumb; options chain unavailable"
                )
            self._crumb = crumb
            return crumb

        raise ProviderError(
            "Yahoo crumb handshake failed after "
            f"{self._RETRIES} retries"
        ) from last_exc

    def _get_options(self, ticker: str, *, date_ts: int | None = None) -> dict[str, Any]:
        """Call the v7 options endpoint with retries, returning parsed JSON."""
        crumb = self._ensure_crumb()
        from urllib.parse import quote, urlencode

        params: dict[str, object] = {"crumb": crumb}
        if date_ts is not None:
            params["date"] = date_ts
        url = f"{self._OPTIONS_URL.format(ticker=ticker)}?{urlencode(params)}"

        last_exc: Exception | None = None
        for attempt in range(1, self._RETRIES + 1):
            try:
                body = self._opener.open(url, timeout=self._TIMEOUT).read()
                return json.loads(body.decode("utf-8"))
            except HTTPError as exc:
                if exc.code in (429, 401, 500, 502, 503) and attempt < self._RETRIES:
                    last_exc = exc
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise ProviderError(
                    f"Yahoo options API failed for {ticker!r}: HTTP {exc.code}"
                ) from exc
            except (URLError, TimeoutError, _HttpConnError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < self._RETRIES:
                    time.sleep(self._RETRY_DELAY)
                    continue
                raise ProviderError(
                    f"Yahoo options API failed for {ticker!r}: {exc}"
                ) from exc
        raise ProviderError(
            f"Yahoo options API failed for {ticker!r} after {self._RETRIES} retries"
        ) from last_exc

    # -- OptionsFetcher protocol ----------------------------------------

    def fetch_expirations(self, ticker: str) -> tuple[str, ...]:
        data = self._get_options(ticker)
        result = self._extract_result(data, ticker)
        raw_exps = result.get("expirationDates") or []
        return tuple(self._ts_to_date(ts) for ts in raw_exps)

    def fetch_chain(self, ticker: str, expiration: str) -> _ChainRows:
        date_ts = self._expiration_to_ts(ticker, expiration)
        data = self._get_options(ticker, date_ts=date_ts)
        result = self._extract_result(data, ticker)
        options = result.get("options") or [{}]
        chain = options[0] if options else {}
        calls = self._normalise_contracts(chain.get("calls") or [])
        puts = self._normalise_contracts(chain.get("puts") or [])
        return _ChainRows(calls=calls, puts=puts)

    def fetch_underlying_price(self, ticker: str) -> float | None:
        data = self._get_options(ticker)
        result = self._extract_result(data, ticker)
        quote = result.get("quote") or {}
        price = quote.get("regularMarketPrice")
        if price is None:
            price = quote.get("lastPrice")
        return float(price) if price is not None else None

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _extract_result(data: dict[str, Any], ticker: str) -> dict[str, Any]:
        results = (data.get("optionChain") or {}).get("result") or []
        if not results:
            err = (data.get("optionChain") or {}).get("error") or {}
            msg = err.get("description") if isinstance(err, dict) else None
            raise ProviderError(
                f"Yahoo options API returned no result for {ticker!r}"
                + (f": {msg}" if msg else "")
            )
        return results[0]

    def _expiration_to_ts(self, ticker: str, expiration: str) -> int:
        """Resolve a ``YYYY-MM-DD`` expiration to Yahoo's unix timestamp.

        Yahoo's ``?date=`` parameter expects the unix-seconds timestamp it
        advertises in ``expirationDates``.  We map the human date back to
        that exact timestamp rather than re-deriving it, so DST / time-zone
        quirks can't shift it.
        """
        data = self._get_options(ticker)
        result = self._extract_result(data, ticker)
        date_to_ts = {
            self._ts_to_date(ts): ts for ts in (result.get("expirationDates") or [])
        }
        ts = date_to_ts.get(expiration)
        if ts is None:
            raise ProviderError(
                f"expiration {expiration!r} not available for {ticker!r}"
            )
        return ts

    @staticmethod
    def _ts_to_date(ts: int) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _normalise_contracts(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pass through v7 contract dicts, normalising NaN to None.

        v7 returns plain JSON (no NaN), but normalisation keeps behaviour
        consistent with the previous yfinance-backed fetcher.
        """
        out: list[dict[str, Any]] = []
        for d in raw:
            clean: dict[str, Any] = {}
            for k, v in d.items():
                if isinstance(v, float) and math.isnan(v):
                    v = None
                clean[k] = v
            out.append(clean)
        return out


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class YFinanceProvider(SignalProvider):
    """Yahoo Finance options chain provider.

    Provides options chain data (IV, Greeks, put/call ratio, max pain)
    fetched directly from Yahoo's v7 options endpoint — no ``yfinance``
    dependency.  Black-Scholes Greeks are computed with ``math.erf``.
    """

    provider_id = "yfinance"
    display_name = "Yahoo Finance Options"
    capabilities = ("options_chain", "options_expirations", "greeks")

    def __init__(self, *, fetcher: OptionsFetcher | None = None) -> None:
        self._fetcher: OptionsFetcher = fetcher or _DirectYahooOptionsFetcher()

    # -- public API --------------------------------------------------------

    def get_expirations(self, ticker: str) -> OptionsExpirations:
        """List available options expiration dates for *ticker*."""
        exps = self._fetcher.fetch_expirations(ticker.upper())
        return OptionsExpirations(ticker=ticker.upper(), expirations=exps)

    def get_chain(self, query: OptionsChainQuery) -> OptionsChain:
        """Fetch the options chain for a specific expiration.

        If *query.expiration* is ``None`` the nearest available expiration is
        used.
        """
        ticker = query.ticker.upper()

        # Determine expiration
        expiration = query.expiration
        if expiration is None:
            exps = self._fetcher.fetch_expirations(ticker)
            if not exps:
                raise ProviderParseError(
                    f"no options expirations found for {ticker}"
                )
            expiration = exps[0]

        # Fetch raw data
        raw = self._fetcher.fetch_chain(ticker, expiration)
        underlying = self._fetcher.fetch_underlying_price(ticker)

        # Time to expiration (years)
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
            days_to_exp = (exp_date - date.today()).days
            T = max(days_to_exp, 0) / 365.0
        except ValueError:
            T = 0.0

        # Parse contracts
        calls = self._parse_contracts(
            raw.calls,
            "call",
            expiration,
            underlying,
            T,
            query.risk_free_rate,
            query.compute_greeks,
        )
        puts = self._parse_contracts(
            raw.puts,
            "put",
            expiration,
            underlying,
            T,
            query.risk_free_rate,
            query.compute_greeks,
        )

        return OptionsChain(
            ticker=ticker,
            expiration=expiration,
            underlying_price=underlying,
            calls=tuple(calls),
            puts=tuple(puts),
        )

    # -- internal ----------------------------------------------------------

    def _parse_contracts(
        self,
        rows: list[dict[str, Any]],
        option_type: str,
        expiration: str,
        underlying: float | None,
        T: float,
        risk_free_rate: float,
        compute_greeks: bool,
    ) -> list[OptionContract]:
        contracts: list[OptionContract] = []
        for row in rows:
            strike = _coerce_float(row.get("strike"))
            if strike is None:
                continue

            bid = _coerce_float(row.get("bid"))
            ask = _coerce_float(row.get("ask"))
            mid = None
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0

            iv = _coerce_float(row.get("impliedVolatility"))

            # Compute Greeks when possible
            greeks = None
            if compute_greeks and iv is not None and underlying is not None and T > 0:
                greeks = black_scholes_greeks(
                    S=underlying,
                    K=strike,
                    T=T,
                    r=risk_free_rate,
                    sigma=iv,
                    option_type=option_type,
                )

            volume = _coerce_int(row.get("volume"))
            oi = _coerce_int(row.get("openInterest"))

            contract_symbol = str(row.get("contractSymbol", ""))
            last_price = _coerce_float(row.get("lastPrice"))
            in_the_money = row.get("inTheMoney")
            if in_the_money is not None:
                in_the_money = bool(in_the_money)

            contracts.append(
                OptionContract(
                    contract_symbol=contract_symbol,
                    option_type=option_type,
                    expiration=expiration,
                    strike=strike,
                    last_price=last_price,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    volume=volume,
                    open_interest=oi,
                    implied_volatility=iv,
                    in_the_money=in_the_money,
                    greeks=greeks,
                )
            )

        return contracts
