"""Tests for YFinanceProvider (options chain + Black-Scholes Greeks)."""

from __future__ import annotations

import math
import unittest
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

from digital_oracle.providers.yfinance_provider import (
    OptionContract,
    OptionGreeks,
    OptionsChain,
    OptionsChainQuery,
    OptionsExpirations,
    YFinanceProvider,
    _ChainRows,
    _norm_cdf,
    _norm_pdf,
    black_scholes_greeks,
)


# ---------------------------------------------------------------------------
# Dynamic expiration dates — always in the future so Greeks (T > 0) stay valid.
# Hardcoded past dates silently broke Greeks tests once the date elapsed.
# ---------------------------------------------------------------------------

def _future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).strftime("%Y-%m-%d")

# Primary expiration ~60 days out; two further expirations for "nearest" tests.
EXPIRATION = _future_date(60)
EXPIRATION_2 = _future_date(90)
EXPIRATION_3 = _future_date(120)


def _contract_symbol(strike: float, kind: str) -> str:
    """Build an OCC-style symbol consistent with EXPIRATION (YYMMDD)."""
    yymmdd = EXPIRATION[2:10].replace("-", "")
    return f"AAPL{yymmdd}{kind}{int(strike * 1000):08d}"


# ---------------------------------------------------------------------------
# Fake fetcher for testing (no yfinance / pandas needed)
# ---------------------------------------------------------------------------

_FAKE_CALLS = [
    {
        "contractSymbol": _contract_symbol(130.0, "C"),
        "strike": 130.0,
        "lastPrice": 22.50,
        "bid": 22.00,
        "ask": 23.00,
        "volume": 800,
        "openInterest": 3000,
        "impliedVolatility": 0.32,
        "inTheMoney": True,
    },
    {
        "contractSymbol": _contract_symbol(140.0, "C"),
        "strike": 140.0,
        "lastPrice": 13.20,
        "bid": 13.00,
        "ask": 13.40,
        "volume": 1500,
        "openInterest": 5000,
        "impliedVolatility": 0.28,
        "inTheMoney": True,
    },
    {
        "contractSymbol": _contract_symbol(150.0, "C"),
        "strike": 150.0,
        "lastPrice": 5.20,
        "bid": 5.00,
        "ask": 5.40,
        "volume": 3000,
        "openInterest": 8000,
        "impliedVolatility": 0.25,
        "inTheMoney": False,
    },
    {
        "contractSymbol": _contract_symbol(160.0, "C"),
        "strike": 160.0,
        "lastPrice": 1.80,
        "bid": 1.60,
        "ask": 2.00,
        "volume": 2000,
        "openInterest": 6000,
        "impliedVolatility": 0.30,
        "inTheMoney": False,
    },
    {
        "contractSymbol": _contract_symbol(170.0, "C"),
        "strike": 170.0,
        "lastPrice": 0.45,
        "bid": 0.30,
        "ask": 0.60,
        "volume": 500,
        "openInterest": 2000,
        "impliedVolatility": 0.35,
        "inTheMoney": False,
    },
]

_FAKE_PUTS = [
    {
        "contractSymbol": _contract_symbol(130.0, "P"),
        "strike": 130.0,
        "lastPrice": 0.30,
        "bid": 0.20,
        "ask": 0.40,
        "volume": 400,
        "openInterest": 1500,
        "impliedVolatility": 0.34,
        "inTheMoney": False,
    },
    {
        "contractSymbol": _contract_symbol(140.0, "P"),
        "strike": 140.0,
        "lastPrice": 1.10,
        "bid": 1.00,
        "ask": 1.20,
        "volume": 1200,
        "openInterest": 4000,
        "impliedVolatility": 0.30,
        "inTheMoney": False,
    },
    {
        "contractSymbol": _contract_symbol(150.0, "P"),
        "strike": 150.0,
        "lastPrice": 3.40,
        "bid": 3.20,
        "ask": 3.60,
        "volume": 2500,
        "openInterest": 7000,
        "impliedVolatility": 0.26,
        "inTheMoney": True,
    },
    {
        "contractSymbol": _contract_symbol(160.0, "P"),
        "strike": 160.0,
        "lastPrice": 9.50,
        "bid": 9.20,
        "ask": 9.80,
        "volume": 1800,
        "openInterest": 5500,
        "impliedVolatility": 0.28,
        "inTheMoney": True,
    },
    {
        "contractSymbol": _contract_symbol(170.0, "P"),
        "strike": 170.0,
        "lastPrice": 19.00,
        "bid": 18.50,
        "ask": 19.50,
        "volume": 300,
        "openInterest": 1000,
        "impliedVolatility": 0.33,
        "inTheMoney": True,
    },
]


