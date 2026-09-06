from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import StringIO
from typing import Protocol
from urllib.parse import quote

from ..http import TextHttpClient, UrllibJsonClient

from ._coerce import _coerce_float
from .base import ProviderParseError, SignalProvider

BIS_BASE_URL = "https://stats.bis.org/api/v1"

# The WS_CREDIT_GAP dataset carries three series per country-quarter under the
# CG_DTYPE dimension. Empirically verified 2026-09-06 (two countries):
#   CN 2024-Q1: A=198.2  B=203.2  C=-4.9
#   US 2025-Q2: A=141.0  B=153.4  C=-12.4
# A/B sit in credit-to-GDP *ratio* magnitude (140-205% of GDP); C is the signed
# Basel-style credit-to-GDP *gap*. Only C is the measure this provider names.
# Caveat: BIS has remapped the codes before — the same query in 2026-07
# labelled the gap "A" — so re-verify against the magnitude signature if
# values ever look like a ratio. Set ``include_all_series`` to bypass the
# filter and inspect every series yourself.
GAP_DATA_TYPE = "C"


class BisHttpClient(TextHttpClient, Protocol):
    pass


@dataclass(frozen=True)
class BisRateQuery:
    countries: tuple[str, ...] = ("US",)
    start_year: int = 2020


@dataclass(frozen=True)
class BisPolicyRate:
    country: str
    period: str  # e.g. "2026-01"
    rate: float


@dataclass(frozen=True)
class BisCreditGapQuery:
    countries: tuple[str, ...] = ("US",)
    start_year: int = 2015
    include_all_series: bool = False  # False → only the gap series (CG_DTYPE C)


@dataclass(frozen=True)
class BisCreditGap:
    country: str
    period: str  # e.g. "2025-Q3"
    gap_pct: float  # credit-to-GDP gap percentage points
    data_type: str = ""  # BIS CG_DTYPE code; "" when the CSV lacks the column


class BisProvider(SignalProvider):
    provider_id = "bis"
    display_name = "Bank for International Settlements"
    capabilities = ("policy_rates", "credit_gaps")

    def __init__(self, http_client: BisHttpClient | None = None):
        self.http_client = http_client or UrllibJsonClient()

    def get_policy_rates(self, query: BisRateQuery | None = None) -> list[BisPolicyRate]:
        query = query or BisRateQuery()
        country_codes = "+".join(quote(c, safe="") for c in query.countries)
        url = f"{BIS_BASE_URL}/data/WS_CBPOL/M.{country_codes}"
        payload = self.http_client.get_text(
            url,
            params={
                "startPeriod": query.start_year,
                "detail": "dataonly",
                "format": "csv",
            },
        )
        return self._parse_policy_rates_csv(payload)

    def get_credit_to_gdp(self, query: BisCreditGapQuery | None = None) -> list[BisCreditGap]:
        query = query or BisCreditGapQuery()
        country_codes = "+".join(quote(c, safe="") for c in query.countries)
        url = f"{BIS_BASE_URL}/data/WS_CREDIT_GAP/Q.{country_codes}"
        payload = self.http_client.get_text(
            url,
            params={
                "startPeriod": query.start_year,
                "detail": "dataonly",
                "format": "csv",
            },
        )
        gaps = self._parse_credit_gap_csv(payload)
        if not query.include_all_series and any(gap.data_type for gap in gaps):
            # Default to the actual gap series: the dataset also carries
            # credit-to-GDP *ratio* variants, which would otherwise be
            # indistinguishable from the gap by column name alone. CSVs
            # without a CG_DTYPE column (legacy format) are passed through.
            gaps = [gap for gap in gaps if gap.data_type == GAP_DATA_TYPE]
        return gaps

    def _parse_policy_rates_csv(self, payload: str) -> list[BisPolicyRate]:
        reader = csv.DictReader(StringIO(payload))
        if not reader.fieldnames:
            raise ProviderParseError("BIS policy rates CSV has no headers")

        required = {"REF_AREA", "TIME_PERIOD", "OBS_VALUE"}
        if not required.issubset(set(reader.fieldnames)):
            raise ProviderParseError(
                f"BIS policy rates CSV missing required columns; "
                f"expected {required}, got {reader.fieldnames}"
            )

        rates: list[BisPolicyRate] = []
        for row in reader:
            if not row:
                continue
            value = _coerce_float(row.get("OBS_VALUE"))
            if value is None:
                continue
            rates.append(
                BisPolicyRate(
                    country=row["REF_AREA"],
                    period=row["TIME_PERIOD"],
                    rate=value,
                )
            )
        return rates

    def _parse_credit_gap_csv(self, payload: str) -> list[BisCreditGap]:
        reader = csv.DictReader(StringIO(payload))
        if not reader.fieldnames:
            raise ProviderParseError("BIS credit gap CSV has no headers")

        required = {"TIME_PERIOD", "OBS_VALUE"}
        if not required.issubset(set(reader.fieldnames)):
            raise ProviderParseError(
                f"BIS credit gap CSV missing required columns; "
                f"expected {required}, got {reader.fieldnames}"
            )

        # BIS uses REF_AREA or BORROWERS_CTY depending on the dataset version
        country_col = "REF_AREA" if "REF_AREA" in reader.fieldnames else "BORROWERS_CTY"
        has_dtype = "CG_DTYPE" in reader.fieldnames

        gaps: list[BisCreditGap] = []
        for row in reader:
            if not row:
                continue
            value = _coerce_float(row.get("OBS_VALUE"))
            if value is None:
                continue
            gaps.append(
                BisCreditGap(
                    country=row.get(country_col, ""),
                    period=row["TIME_PERIOD"],
                    gap_pct=value,
                    data_type=row.get("CG_DTYPE", "") if has_dtype else "",
                )
            )
        return gaps
