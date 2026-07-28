from __future__ import annotations

import sys
import unittest
from typing import Any, Mapping

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from digital_oracle.providers.edgar import (
    EDGAR_SEARCH_URL,
    EDGAR_SUBMISSIONS_URL,
    EDGAR_TICKERS_URL,
    EdgarInsiderQuery,
    EdgarProvider,
    EdgarSearchQuery,
)
from digital_oracle.providers.base import ProviderError


SAMPLE_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
}

SAMPLE_SUBMISSIONS = {
    "cik": "0000320193",
    "entityType": "operating",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001140361-26-004321",
                "0000320193-26-000012",
                "0001140361-26-004100",
                "0000320193-26-000011",
                "0001140361-26-003999",
            ],
            "filingDate": [
                "2026-03-05",
                "2026-02-28",
                "2026-02-15",
                "2026-02-01",
                "2026-01-20",
            ],
            "reportDate": [
                "2026-03-03",
                "2025-12-28",
                "2026-02-13",
                "2025-12-28",
                "2026-01-18",
            ],
            "form": ["4", "10-Q", "4", "8-K", "4"],
            "primaryDocument": [
                "xslF345X05/wf-form4_abc.xml",
                "aapl-20251228.htm",
                "xslF345X05/wf-form4_def.xml",
                "aapl-20251228-8k.htm",
                "xslF345X05/wf-form4_ghi.xml",
            ],
            "primaryDocDescription": [
                "4 - APPLE INC (Tim Cook)",
                "10-Q",
                "4 - APPLE INC (Luca Maestri)",
                "8-K",
                "4 - APPLE INC (Jeff Williams)",
            ],
        },
        "files": [],
    },
}

SAMPLE_SEARCH_RESPONSE = {
    "hits": {
        "total": {"value": 42, "relation": "eq"},
        "hits": [
            {
                "_source": {
                    "entity_name": "Apple Inc.",
                    "file_date": "2026-03-05",
                    "form_type": "4",
                    "file_num": "001-36743",
                    "display_names": ["COOK TIMOTHY D"],
                }
            },
            {
                "_source": {
                    "entity_name": "Microsoft Corp",
                    "file_date": "2026-03-01",
                    "form_type": "4",
                    "file_num": "001-37845",
                    "display_names": ["NADELLA SATYA"],
                }
            },
            {
                "_source": {
                    "entity_name": "Tesla, Inc.",
                    "file_date": "2026-02-28",
                    "form_type": "4",
                    "file_num": "001-34756",
                    "display_names": [],
                }
            },
        ],
    }
}


class FakeJsonClient:
    def __init__(
        self,
        *,
        tickers_payload: dict[str, Any] = SAMPLE_TICKERS,
        submissions_payload: dict[str, Any] = SAMPLE_SUBMISSIONS,
        search_payload: dict[str, Any] = SAMPLE_SEARCH_RESPONSE,
    ):
        self.tickers_payload = tickers_payload
        self.submissions_payload = submissions_payload
        self.search_payload = search_payload
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    def get_json(self, url: str, *, params: Mapping[str, object] | None = None) -> Any:
        self.calls.append((url, params))
        if url == EDGAR_TICKERS_URL:
            return self.tickers_payload
        if url.startswith(EDGAR_SUBMISSIONS_URL):
            return self.submissions_payload
        if url.startswith(EDGAR_SEARCH_URL) or url == EDGAR_SEARCH_URL:
            return self.search_payload
        raise AssertionError(f"unexpected url: {url}")


class EdgarProviderCIKResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = FakeJsonClient()
        self.provider = EdgarProvider(http_client=self.fake_client)

    def test_resolve_cik_pads_to_ten_digits(self) -> None:
        cik, name = self.provider._resolve_cik("AAPL")
        self.assertEqual(cik, "0000320193")
        self.assertEqual(name, "Apple Inc.")

    def test_resolve_cik_case_insensitive(self) -> None:
        cik, name = self.provider._resolve_cik("aapl")
        self.assertEqual(cik, "0000320193")
        self.assertEqual(name, "Apple Inc.")

    def test_resolve_cik_caches_ticker_map(self) -> None:
        self.provider._resolve_cik("AAPL")
        self.provider._resolve_cik("MSFT")
        # Should only fetch tickers once
        ticker_calls = [url for url, _ in self.fake_client.calls if url == EDGAR_TICKERS_URL]
        self.assertEqual(len(ticker_calls), 1)

    def test_resolve_cik_raises_for_unknown_ticker(self) -> None:
        with self.assertRaises(ProviderError) as ctx:
            self.provider._resolve_cik("ZZZZZ")
        self.assertIn("ticker not found", str(ctx.exception))


class EdgarInsiderTransactionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = FakeJsonClient()
        self.provider = EdgarProvider(http_client=self.fake_client)

    def test_get_insider_transactions_filters_form4s(self) -> None:
        summary = self.provider.get_insider_transactions(
            EdgarInsiderQuery(ticker="AAPL")
        )
        self.assertEqual(summary.ticker, "AAPL")
        self.assertEqual(summary.company_name, "Apple Inc.")
        self.assertEqual(summary.cik, "0000320193")
        self.assertEqual(summary.total_form4_count, 3)
        self.assertEqual(len(summary.recent_form4s), 3)

    def test_form4_filing_fields_are_parsed_correctly(self) -> None:
        summary = self.provider.get_insider_transactions(
            EdgarInsiderQuery(ticker="AAPL")
        )
        first = summary.recent_form4s[0]
        self.assertEqual(first.accession_number, "0001140361-26-004321")
        self.assertEqual(first.form_type, "4")
        self.assertEqual(first.filing_date, "2026-03-05")
        self.assertEqual(first.report_date, "2026-03-03")
        self.assertEqual(first.primary_document, "xslF345X05/wf-form4_abc.xml")
        self.assertEqual(first.description, "4 - APPLE INC (Tim Cook)")

    def test_limit_restricts_number_of_filings_returned(self) -> None:
        summary = self.provider.get_insider_transactions(
            EdgarInsiderQuery(ticker="AAPL", limit=2)
        )
        self.assertEqual(len(summary.recent_form4s), 2)
        # total_form4_count should still reflect all Form 4s
        self.assertEqual(summary.total_form4_count, 3)

    def test_non_form4_filings_are_excluded(self) -> None:
        summary = self.provider.get_insider_transactions(
            EdgarInsiderQuery(ticker="AAPL")
        )
        for filing in summary.recent_form4s:
            self.assertEqual(filing.form_type, "4")

    def test_submissions_url_uses_padded_cik(self) -> None:
        self.provider.get_insider_transactions(
            EdgarInsiderQuery(ticker="AAPL")
        )
        submissions_calls = [
            url for url, _ in self.fake_client.calls
            if url.startswith(EDGAR_SUBMISSIONS_URL) and url != EDGAR_TICKERS_URL
        ]
        self.assertEqual(len(submissions_calls), 1)
        self.assertEqual(
            submissions_calls[0],
            f"{EDGAR_SUBMISSIONS_URL}/CIK0000320193.json",
        )

    def test_ticker_normalization_uppercase(self) -> None:
        summary = self.provider.get_insider_transactions(
            EdgarInsiderQuery(ticker="aapl")
        )
        self.assertEqual(summary.ticker, "AAPL")


class EdgarSearchFilingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_client = FakeJsonClient()
        self.provider = EdgarProvider(http_client=self.fake_client)

    def test_search_filings_returns_hits(self) -> None:
        results = self.provider.search_filings(
            EdgarSearchQuery(query="insider purchase", forms="4")
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].entity_name, "Apple Inc.")
        self.assertEqual(results[0].file_date, "2026-03-05")
        self.assertEqual(results[0].form_type, "4")
        self.assertEqual(results[0].file_number, "001-36743")

    def test_search_filings_passes_params(self) -> None:
        self.provider.search_filings(
            EdgarSearchQuery(
                query="quarterly report",
                forms="10-Q",
                date_start="2025-01-01",
                date_end="2026-03-11",
            )
        )
        search_calls = [
            (url, params) for url, params in self.fake_client.calls
            if url == EDGAR_SEARCH_URL
        ]
        self.assertEqual(len(search_calls), 1)
        _, params = search_calls[0]
        self.assertIsNotNone(params)
        assert params is not None
        self.assertEqual(params["q"], "quarterly report")
        self.assertEqual(params["forms"], "10-Q")
        self.assertEqual(params["dateRange"], "custom")
        self.assertEqual(params["startdt"], "2025-01-01")
        self.assertEqual(params["enddt"], "2026-03-11")

    def test_search_filings_limit_restricts_results(self) -> None:
        results = self.provider.search_filings(
            EdgarSearchQuery(query="test", limit=2)
        )
        self.assertEqual(len(results), 2)

    def test_search_filings_empty_display_names_handled(self) -> None:
        results = self.provider.search_filings(
            EdgarSearchQuery(query="test", limit=10)
        )
        # Third hit has empty display_names list
        self.assertEqual(results[2].entity_name, "Tesla, Inc.")
        self.assertEqual(results[2].description, "")

    def test_search_filings_no_date_range_omits_params(self) -> None:
        self.provider.search_filings(
            EdgarSearchQuery(query="test")
        )
        search_calls = [
            (url, params) for url, params in self.fake_client.calls
            if url == EDGAR_SEARCH_URL
        ]
        _, params = search_calls[0]
        assert params is not None
        self.assertNotIn("dateRange", params)
        self.assertNotIn("startdt", params)
        self.assertNotIn("enddt", params)


class EdgarProviderMetadataTests(unittest.TestCase):
    def test_provider_metadata(self) -> None:
        provider = EdgarProvider(http_client=FakeJsonClient())
        meta = provider.describe()
        self.assertEqual(meta.provider_id, "sec_edgar")
        self.assertEqual(meta.display_name, "SEC EDGAR")
        self.assertIn("insider_transactions", meta.capabilities)
        self.assertIn("filings_search", meta.capabilities)


# ---------------------------------------------------------------------------
# Form 4 body parsing + get_insider_transactions_detail
# ---------------------------------------------------------------------------

from digital_oracle.providers.edgar import (
    EDGAR_ARCHIVES_URL,
    EdgarInsiderTransaction,
    FORM4_TRANSACTION_CODES,
    parse_form4_xml,
)

# A realistic Form 4 XML body (shape matches the live SEC format).
# Tench Coxe gift of 500,000 NVDA shares — mirrors a real filing.
SAMPLE_FORM4_XML_SALE = """\
<ownershipDocument xmlns="http://www.sec.gov/edgar/document/ownership/form4">
  <issuer>
    <issuerCik>0001045810</issuerCik>
    <issuerName>NVIDIA CORP</issuerName>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>0001197647</rptOwnerCik>
      <rptOwnerName>COXE TENCH</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
      <officerTitle></officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate>
        <value>2026-07-01</value>
      </transactionDate>
      <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmount>
        <transactionShares>
          <value>500000</value>
        </transactionShares>
      </transactionAmount>
      <transactionPricePerShare>
        <value>120.50</value>
      </transactionPricePerShare>
      <postTransactionTransactionOwnership>
        <sharesOwnedFollowingTransaction>
          <value>25171360</value>
        </sharesOwnedFollowingTransaction>
      </postTransactionTransactionOwnership>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""

SAMPLE_FORM4_XML_PURCHASE = """\
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerName>DOE JANE</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-15</value></transactionDate>
      <transactionCoding>
        <transactionFormType>4</transactionFormType>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmount>
        <transactionShares><value>10000</value></transactionShares>
      </transactionAmount>
      <transactionPricePerShare><value>45.00</value></transactionPricePerShare>
      <postTransactionTransactionOwnership>
        <sharesOwnedFollowingTransaction><value>50000</value></sharesOwnedFollowingTransaction>
      </postTransactionTransactionOwnership>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