class FakeOptionsFetcher:
    """Fake fetcher that returns predetermined data without yfinance."""

    def __init__(
        self,
        *,
        expirations: tuple[str, ...] = (EXPIRATION, EXPIRATION_2, EXPIRATION_3),
        underlying_price: float | None = 150.0,
        calls: list[dict[str, Any]] | None = None,
        puts: list[dict[str, Any]] | None = None,
    ):
        self.expirations = expirations
        self.underlying_price = underlying_price
        self.calls = calls if calls is not None else list(_FAKE_CALLS)
        self.puts = puts if puts is not None else list(_FAKE_PUTS)
        self.fetch_calls: list[tuple[str, str | None]] = []

    def fetch_expirations(self, ticker: str) -> tuple[str, ...]:
        return self.expirations

    def fetch_chain(self, ticker: str, expiration: str) -> _ChainRows:
        self.fetch_calls.append((ticker, expiration))
        return _ChainRows(calls=self.calls, puts=self.puts)

    def fetch_underlying_price(self, ticker: str) -> float | None:
        return self.underlying_price


# ---------------------------------------------------------------------------
# Black-Scholes unit tests
# ---------------------------------------------------------------------------


class NormFunctionsTests(unittest.TestCase):
    def test_norm_cdf_zero(self) -> None:
        self.assertAlmostEqual(_norm_cdf(0.0), 0.5, places=10)

    def test_norm_cdf_large_positive(self) -> None:
        self.assertAlmostEqual(_norm_cdf(6.0), 1.0, places=6)

    def test_norm_cdf_large_negative(self) -> None:
        self.assertAlmostEqual(_norm_cdf(-6.0), 0.0, places=6)

    def test_norm_cdf_symmetry(self) -> None:
        self.assertAlmostEqual(_norm_cdf(1.0) + _norm_cdf(-1.0), 1.0, places=10)

    def test_norm_pdf_zero(self) -> None:
        expected = 1.0 / math.sqrt(2.0 * math.pi)
        self.assertAlmostEqual(_norm_pdf(0.0), expected, places=10)

    def test_norm_pdf_symmetry(self) -> None:
        self.assertAlmostEqual(_norm_pdf(1.5), _norm_pdf(-1.5), places=10)


