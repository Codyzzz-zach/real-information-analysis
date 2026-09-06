from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import partial
from typing import Any, Mapping

from ..concurrent import gather
from ..http import JsonHttpClient, UrllibJsonClient

from ._coerce import _coerce_float
from .base import ProviderError, ProviderParseError, SignalProvider

from .._version import __version__ as _package_version

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
EDGAR_XBRL_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept"


@dataclass(frozen=True)
class EdgarInsiderQuery:
    ticker: str
    limit: int = 20


@dataclass(frozen=True)
class EdgarFiling:
    accession_number: str
    form_type: str
    filing_date: str
    report_date: str
    primary_document: str
    description: str


@dataclass(frozen=True)
class EdgarInsiderSummary:
    ticker: str
    company_name: str
    cik: str
    recent_form4s: tuple[EdgarFiling, ...]
    total_form4_count: int


# SEC Form 4 transaction codes (Transaction Coding - transactionCode element).
# See https://www.sec.gov/oiega/FinancialStatementAndOtherInformation.htm
FORM4_TRANSACTION_CODES: Mapping[str, str] = {
    "P": "Open-market purchase",        # 买
    "S": "Open-market sale",            # 卖
    "A": "Grant/award",                 # 授予
    "D": "Disposition to issuer",       # 回缴
    "F": "Tax withholding",             # 税收
    "G": "Gift",                        # 赠与
    "V": "Voluntary report",            # 自愿
    "J": "Other",                       # 其他
}


@dataclass(frozen=True)
class EdgarInsiderTransaction:
    """A single insider trade parsed from a Form 4 document body.

    Unlike :class:`EdgarFiling` (which only holds filing metadata), this
    captures the actual trade: who traded, buy/sell, how many shares, at
    what price, and the resulting holding.
    """

    accession_number: str
    reporting_owner: str          # e.g. "COXE TENCH"
    owner_title: str | None       # e.g. "Director", "Chief Financial Officer"
    transaction_date: str | None  # YYYY-MM-DD
    transaction_code: str         # "P"/"S"/"G"/... (see FORM4_TRANSACTION_CODES)
    transaction_label: str        # human-readable: "Open-market sale"
    is_purchase: bool             # True iff transaction_code == "P"
    is_sale: bool                 # True iff transaction_code == "S"
    shares: float | None          # shares traded (non-derivative)
    price_per_share: float | None # USD
    shares_owned_after: float | None  # holding after the trade
    filing_url: str               # the raw Form 4 XML URL


# ---------------------------------------------------------------------------
# Capital expenditure / R&D trends (long-term capital allocation signal)
# ---------------------------------------------------------------------------

