from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from digital_oracle.http import JsonHttpClient, UrllibJsonClient

from ._coerce import _coerce_float
from .base import ProviderParseError, SignalProvider

WORLDBANK_BASE = "https://api.worldbank.org/v2"


class WorldBankHttpClient(JsonHttpClient, Protocol):
    pass


@dataclass(frozen=True)
class WorldBankQuery:
    indicator: str  # e.g. "NY.GDP.MKTP.CD"
    countries: tuple[str, ...] = ("US", "CN")
    date_range: str = "2015:2025"
    per_page: int = 500


@dataclass(frozen=True)
class WorldBankDataPoint:
    country_code: str
    country_name: str
    indicator_id: str
    indicator_name: str
    date: str  # year string
    value: float | None


@dataclass(frozen=True)
class WorldBankResult:
    indicator_id: str
    indicator_name: str
    points: tuple[WorldBankDataPoint, ...]

    @property
    def latest(self) -> WorldBankDataPoint | None:
        """Most recent data point (points are stored newest-first by the API)."""
        # Skip leading points whose value is None (not-yet-published years).
        for p in self.points:
            if p.value is not None:
                return p
        return None

    def latest_for_country(self, country_code: str) -> WorldBankDataPoint | None:
        """Most recent non-null point for a specific country."""
        for p in self.points:
            if p.country_code == country_code and p.value is not None:
                return p
        return None


class WorldBankProvider(SignalProvider):
    provider_id = "worldbank"
    display_name = "World Bank"
    capabilities = ("indicators",)

    def __init__(self, http_client: WorldBankHttpClient | None = None):
        self.http_client = http_client or UrllibJsonClient()

    def get_indicator(self, query: WorldBankQuery) -> WorldBankResult:
        countries_str = ";".join(query.countries)
        url = f"{WORLDBANK_BASE}/country/{countries_str}/indicator/{query.indicator}"
        payload = self.http_client.get_json(
            url,
            params={
                "format": "json",
                "date": query.date_range,
                "per_page": query.per_page,
            },
        )

        if not isinstance(payload, list) or len(payload) < 2:
            raise ProviderParseError(
                "expected World Bank response to be a JSON array with [metadata, data]"
            )

        _meta, data = payload[0], payload[1]

        if data is None:
            data = []

        if not isinstance(data, list):
            raise ProviderParseError("expected World Bank data element to be a list")

        points: list[WorldBankDataPoint] = []
        for item in data:
            if not isinstance(item, Mapping):
                continue
            value = _coerce_float(item.get("value"))
            country = item.get("country")
            indicator = item.get("indicator")
            if not isinstance(country, Mapping) or not isinstance(indicator, Mapping):
                continue
            points.append(
                WorldBankDataPoint(
                    country_code=str(country.get("id", "")),
                    country_name=str(country.get("value", "")),
                    indicator_id=str(indicator.get("id", "")),
                    indicator_name=str(indicator.get("value", "")),
                    date=str(item.get("date", "")),
                    value=value,
                )
            )

        indicator_name = (
            points[0].indicator_name if points else query.indicator
        )

        return WorldBankResult(
            indicator_id=query.indicator,
            indicator_name=indicator_name,
            points=tuple(points),
        )