class BlackScholesGreeksTests(unittest.TestCase):
    """Test Black-Scholes Greeks against known values."""

    def test_atm_call_delta_near_half(self) -> None:
        """ATM call delta should be approximately 0.5."""
        g = black_scholes_greeks(S=100, K=100, T=1.0, r=0.0, sigma=0.20, option_type="call")
        self.assertIsNotNone(g)
        assert g is not None
        self.assertAlmostEqual(g.delta, 0.5, delta=0.06)

    def test_atm_put_delta_near_minus_half(self) -> None:
        """ATM put delta should be approximately -0.5."""
        g = black_scholes_greeks(S=100, K=100, T=1.0, r=0.0, sigma=0.20, option_type="put")
        self.assertIsNotNone(g)
        assert g is not None
        self.assertAlmostEqual(g.delta, -0.5, delta=0.06)

    def test_call_put_delta_parity(self) -> None:
        """Call delta - put delta should be approximately 1."""
        gc = black_scholes_greeks(S=100, K=100, T=0.5, r=0.05, sigma=0.25, option_type="call")
        gp = black_scholes_greeks(S=100, K=100, T=0.5, r=0.05, sigma=0.25, option_type="put")
        self.assertIsNotNone(gc)
        self.assertIsNotNone(gp)
        assert gc is not None and gp is not None
        self.assertAlmostEqual(gc.delta - gp.delta, 1.0, places=5)

    def test_gamma_same_for_call_and_put(self) -> None:
        """Gamma is the same for calls and puts at the same strike."""
        gc = black_scholes_greeks(S=100, K=110, T=0.25, r=0.03, sigma=0.30, option_type="call")
        gp = black_scholes_greeks(S=100, K=110, T=0.25, r=0.03, sigma=0.30, option_type="put")
        self.assertIsNotNone(gc)
        self.assertIsNotNone(gp)
        assert gc is not None and gp is not None
        self.assertAlmostEqual(gc.gamma, gp.gamma, places=8)

    def test_vega_same_for_call_and_put(self) -> None:
        gc = black_scholes_greeks(S=100, K=105, T=0.5, r=0.04, sigma=0.20, option_type="call")
        gp = black_scholes_greeks(S=100, K=105, T=0.5, r=0.04, sigma=0.20, option_type="put")
        self.assertIsNotNone(gc)
        self.assertIsNotNone(gp)
        assert gc is not None and gp is not None
        self.assertAlmostEqual(gc.vega, gp.vega, places=8)

    def test_theta_is_negative_for_call(self) -> None:
        g = black_scholes_greeks(S=100, K=100, T=0.5, r=0.05, sigma=0.25, option_type="call")
        self.assertIsNotNone(g)
        assert g is not None
        self.assertLess(g.theta, 0)

    def test_deep_itm_call_delta_near_one(self) -> None:
        g = black_scholes_greeks(S=200, K=100, T=0.5, r=0.05, sigma=0.20, option_type="call")
        self.assertIsNotNone(g)
        assert g is not None
        self.assertAlmostEqual(g.delta, 1.0, delta=0.01)

    def test_deep_otm_call_delta_near_zero(self) -> None:
        g = black_scholes_greeks(S=50, K=100, T=0.1, r=0.05, sigma=0.20, option_type="call")
        self.assertIsNotNone(g)
        assert g is not None
        self.assertAlmostEqual(g.delta, 0.0, delta=0.01)

    def test_invalid_inputs_return_none(self) -> None:
        self.assertIsNone(black_scholes_greeks(S=100, K=100, T=0, r=0.05, sigma=0.2, option_type="call"))
        self.assertIsNone(black_scholes_greeks(S=100, K=100, T=0.5, r=0.05, sigma=0, option_type="call"))
        self.assertIsNone(black_scholes_greeks(S=0, K=100, T=0.5, r=0.05, sigma=0.2, option_type="call"))
        self.assertIsNone(black_scholes_greeks(S=100, K=0, T=0.5, r=0.05, sigma=0.2, option_type="call"))

    def test_known_values(self) -> None:
        """Cross-check against pre-computed Black-Scholes values.

        S=100, K=100, T=1, r=0.05, sigma=0.20
        Expected (from standard BS tables):
          call delta ≈ 0.6368
          gamma ≈ 0.01876
          call theta ≈ -0.01746/day
          vega ≈ 0.3752 (per 1%)
        """
        g = black_scholes_greeks(S=100, K=100, T=1.0, r=0.05, sigma=0.20, option_type="call")
        self.assertIsNotNone(g)
        assert g is not None
        self.assertAlmostEqual(g.delta, 0.6368, delta=0.005)
        self.assertAlmostEqual(g.gamma, 0.01876, delta=0.001)
        self.assertAlmostEqual(g.vega, 0.3752, delta=0.005)
        # theta is per calendar day
        self.assertAlmostEqual(g.theta, -0.01746, delta=0.002)


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


class YFinanceProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeOptionsFetcher()
        self.provider = YFinanceProvider(fetcher=self.fake)

    def test_get_expirations(self) -> None:
        result = self.provider.get_expirations("aapl")
        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.expirations, (EXPIRATION, EXPIRATION_2, EXPIRATION_3))

    def test_get_chain_parses_all_contracts(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )
        self.assertEqual(chain.ticker, "AAPL")
        self.assertEqual(chain.expiration, EXPIRATION)
        self.assertEqual(len(chain.calls), 5)
        self.assertEqual(len(chain.puts), 5)
        self.assertEqual(chain.underlying_price, 150.0)

    def test_contract_fields_parsed(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )
        c = chain.calls[0]
        self.assertEqual(c.contract_symbol, _contract_symbol(130.0, "C"))
        self.assertEqual(c.option_type, "call")
        self.assertEqual(c.strike, 130.0)
        self.assertEqual(c.last_price, 22.50)
        self.assertEqual(c.bid, 22.00)
        self.assertEqual(c.ask, 23.00)
        self.assertAlmostEqual(c.mid, 22.50)
        self.assertEqual(c.volume, 800)
        self.assertEqual(c.open_interest, 3000)
        self.assertAlmostEqual(c.implied_volatility, 0.32)
        self.assertTrue(c.in_the_money)

    def test_default_expiration_uses_nearest(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", compute_greeks=False)
        )
        self.assertEqual(chain.expiration, EXPIRATION)

    def test_greeks_computed_when_enabled(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=True)
        )
        # All contracts should have Greeks (they all have IV)
        for c in chain.calls:
            self.assertIsNotNone(c.greeks, f"Greeks missing for call {c.strike}")
        for p in chain.puts:
            self.assertIsNotNone(p.greeks, f"Greeks missing for put {p.strike}")

    def test_greeks_skipped_when_disabled(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )
        for c in chain.calls:
            self.assertIsNone(c.greeks)
        for p in chain.puts:
            self.assertIsNone(p.greeks)

    def test_greeks_none_when_iv_missing(self) -> None:
        self.fake.calls = [{"strike": 150.0, "contractSymbol": "X", "impliedVolatility": None}]
        self.fake.puts = []
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=True)
        )
        self.assertEqual(len(chain.calls), 1)
        self.assertIsNone(chain.calls[0].greeks)

    def test_greeks_none_when_underlying_missing(self) -> None:
        self.fake.underlying_price = None
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=True)
        )
        for c in chain.calls:
            self.assertIsNone(c.greeks)

    def test_call_delta_positive_put_delta_negative(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION)
        )
        for c in chain.calls:
            if c.greeks:
                self.assertGreater(c.greeks.delta, 0, f"Call delta should be positive at {c.strike}")
        for p in chain.puts:
            if p.greeks:
                self.assertLess(p.greeks.delta, 0, f"Put delta should be negative at {p.strike}")

    def test_ticker_normalised_to_uppercase(self) -> None:
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="aapl", expiration=EXPIRATION, compute_greeks=False)
        )
        self.assertEqual(chain.ticker, "AAPL")

    def test_no_expirations_raises(self) -> None:
        self.fake.expirations = ()
        with self.assertRaises(Exception):
            self.provider.get_chain(OptionsChainQuery(ticker="AAPL"))

    def test_nan_values_treated_as_none(self) -> None:
        """NaN values from yfinance DataFrames should become None."""
        self.fake.calls = [
            {
                "contractSymbol": "X",
                "strike": 150.0,
                "volume": float("nan"),
                "openInterest": float("nan"),
                "impliedVolatility": float("nan"),
                "bid": float("nan"),
                "ask": float("nan"),
                "lastPrice": float("nan"),
            }
        ]
        self.fake.puts = []
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )
        c = chain.calls[0]
        self.assertIsNone(c.volume)
        self.assertIsNone(c.open_interest)
        self.assertIsNone(c.implied_volatility)
        self.assertIsNone(c.bid)
        self.assertIsNone(c.ask)
        self.assertIsNone(c.mid)

    def test_missing_strike_skips_row(self) -> None:
        self.fake.calls = [{"contractSymbol": "X"}, {"contractSymbol": "Y", "strike": 150.0}]
        self.fake.puts = []
        chain = self.provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )
        self.assertEqual(len(chain.calls), 1)
        self.assertEqual(chain.calls[0].strike, 150.0)

    def test_describe(self) -> None:
        meta = self.provider.describe()
        self.assertEqual(meta.provider_id, "yfinance")
        self.assertEqual(meta.display_name, "Yahoo Finance Options")
        self.assertIn("options_chain", meta.capabilities)
        self.assertIn("greeks", meta.capabilities)