class ParseForm4XmlTests(unittest.TestCase):
    def test_parses_sale_transaction(self) -> None:
        tx = parse_form4_xml(
            SAMPLE_FORM4_XML_SALE,
            accession_number="0001197647-26-000005",
            filing_url="https://example.com/form4.xml",
        )
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.reporting_owner, "COXE TENCH")
        self.assertEqual(tx.transaction_code, "S")
        self.assertEqual(tx.transaction_label, "Open-market sale")
        self.assertTrue(tx.is_sale)
        self.assertFalse(tx.is_purchase)
        self.assertEqual(tx.transaction_date, "2026-07-01")
        self.assertAlmostEqual(tx.shares, 500000)
        self.assertAlmostEqual(tx.price_per_share, 120.50)
        self.assertAlmostEqual(tx.shares_owned_after, 25171360)

    def test_parses_purchase_transaction(self) -> None:
        tx = parse_form4_xml(SAMPLE_FORM4_XML_PURCHASE, "acc-1", "url")
        self.assertIsNotNone(tx)
        assert tx is not None
        self.assertEqual(tx.reporting_owner, "DOE JANE")
        self.assertEqual(tx.owner_title, "Chief Financial Officer")
        self.assertEqual(tx.transaction_code, "P")
        self.assertTrue(tx.is_purchase)
        self.assertFalse(tx.is_sale)
        self.assertEqual(tx.transaction_label, "Open-market purchase")

    def test_no_reporting_owner_returns_none(self) -> None:
        body = "<ownershipDocument><issuer><issuerName>X</issuerName></issuer></ownershipDocument>"
        self.assertIsNone(parse_form4_xml(body, "acc", "url"))

    def test_invalid_xml_raises(self) -> None:
        from digital_oracle.providers.base import ProviderParseError

        with self.assertRaises(ProviderParseError):
            parse_form4_xml("not xml at all <", "acc", "url")

    def test_all_standard_transaction_codes_mapped(self) -> None:
        # Every code used in real filings should have a human-readable label.
        for code in ("P", "S", "A", "D", "F", "G", "V", "J"):
            self.assertIn(code, FORM4_TRANSACTION_CODES)


class _DetailFakeClient:
    """Fake client that serves JSON for submissions + text for Form 4 bodies.

    Routes Form 4 body fetches by accession number so each filing can have a
    distinct (or failing) body.
    """

    def __init__(
        self,
        *,
        tickers_payload: dict[str, Any] = SAMPLE_TICKERS,
        submissions_payload: dict[str, Any] = SAMPLE_SUBMISSIONS,
        form4_bodies: Mapping[str, str] | None = None,
    ) -> None:
        self.tickers_payload = tickers_payload
        self.submissions_payload = submissions_payload
        self.form4_bodies = form4_bodies or {}
        self.json_calls: list[str] = []
        self.text_calls: list[str] = []

    def get_json(self, url: str, *, params: Mapping[str, object] | None = None) -> Any:
        self.json_calls.append(url)
        if url == EDGAR_TICKERS_URL:
            return self.tickers_payload
        if url.startswith(EDGAR_SUBMISSIONS_URL):
            return self.submissions_payload
        raise AssertionError(f"unexpected get_json url: {url}")

    def get_text(self, url: str, *, params: Mapping[str, object] | None = None) -> str:
        self.text_calls.append(url)
        # Match by accession number (no dashes) embedded in the archive URL.
        for acc, body in self.form4_bodies.items():
            if acc.replace("-", "") in url:
                return body
        raise AssertionError(f"unexpected get_text url: {url}")


def _make_submissions_with_form4s(accessions: list[tuple[str, str]]) -> dict[str, Any]:
    """Build a submissions payload listing the given (accession, primary_doc) Form 4s."""
    return {
        "name": "Apple Inc.",
        "cik": "320193",
        "filings": {
            "recent": {
                "form": ["4"] * len(accessions),
                "accessionNumber": [a for a, _ in accessions],
                "filingDate": ["2026-07-0" + str(i + 1) for i in range(len(accessions))],
                "reportDate": ["2026-06-3" + str(i) for i in range(len(accessions))],
                "primaryDocument": [d for _, d in accessions],
                "primaryDocDescription": ["FORM 4"] * len(accessions),
            }
        },
    }