# Maps a friendly concept name to XBRL tags per reporting regime.
# US domestic filers (10-K) report under the ``us-gaap`` taxonomy; foreign
# private issuers (20-F, IFRS) report under ``ifrs-full`` — TSM's
# companyfacts (CIK 1046179) contains zero us-gaap tags, so a us-gaap-only
# lookup silently returns nothing for them (verified live 2026-09-06).
# ifrs-full tags verified against TSM's live companyfacts:
#   ResearchAndDevelopmentExpense,
#   PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities,
#   PropertyPlantAndEquipment.
CAPITAL_CONCEPTS: Mapping[str, Mapping[str, str]] = {
    "R&D": {
        "us-gaap": "ResearchAndDevelopmentExpense",
        "ifrs-full": "ResearchAndDevelopmentExpense",
    },
    "CapEx": {
        "us-gaap": "PaymentsToAcquireProductiveAssets",
        "ifrs-full": "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    },
    "PP&E": {
        "us-gaap": "PropertyPlantAndEquipmentNet",
        "ifrs-full": "PropertyPlantAndEquipment",
    },
}


@dataclass(frozen=True)
class CapitalDataPoint:
    """One annual data point of a company's capital expenditure / R&D."""
    fiscal_year: int
    value: float              # in *currency* (USD preferred, else native)
    period_end: str           # YYYY-MM-DD


@dataclass(frozen=True)
class CompanyCapitalTrend:
    """A single company's multi-year capital allocation trend.

    Sourced from SEC EDGAR XBRL (10-K for US filers, 20-F for foreign filers).
    Currency is USD when reported (most filers), otherwise the native currency
    is kept and exposed via :attr:`currency`.
    """
    ticker: str
    company_name: str
    cik: str
    concept: str                  # "R&D" / "CapEx" / "PP&E"
    currency: str                 # "USD", "EUR", "CNY" ...
    history: tuple[CapitalDataPoint, ...]   # ascending by fiscal_year
    latest_value: float | None
    latest_fiscal_year: int | None
    yoy_growth_pct: float | None = None
    xbrl_namespace: str = ""      # taxonomy the data came from: us-gaap / ifrs-full
    yoy_growth_pct: float | None  # latest vs prior year, None if <2 data points

    def __len__(self) -> int:
        return len(self.history)


@dataclass(frozen=True)
class EdgarSearchQuery:
    query: str
    forms: str = ""
    date_start: str = ""
    date_end: str = ""
    limit: int = 10


@dataclass(frozen=True)
class EdgarSearchHit:
    entity_name: str
    file_date: str
    form_type: str
    file_number: str
    description: str


def _extract_description(source: Mapping[str, Any]) -> str:
    """Extract description from a search hit _source."""
    display_names = source.get("display_names")
    if isinstance(display_names, list):
        return str(display_names[0]) if display_names else ""
    if display_names is not None:
        return str(display_names)
    return ""


def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix from a tag."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text_of(parent: ET.Element | None, child_tag: str) -> str | None:
    """Return the stripped text of the first descendant ``child_tag`` of *parent*.

    Form 4 XML nests values in ``<value>`` children (e.g.
    ``<transactionShares><value>500000</value></transactionShares>``), so we
    also fall back to the first sub-element's text.
    """
    if parent is None:
        return None
    for elem in parent.iter():
        if _strip_ns(elem.tag) == child_tag:
            text = (elem.text or "").strip()
            if text:
                return text
            # Fall back to the first child's text (the <value> wrapper).
            for child in elem:
                child_text = (child.text or "").strip()
                if child_text:
                    return child_text
    return None


def parse_form4_xml(body: str, accession_number: str, filing_url: str) -> EdgarInsiderTransaction | None:
    """Parse a Form 4 XML document body into an :class:`EdgarInsiderTransaction`.

    Returns ``None`` if the document has no parseable non-derivative trade
    (e.g. filing-only amendments or derivative-only filings).
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ProviderParseError(f"failed to parse Form 4 XML: {exc}") from exc

    # reporting owner
    owner = None
    owner_title = None
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "rptOwnerName" and owner is None:
            owner = (elem.text or "").strip() or None
        elif tag == "officerTitle" and owner_title is None:
            owner_title = (elem.text or "").strip() or None
    if owner is None:
        # No reporting owner -> not a useful insider record.
        return None

    # first non-derivative transaction
    code = _text_of(root, "transactionCode") or ""
    tx_date = _text_of(root, "transactionDate")
    shares_raw = _text_of(root, "transactionShares")
    price_raw = _text_of(root, "transactionPricePerShare")
    after_raw = _text_of(root, "sharesOwnedFollowingTransaction")

    # Parse numerics defensively (SEC values are plain decimal strings).
    def _to_float(s: str | None) -> float | None:
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    return EdgarInsiderTransaction(
        accession_number=accession_number,
        reporting_owner=owner,
        owner_title=owner_title,
        transaction_date=tx_date,
        transaction_code=code,
        transaction_label=FORM4_TRANSACTION_CODES.get(code, code or "Unknown"),
        is_purchase=code == "P",
        is_sale=code == "S",
        shares=_to_float(shares_raw),
        price_per_share=_to_float(price_raw),
        shares_owned_after=_to_float(after_raw),
        filing_url=filing_url,
    )


class EdgarProvider(SignalProvider):
    provider_id = "sec_edgar"
    display_name = "SEC EDGAR"
    capabilities = ("insider_transactions", "filings_search")

    def __init__(
        self,
        http_client: JsonHttpClient | None = None,
        user_email: str | None = None,
    ):
        if http_client is None:
            # SEC EDGAR requires User-Agent with contact email to avoid 403.
            # See: https://www.sec.gov/os/accessing-edgar-data
            ua = f"real-information-analysis/{_package_version} ({user_email})" if user_email else f"real-information-analysis/{_package_version}"
            http_client = UrllibJsonClient(headers={
                "Accept": "application/json",
                "User-Agent": ua,
            })
        self.http_client: JsonHttpClient = http_client
        self._ticker_map: dict[str, dict[str, Any]] | None = None
        # Lazy ticker-map init runs under a lock: gather() may call this
        # provider from several threads at once.
        self._ticker_map_lock = threading.Lock()

    def _resolve_cik(self, ticker: str) -> tuple[str, str]:
        """Return (cik_padded, company_name) for a ticker."""
        if self._ticker_map is None:
            with self._ticker_map_lock:
                if self._ticker_map is None:
                    self._ticker_map = self._load_ticker_map()
        ticker_upper = ticker.upper()
        entry = self._ticker_map.get(ticker_upper)
        if not entry:
            raise ProviderError(f"ticker not found: {ticker}")
        cik = str(entry["cik_str"]).zfill(10)
        return cik, str(entry.get("title", ""))

    def _load_ticker_map(self) -> dict[str, dict[str, Any]]:
        data = self.http_client.get_json(EDGAR_TICKERS_URL)
        if not isinstance(data, Mapping):
            raise ProviderParseError("expected company_tickers.json to be an object")
        ticker_map: dict[str, dict[str, Any]] = {}
        for entry in data.values():
            if not isinstance(entry, Mapping):
                continue
            t = str(entry.get("ticker", "")).upper()
            if t:
                ticker_map[t] = dict(entry)
        return ticker_map

    def get_insider_transactions(self, query: EdgarInsiderQuery) -> EdgarInsiderSummary:
        """Get recent Form 4 filings (insider transactions) for a company."""
        cik, company_name = self._resolve_cik(query.ticker)
        submissions = self.http_client.get_json(
            f"{EDGAR_SUBMISSIONS_URL}/CIK{cik}.json"
        )
        if not isinstance(submissions, Mapping):
            raise ProviderParseError("expected submissions response to be an object")

        filings_block = submissions.get("filings")
        if not isinstance(filings_block, Mapping):
            raise ProviderParseError("expected submissions.filings to be an object")

        recent = filings_block.get("recent")
        if not isinstance(recent, Mapping):
            raise ProviderParseError("expected submissions.filings.recent to be an object")

        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_documents = recent.get("primaryDocument", [])
        primary_doc_descriptions = recent.get("primaryDocDescription", [])

        n = len(forms)
        form4_filings: list[EdgarFiling] = []
        total_form4_count = 0
        for i in range(n):
            if str(forms[i]) != "4":
                continue
            total_form4_count += 1
            if len(form4_filings) < query.limit:
                form4_filings.append(
                    EdgarFiling(
                        accession_number=str(accession_numbers[i]) if i < len(accession_numbers) else "",
                        form_type="4",
                        filing_date=str(filing_dates[i]) if i < len(filing_dates) else "",
                        report_date=str(report_dates[i]) if i < len(report_dates) else "",
                        primary_document=str(primary_documents[i]) if i < len(primary_documents) else "",
                        description=str(primary_doc_descriptions[i]) if i < len(primary_doc_descriptions) else "",
                    )
                )

        return EdgarInsiderSummary(
            ticker=query.ticker.upper(),
            company_name=company_name,
            cik=cik,
            recent_form4s=tuple(form4_filings),
            total_form4_count=total_form4_count,
        )

    def get_insider_transactions_detail(
        self, query: EdgarInsiderQuery
    ) -> list[EdgarInsiderTransaction]:
        """Fetch and parse the actual trades from recent Form 4 documents.

        :meth:`get_insider_transactions` only returns filing metadata; this
        method additionally downloads each Form 4's XML body and extracts
        who traded, buy/sell, shares, price and resulting holding — the data
        needed to judge insider sentiment.

        Filings that fail to download or parse are skipped (partial-failure
        tolerant), so one bad filing does not abort the whole batch.  The
        per-filing XML downloads run through :func:`gather` with a small
        worker pool - sequential fetches made a 20-filing query take minutes
        whenever a single archive URL hung until timeout.
        """
        summary = self.get_insider_transactions(query)
        cik_no_zeros = summary.cik.lstrip("0")

        get_text = getattr(self.http_client, "get_text", None)
        if get_text is None:  # pragma: no cover - UrllibJsonClient always has it
            raise ProviderError("HTTP client does not support get_text; cannot fetch Form 4 bodies")

        def fetch_one(filing: EdgarFiling) -> EdgarInsiderTransaction | None:
            acc_no_dash = filing.accession_number.replace("-", "")
            # primary_document may include an xsl render wrapper (e.g.
            # "xslF345X06/wk-form4_...xml"); the raw XML lives at the archive
            # root under its original filename.
            doc = filing.primary_document
            if doc.startswith("xslF345X06/"):
                doc = doc.split("/", 1)[1]
            url = f"{EDGAR_ARCHIVES_URL}/{cik_no_zeros}/{acc_no_dash}/{doc}"
            try:
                body = get_text(url)
            except Exception:
                # Skip filings we cannot fetch - don't abort the batch.
                return None
            try:
                return parse_form4_xml(body, filing.accession_number, url)
            except ProviderParseError:
                return None

        result = gather(
            {f.accession_number: partial(fetch_one, f) for f in summary.recent_form4s},
            max_workers=4,
        )
        # Preserve the filing order (newest first) from the summary.
        return [
            tx
            for tx in (result.get_or(f.accession_number, None) for f in summary.recent_form4s)
            if tx is not None
        ]

    # -- capital expenditure / R&D trends --------------------------------

    def get_capital_trends(
        self,
        tickers: list[str],
        concept: str = "R&D",
        years: int = 5,
    ) -> list[CompanyCapitalTrend]:
        """Fetch multi-year capital allocation trends for a list of companies.

        This is the **long-term capital allocation signal**: how much real
        money a company has committed to R&D / capex / productive assets over
        time. Unlike stock prices (mid-term consensus) or insider trades
        (short-term sentiment), this reflects *irreversible* capital already
        spent — the hardest signal for judging long-term commitment to a
        sector.

        Works for US filers (10-K/USD), Chinese ADRs (20-F/CNY+USD) and
        European ADRs (20-F/EUR). Currency is USD when reported, otherwise
        the native currency is kept (see :attr:`CompanyCapitalTrend.currency`).

        Parameters
        ----------
        tickers:
            Arbitrary list of company tickers — *no preset themes*. The caller
            decides which companies are relevant to the question.
        concept:
            One of ``"R&D"`` (research & development), ``"CapEx"``
            (payments to acquire productive assets), or ``"PP&E"``
            (net property/plant/equipment).
        years:
            How many most-recent fiscal years to include in the history.
        """
        if concept not in CAPITAL_CONCEPTS:
            raise ValueError(
                f"unknown concept {concept!r}; choose from {list(CAPITAL_CONCEPTS)}"
            )
        tag_variants = CAPITAL_CONCEPTS[concept]
        get_json = self.http_client.get_json

        trends: list[CompanyCapitalTrend] = []
        for ticker in tickers:
            try:
                cik, company_name = self._resolve_cik(ticker)
            except ProviderError:
                continue  # unknown ticker — skip, don't abort the batch
            trend = None
            for namespace, tag in tag_variants.items():
                try:
                    payload = get_json(
                        f"{EDGAR_XBRL_CONCEPT_URL}/CIK{cik}/{namespace}/{tag}.json"
                    )
                except Exception:
                    continue  # tag not reported under this namespace — try next
                if not isinstance(payload, Mapping):
                    continue

                unit_key, history = self._extract_capital_history(
                    payload, years=years
                )
                if unit_key is None or not history:
                    continue

                latest = history[-1]
                prior = history[-2] if len(history) >= 2 else None
                yoy = (
                    (latest.value - prior.value) / abs(prior.value) * 100.0
                    if prior and prior.value not in (0, None)
                    else None
                )
                trend = CompanyCapitalTrend(
                    ticker=ticker.upper(),
                    company_name=company_name,
                    cik=cik,
                    concept=concept,
                    currency=unit_key,
                    history=tuple(history),
                    latest_value=latest.value,
                    latest_fiscal_year=latest.fiscal_year,
                    yoy_growth_pct=yoy,
                    xbrl_namespace=namespace,
                )
                break
            if trend is not None:
                trends.append(trend)
        return trends

    def _extract_capital_history(
        self,
        payload: Mapping[str, Any],
        *,
        years: int,
    ) -> tuple[str | None, list[CapitalDataPoint]]:
        """Pick the best currency unit and return ascending annual history.

        Prefers USD (so cross-company comparison stays apples-to-apples), then
        falls back to the first available unit. Deduplicates by fiscal year
        (XBRL can report the same FY from a 10-K and a 10-K/A amendment).
        """
        units = payload.get("units")
        if not isinstance(units, Mapping) or not units:
            return None, []

        # Prefer USD; otherwise take the first unit reported.
        unit_key = "USD" if "USD" in units else next(iter(units), None)
        if unit_key is None:
            return None, []

        raw = units.get(unit_key)
        if not isinstance(raw, list):
            return unit_key, []

        # Only annual filings: US filers use 10-K, foreign filers use 20-F.
        annual = [
            x for x in raw
            if isinstance(x, Mapping)
            and x.get("form") in ("10-K", "20-F")
            and isinstance(x.get("fy"), int)
            and x.get("val") is not None
        ]
        # Deduplicate by fiscal year, keeping the latest period_end when a FY
        # repeats (amended filings).
        by_year: dict[int, CapitalDataPoint] = {}
        for x in annual:
            fy = x["fy"]
            val = _coerce_float(x.get("val"))
            if val is None:
                continue
            end = str(x.get("end", ""))
            existing = by_year.get(fy)
            if existing is None or end > existing.period_end:
                by_year[fy] = CapitalDataPoint(fiscal_year=fy, value=val, period_end=end)

        ordered = [by_year[k] for k in sorted(by_year)]
        return unit_key, ordered[-years:] if years > 0 else ordered

    def search_filings(self, query: EdgarSearchQuery) -> list[EdgarSearchHit]:
        """Full-text search across SEC filings."""
        params: dict[str, object] = {
            "q": query.query,
        }
        if query.forms:
            params["forms"] = query.forms
        if query.date_start and query.date_end:
            params["dateRange"] = "custom"
            params["startdt"] = query.date_start
            params["enddt"] = query.date_end
        elif query.date_start:
            params["dateRange"] = "custom"
            params["startdt"] = query.date_start
        elif query.date_end:
            params["dateRange"] = "custom"
            params["enddt"] = query.date_end

        payload = self.http_client.get_json(EDGAR_SEARCH_URL, params=params)
        if not isinstance(payload, Mapping):
            raise ProviderParseError("expected search response to be an object")

        hits_outer = payload.get("hits")
        if not isinstance(hits_outer, Mapping):
            raise ProviderParseError("expected search response.hits to be an object")

        hits_inner = hits_outer.get("hits", [])
        if not isinstance(hits_inner, list):
            raise ProviderParseError("expected search response.hits.hits to be a list")

        results: list[EdgarSearchHit] = []
        for hit in hits_inner:
            if not isinstance(hit, Mapping):
                continue
            source = hit.get("_source", {})
            if not isinstance(source, Mapping):
                continue
            results.append(
                EdgarSearchHit(
                    entity_name=str(source.get("entity_name", "")),
                    file_date=str(source.get("file_date", "")),
                    form_type=str(source.get("form_type", "")),
                    file_number=str(source.get("file_num", "")),
                    description=_extract_description(source),
                )
            )
            if len(results) >= query.limit:
                break

        return results