# ---------------------------------------------------------------------------
# OptionsChain helper tests
# ---------------------------------------------------------------------------


class OptionsChainHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        fake = FakeOptionsFetcher()
        provider = YFinanceProvider(fetcher=fake)
        self.chain = provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )

    def test_atm_strike(self) -> None:
        # Underlying is 150.0, so ATM strike should be 150.0
        self.assertEqual(self.chain.atm_strike, 150.0)

    def test_atm_call(self) -> None:
        c = self.chain.atm_call
        self.assertIsNotNone(c)
        assert c is not None
        self.assertEqual(c.strike, 150.0)
        self.assertEqual(c.option_type, "call")

    def test_atm_put(self) -> None:
        p = self.chain.atm_put
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.strike, 150.0)
        self.assertEqual(p.option_type, "put")

    def test_atm_iv(self) -> None:
        iv = self.chain.atm_iv
        self.assertIsNotNone(iv)
        assert iv is not None
        # ATM call IV = 0.25, ATM put IV = 0.26, average = 0.255
        self.assertAlmostEqual(iv, 0.255)

    def test_implied_move(self) -> None:
        move = self.chain.implied_move()
        self.assertIsNotNone(move)
        assert move is not None
        # ATM call mid = 5.20, ATM put mid = 3.40 → straddle = 8.60
        # move = 8.60 / 150.0 ≈ 0.0573
        self.assertAlmostEqual(move, 8.6 / 150.0, delta=0.001)

    def test_put_call_volume_ratio(self) -> None:
        ratio = self.chain.put_call_volume_ratio
        self.assertIsNotNone(ratio)
        assert ratio is not None
        call_vol = 800 + 1500 + 3000 + 2000 + 500  # 7800
        put_vol = 400 + 1200 + 2500 + 1800 + 300  # 6200
        self.assertAlmostEqual(ratio, put_vol / call_vol, places=4)

    def test_put_call_oi_ratio(self) -> None:
        ratio = self.chain.put_call_oi_ratio
        self.assertIsNotNone(ratio)
        assert ratio is not None
        call_oi = 3000 + 5000 + 8000 + 6000 + 2000  # 24000
        put_oi = 1500 + 4000 + 7000 + 5500 + 1000  # 19000
        self.assertAlmostEqual(ratio, put_oi / call_oi, places=4)

    def test_total_volume(self) -> None:
        self.assertEqual(self.chain.total_volume, 7800 + 6200)

    def test_total_open_interest(self) -> None:
        self.assertEqual(self.chain.total_open_interest, 24000 + 19000)

    def test_max_pain(self) -> None:
        mp = self.chain.max_pain()
        self.assertIsNotNone(mp)
        assert mp is not None
        # Max pain should be one of the strikes
        all_strikes = {c.strike for c in self.chain.calls} | {p.strike for p in self.chain.puts}
        self.assertIn(mp, all_strikes)

    def test_atm_strike_none_when_no_underlying(self) -> None:
        chain = OptionsChain(
            ticker="X", expiration=EXPIRATION, underlying_price=None, calls=(), puts=()
        )
        self.assertIsNone(chain.atm_strike)

    def test_put_call_volume_ratio_none_when_zero_call_volume(self) -> None:
        chain = OptionsChain(
            ticker="X",
            expiration=EXPIRATION,
            underlying_price=100.0,
            calls=(
                OptionContract(
                    contract_symbol="X", option_type="call", expiration=EXPIRATION,
                    strike=100.0, volume=0,
                ),
            ),
            puts=(
                OptionContract(
                    contract_symbol="Y", option_type="put", expiration=EXPIRATION,
                    strike=100.0, volume=500,
                ),
            ),
        )
        self.assertIsNone(chain.put_call_volume_ratio)

    def test_max_pain_none_when_empty(self) -> None:
        chain = OptionsChain(
            ticker="X", expiration=EXPIRATION, underlying_price=100.0, calls=(), puts=()
        )
        self.assertIsNone(chain.max_pain())

    def test_implied_move_none_when_missing_data(self) -> None:
        chain = OptionsChain(
            ticker="X", expiration=EXPIRATION, underlying_price=None, calls=(), puts=()
        )
        self.assertIsNone(chain.implied_move())


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class EdgeCaseTests(unittest.TestCase):
    def test_empty_chain(self) -> None:
        fake = FakeOptionsFetcher(calls=[], puts=[])
        provider = YFinanceProvider(fetcher=fake)
        chain = provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, compute_greeks=False)
        )
        self.assertEqual(len(chain.calls), 0)
        self.assertEqual(len(chain.puts), 0)
        self.assertEqual(chain.total_volume, 0)
        self.assertEqual(chain.total_open_interest, 0)

    def test_mid_price_computed(self) -> None:
        fake = FakeOptionsFetcher(
            calls=[{"strike": 100.0, "contractSymbol": "X", "bid": 2.0, "ask": 3.0}],
            puts=[],
        )
        provider = YFinanceProvider(fetcher=fake)
        chain = provider.get_chain(
            OptionsChainQuery(ticker="TEST", expiration=EXPIRATION, compute_greeks=False)
        )
        self.assertAlmostEqual(chain.calls[0].mid, 2.5)

    def test_mid_none_when_bid_or_ask_missing(self) -> None:
        fake = FakeOptionsFetcher(
            calls=[{"strike": 100.0, "contractSymbol": "X", "bid": 2.0}],
            puts=[],
        )
        provider = YFinanceProvider(fetcher=fake)
        chain = provider.get_chain(
            OptionsChainQuery(ticker="TEST", expiration=EXPIRATION, compute_greeks=False)
        )
        self.assertIsNone(chain.calls[0].mid)

    def test_risk_free_rate_passed_to_greeks(self) -> None:
        """Changing risk-free rate should change Greeks."""
        fake = FakeOptionsFetcher()
        provider = YFinanceProvider(fetcher=fake)

        chain1 = provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, risk_free_rate=0.01)
        )
        chain2 = provider.get_chain(
            OptionsChainQuery(ticker="AAPL", expiration=EXPIRATION, risk_free_rate=0.10)
        )
        # ATM call delta should differ between the two rates
        g1 = chain1.calls[2].greeks  # 150 strike
        g2 = chain2.calls[2].greeks
        self.assertIsNotNone(g1)
        self.assertIsNotNone(g2)
        assert g1 is not None and g2 is not None
        self.assertNotAlmostEqual(g1.delta, g2.delta, places=3)