class GetInsiderTransactionsDetailTests(unittest.TestCase):
    def test_fetches_and_parses_each_form4_body(self) -> None:
        accessions = [
            ("0001197647-26-000005", "wk-form4_1.xml"),
            ("0001197647-26-000006", "wk-form4_2.xml"),
        ]
        submissions = _make_submissions_with_form4s(accessions)
        bodies = {
            "0001197647-26-000005": SAMPLE_FORM4_XML_SALE,
            "0001197647-26-000006": SAMPLE_FORM4_XML_PURCHASE,
        }
        fake = _DetailFakeClient(submissions_payload=submissions, form4_bodies=bodies)
        provider = EdgarProvider(http_client=fake)

        txs = provider.get_insider_transactions_detail(EdgarInsiderQuery(ticker="AAPL", limit=5))

        self.assertEqual(len(txs), 2)
        # Both bodies were fetched via get_text, hitting the archives URL.
        self.assertEqual(len(fake.text_calls), 2)
        self.assertTrue(all(EDGAR_ARCHIVES_URL in u for u in fake.text_calls))
        # Verify trade fields parsed.
        codes = {tx.transaction_code for tx in txs}
        self.assertEqual(codes, {"S", "P"})

    def test_strips_xsl_render_wrapper_in_primary_document(self) -> None:
        accessions = [("0001197647-26-000005", "xslF345X06/wk-form4_1.xml")]
        submissions = _make_submissions_with_form4s(accessions)
        bodies = {"0001197647-26-000005": SAMPLE_FORM4_XML_SALE}
        fake = _DetailFakeClient(submissions_payload=submissions, form4_bodies=bodies)
        provider = EdgarProvider(http_client=fake)

        provider.get_insider_transactions_detail(EdgarInsiderQuery(ticker="AAPL", limit=1))

        # The fetched URL must NOT contain the xslF345X06 wrapper.
        self.assertEqual(len(fake.text_calls), 1)
        self.assertNotIn("xslF345X06", fake.text_calls[0])
        self.assertIn("wk-form4_1.xml", fake.text_calls[0])

    def test_unfetchable_filing_skipped_not_aborted(self) -> None:
        accessions = [
            ("0001197647-26-000005", "wk-form4_1.xml"),
            ("0001197647-26-BROKEN", "wk-form4_broken.xml"),
            ("0001197647-26-000006", "wk-form4_2.xml"),
        ]
        submissions = _make_submissions_with_form4s(accessions)
        # Only provide bodies for 2 of 3; the middle one's get_text will raise.
        bodies = {
            "0001197647-26-000005": SAMPLE_FORM4_XML_SALE,
            "0001197647-26-000006": SAMPLE_FORM4_XML_PURCHASE,
        }

        class _PartialFake(_DetailFakeClient):
            def get_text(self, url: str, *, params: Mapping[str, object] | None = None) -> str:
                if "000119764726BROKEN" in url:
                    raise RuntimeError("network error")
                return super().get_text(url, params=params)

        fake = _PartialFake(submissions_payload=submissions, form4_bodies=bodies)
        provider = EdgarProvider(http_client=fake)

        txs = provider.get_insider_transactions_detail(EdgarInsiderQuery(ticker="AAPL", limit=5))

        # Middle filing failed but the other two parsed — partial-failure tolerant.
        self.assertEqual(len(txs), 2)

    def test_empty_form4_body_returns_nothing(self) -> None:
        accessions = [("0001197647-26-000005", "wk-form4_1.xml")]
        submissions = _make_submissions_with_form4s(accessions)
        # A Form 4 with no reporting owner -> parse returns None -> skipped.
        bodies = {"0001197647-26-000005": "<ownershipDocument><issuer><issuerName>X</issuerName></issuer></ownershipDocument>"}
        fake = _DetailFakeClient(submissions_payload=submissions, form4_bodies=bodies)
        provider = EdgarProvider(http_client=fake)

        txs = provider.get_insider_transactions_detail(EdgarInsiderQuery(ticker="AAPL", limit=5))
        self.assertEqual(txs, [])


# ---------------------------------------------------------------------------
# get_capital_trends — long-term capital allocation signal
# ---------------------------------------------------------------------------

from digital_oracle.providers.edgar import (
    CAPITAL_CONCEPTS,
    CapitalDataPoint,
    CompanyCapitalTrend,
    EDGAR_XBRL_CONCEPT_URL,
)