# ---------------------------------------------------------------------------
# _DirectYahooOptionsFetcher — crumb handshake + v7 parsing (no network)
# ---------------------------------------------------------------------------

from digital_oracle.providers.base import ProviderError
from digital_oracle.providers.yfinance_provider import _DirectYahooOptionsFetcher


def _fake_v7_payload(exp_ts: int = 1785110400, price: float = 150.0) -> dict[str, Any]:
    """A minimal but realistic v7 options response."""
    return {
        "optionChain": {
            "result": [
                {
                    "expirationDates": [exp_ts],
                    "quote": {"regularMarketPrice": price},
                    "options": [
                        {
                            "calls": [
                                {
                                    "contractSymbol": "AAPL260727C00150000",
                                    "strike": 150.0,
                                    "lastPrice": 5.2,
                                    "bid": 5.0,
                                    "ask": 5.4,
                                    "volume": 3000,
                                    "openInterest": 8000,
                                    "impliedVolatility": 0.25,
                                    "inTheMoney": False,
                                }
                            ],
                            "puts": [
                                {
                                    "contractSymbol": "AAPL260727P00150000",
                                    "strike": 150.0,
                                    "lastPrice": 3.4,
                                    "bid": 3.2,
                                    "ask": 3.6,
                                    "volume": 2500,
                                    "openInterest": 7000,
                                    "impliedVolatility": 0.26,
                                    "inTheMoney": False,
                                }
                            ],
                        }
                    ],
                }
            ],
            "error": None,
        }
    }