def _xbrl_payload(units: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build a minimal companyconcept JSON with the given units."""
    return {
        "entityName": "Test Corp",
        "cik": "0",
        "taxonomy": "us-gaap",
        "tag": "ResearchAndDevelopmentExpense",
        "units": units,
    }


# US filer: USD, 10-K, two fiscal years
_US_RD = _xbrl_payload({
    "USD": [
        {"form": "10-K", "fy": 2024, "end": "2024-12-31", "val": 10000},
        {"form": "10-K", "fy": 2025, "end": "2025-12-31", "val": 15000},
    ]
})

# Chinese ADR: both CNY and USD, 20-F
_CN_RD = _xbrl_payload({
    "CNY": [
        {"form": "20-F", "fy": 2025, "end": "2025-03-31", "val": 66533000000},
    ],
    "USD": [
        {"form": "20-F", "fy": 2025, "end": "2025-03-31", "val": 9645000000},
    ],
})

# European ADR: EUR only, 20-F
_EU_RD = _xbrl_payload({
    "EUR": [
        {"form": "20-F", "fy": 2024, "end": "2024-12-31", "val": 4300000000},
        {"form": "20-F", "fy": 2025, "end": "2025-12-31", "val": 4700000000},
    ]
})


class _CapitalFakeClient:
    """Serves ticker map + per-concept XBRL payloads keyed by CIK."""

    def __init__(self, xbrl: Mapping[str, dict[str, Any]]) -> None:
        self.xbrl = xbrl  # keyed by CIK (zero-padded)
        self.json_calls: list[str] = []

    def get_json(self, url: str, *, params: Mapping[str, object] | None = None) -> Any:
        self.json_calls.append(url)
        if url == EDGAR_TICKERS_URL:
            return SAMPLE_TICKERS
        if url.startswith(EDGAR_SUBMISSIONS_URL):
            return SAMPLE_SUBMISSIONS
        if EDGAR_XBRL_CONCEPT_URL in url:
            # /api/xbrl/companyconcept/CIK0001045810/us-gaap/<tag>.json
            for cik, payload in self.xbrl.items():
                if cik in url:
                    return payload
            raise AssertionError(f"unexpected XBRL url: {url}")
        raise AssertionError(f"unexpected url: {url}")


class GetCapitalTrendsTests(unittest.TestCase):
    def _provider_with(self, tickers_map: Mapping[str, tuple[str, str, dict]]) -> tuple[EdgarProvider, _CapitalFakeClient]:
        """Build a provider + fake client.

        *tickers_map*: ticker -> (cik_padded, company_name, xbrl_payload)
        Also registers the tickers in a custom SAMPLE_TICKERS so _resolve_cik works.
        """
        # custom ticker map
        custom_tickers: dict[str, Any] = {}
        xbrl_by_cik: dict[str, dict[str, Any]] = {}
        for i, (tk, (cik, name, payload)) in enumerate(tickers_map.items()):
            custom_tickers[str(i)] = {"cik_str": int(cik.lstrip("0") or 0), "ticker": tk, "title": name}
            xbrl_by_cik[cik] = payload
        fake = _CapitalFakeClient(xbrl_by_cik)
        fake.tickers_payload = custom_tickers  # type: ignore[attr-defined]

        # Patch the get_json to serve our custom tickers
        original_get_json = fake.get_json

        def patched(url: str, *, params: Mapping[str, object] | None = None) -> Any:
            if url == EDGAR_TICKERS_URL:
                return custom_tickers
            return original_get_json(url, params=params)

        fake.get_json = patched  # type: ignore[assignment]
        provider = EdgarProvider(http_client=fake)
        return provider, fake

    def test_parses_us_filer_and_computes_yoy(self) -> None:
        provider, _ = self._provider_with({
            "AAPL": ("0000320193", "Apple Inc.", _US_RD),
        })
        trends = provider.get_capital_trends(["AAPL"], concept="R&D")

        self.assertEqual(len(trends), 1)
        t = trends[0]
        self.assertEqual(t.ticker, "AAPL")
        self.assertEqual(t.currency, "USD")
        self.assertEqual(t.concept, "R&D")
        self.assertEqual(len(t.history), 2)
        self.assertEqual(t.history[0].fiscal_year, 2024)
        self.assertAlmostEqual(t.latest_value, 15000)
        self.assertEqual(t.latest_fiscal_year, 2025)
        # YoY: (15000-10000)/10000 = 50%
        self.assertAlmostEqual(t.yoy_growth_pct, 50.0)

    def test_prefers_usd_over_native_currency(self) -> None:
        provider, _ = self._provider_with({
            "BABA": ("0001577552", "Alibaba Group", _CN_RD),
        })
        trends = provider.get_capital_trends(["BABA"])
        t = trends[0]
        # CNY and USD both present -> USD chosen
        self.assertEqual(t.currency, "USD")
        self.assertAlmostEqual(t.latest_value, 9645000000)

    def test_falls_back_to_native_currency_when_no_usd(self) -> None:
        provider, _ = self._provider_with({
            "ASML": ("0000937966", "ASML Holding", _EU_RD),
        })
        trends = provider.get_capital_trends(["ASML"])
        t = trends[0]
        self.assertEqual(t.currency, "EUR")
        self.assertAlmostEqual(t.latest_value, 4700000000)

    def test_mixed_batch_all_returned(self) -> None:
        provider, _ = self._provider_with({
            "AAPL": ("0000320193", "Apple Inc.", _US_RD),
            "ASML": ("0000937966", "ASML Holding", _EU_RD),
        })
        trends = provider.get_capital_trends(["AAPL", "ASML"])
        self.assertEqual(len(trends), 2)
        currencies = {t.ticker: t.currency for t in trends}
        self.assertEqual(currencies, {"AAPL": "USD", "ASML": "EUR"})

    def test_unknown_ticker_skipped_not_aborted(self) -> None:
        provider, _ = self._provider_with({
            "AAPL": ("0000320193", "Apple Inc.", _US_RD),
        })
        # BOGUS not in ticker map -> skipped; AAPL still returned
        trends = provider.get_capital_trends(["BOGUS", "AAPL"])
        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0].ticker, "AAPL")

    def test_concept_not_reported_skipped(self) -> None:
        # Ticker resolves but XBRL concept 404 -> the fake raises -> skipped
        provider, fake = self._provider_with({
            "AAPL": ("0000320193", "Apple Inc.", _US_RD),
        })

        class _FailingFake(_CapitalFakeClient):
            def get_json(self, url: str, *, params: Mapping[str, object] | None = None) -> Any:
                self.json_calls.append(url)
                if url == EDGAR_TICKERS_URL:
                    return self.tickers_payload  # type: ignore[attr-defined]
                if EDGAR_XBRL_CONCEPT_URL in url:
                    raise RuntimeError("404 concept not found")
                raise AssertionError(f"unexpected url: {url}")

        provider = EdgarProvider(http_client=_FailingFake(fake.xbrl))
        # re-patch tickers
        provider.http_client.tickers_payload = fake.tickers_payload  # type: ignore[attr-defined]
        trends = provider.get_capital_trends(["AAPL"])
        self.assertEqual(trends, [])

    def test_invalid_concept_raises(self) -> None:
        provider, _ = self._provider_with({"AAPL": ("0000320193", "Apple Inc.", _US_RD)})
        with self.assertRaises(ValueError):
            provider.get_capital_trends(["AAPL"], concept="Bogus")

    def test_years_limits_history(self) -> None:
        big_payload = _xbrl_payload({
            "USD": [
                {"form": "10-K", "fy": fy, "end": f"{fy}-12-31", "val": fy * 1000}
                for fy in range(2020, 2026)
            ]
        })
        provider, _ = self._provider_with({"AAPL": ("0000320193", "Apple Inc.", big_payload)})
        trends = provider.get_capital_trends(["AAPL"], years=3)
        self.assertEqual(len(trends[0].history), 3)
        self.assertEqual(trends[0].history[0].fiscal_year, 2023)

    def test_dedupes_amended_filings_same_year(self) -> None:
        # Same FY reported twice (original 10-K + amendment), later period_end wins
        payload = _xbrl_payload({
            "USD": [
                {"form": "10-K", "fy": 2025, "end": "2025-12-31", "val": 10000},
                {"form": "10-K/A", "fy": 2025, "end": "2026-01-15", "val": 11000},
            ]
        })
        provider, _ = self._provider_with({"AAPL": ("0000320193", "Apple Inc.", payload)})
        # 10-K/A is not in accepted forms, so only the 10-K entry counts
        trends = provider.get_capital_trends(["AAPL"])
        self.assertEqual(len(trends[0].history), 1)
        self.assertAlmostEqual(trends[0].latest_value, 10000)


if __name__ == "__main__":
    unittest.main()