class _FakeHttp:
    """Stand-in for opener.open — returns canned bytes per URL pattern.

    The crumb endpoint returns the crumb string; any v7 options URL returns
    the provided JSON payload; fc.yahoo.com returns empty (404-like).
    """

    def __init__(self, payload: dict[str, Any], crumb: str = "CRUMB123") -> None:
        self.payload = payload
        self.crumb = crumb
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float | None = None) -> "_FakeResp":
        self.calls.append(url)
        if "getcrumb" in url:
            return _FakeResp(self.crumb.encode("utf-8"))
        if "fc.yahoo.com" in url:
            return _FakeResp(b"")
        # v7 options endpoint
        import json as _json

        return _FakeResp(_json.dumps(self.payload).encode("utf-8"))


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *args: object) -> None:
        pass


class DirectYahooOptionsFetcherTests(unittest.TestCase):
    def test_crumb_initialised_once_and_reused(self) -> None:
        fetcher = _DirectYahooOptionsFetcher()
        fake = _FakeHttp(_fake_v7_payload(), crumb="UNIQUE_CRUMB")
        with patch.object(fetcher._opener, "open", side_effect=fake):
            fetcher.fetch_expirations("AAPL")
            fetcher.fetch_underlying_price("AAPL")

        # crumb endpoint hit exactly once across both calls
        crumb_calls = [u for u in fake.calls if "getcrumb" in u]
        self.assertEqual(len(crumb_calls), 1)
        self.assertEqual(fetcher._crumb, "UNIQUE_CRUMB")

    def test_fetch_expirations_converts_unix_ts_to_date(self) -> None:
        fetcher = _DirectYahooOptionsFetcher()
        # 1785110400 = 2026-07-27 UTC
        with patch.object(
            fetcher._opener, "open", side_effect=_FakeHttp(_fake_v7_payload(1785110400))
        ):
            exps = fetcher.fetch_expirations("AAPL")
        self.assertEqual(exps, ("2026-07-27",))

    def test_fetch_chain_parses_contracts(self) -> None:
        fetcher = _DirectYahooOptionsFetcher()
        with patch.object(
            fetcher._opener, "open", side_effect=_FakeHttp(_fake_v7_payload(1785110400))
        ):
            rows = fetcher.fetch_chain("AAPL", "2026-07-27")
        self.assertEqual(len(rows.calls), 1)
        self.assertEqual(len(rows.puts), 1)
        self.assertAlmostEqual(rows.calls[0]["impliedVolatility"], 0.25)
        self.assertEqual(rows.calls[0]["contractSymbol"], "AAPL260727C00150000")

    def test_fetch_underlying_price(self) -> None:
        fetcher = _DirectYahooOptionsFetcher()
        with patch.object(
            fetcher._opener, "open", side_effect=_FakeHttp(_fake_v7_payload(price=333.02))
        ):
            price = fetcher.fetch_underlying_price("AAPL")
        self.assertAlmostEqual(price, 333.02)

    def test_no_result_raises_provider_error(self) -> None:
        fetcher = _DirectYahooOptionsFetcher()
        bad = {"optionChain": {"result": [], "error": {"description": "Invalid ticker"}}}
        with patch.object(fetcher._opener, "open", side_effect=_FakeHttp(bad)):
            with self.assertRaises(ProviderError):
                fetcher.fetch_expirations("BOGUS")

    def test_chain_with_unlisted_expiration_raises(self) -> None:
        fetcher = _DirectYahooOptionsFetcher()
        with patch.object(
            fetcher._opener, "open", side_effect=_FakeHttp(_fake_v7_payload(1785110400))
        ):
            with self.assertRaises(ProviderError):
                fetcher.fetch_chain("AAPL", "2099-01-01")

    def test_uses_short_user_agent(self) -> None:
        """Full-browser UAs get 429'd by Yahoo; the fetcher must use a minimal UA."""
        fetcher = _DirectYahooOptionsFetcher()
        ua_headers = [v for (k, v) in fetcher._opener.addheaders if k == "User-Agent"]
        self.assertEqual(ua_headers, ["Mozilla/5.0"])


if __name__ == "__main__":
    unittest.main()
