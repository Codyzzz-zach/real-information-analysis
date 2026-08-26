from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ._version import __version__ as _package_version


class HttpClientError(RuntimeError):
    pass


# Query-parameter names whose values are masked in error messages so that
# credentials (e.g. the FRED api_key) never leak into tracebacks or logs.
_SENSITIVE_PARAMS = {"api_key", "apikey", "key", "token", "access_token", "api_token"}

# Transient HTTP statuses worth retrying (same policy as the Yahoo
# fetchers): rate limiting and typical gateway failures.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _redact_url(url: str) -> str:
    base, sep, query = url.partition("?")
    if not sep:
        return url
    kept = []
    for pair in query.split("&"):
        key, key_sep, _value = pair.partition("=")
        if key_sep and key.lower() in _SENSITIVE_PARAMS:
            kept.append(f"{key}=***")
        else:
            kept.append(pair)
    return f"{base}?{'&'.join(kept)}"


class JsonHttpClient(Protocol):
    def get_json(self, url: str, *, params: Mapping[str, object] | None = None) -> Any: ...


class TextHttpClient(Protocol):
    def get_text(self, url: str, *, params: Mapping[str, object] | None = None) -> str: ...


def _serialize_query_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_url(url: str, params: Mapping[str, object] | None) -> str:
    if not params:
        return url
    query_items = [
        (key, _serialize_query_value(value))
        for key, value in params.items()
        if value is not None
    ]
    if not query_items:
        return url
    return f"{url}?{urlencode(query_items)}"


@dataclass
class UrllibJsonClient:
    timeout_seconds: float = 20.0
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0
    headers: Mapping[str, str] = field(
        default_factory=lambda: {
            "Accept": "application/json,text/csv,text/plain,application/xml",
            "User-Agent": f"real-information-analysis/{_package_version}",
        }
    )

    def get_json(self, url: str, *, params: Mapping[str, object] | None = None) -> Any:
        request_url = self._build_request(url, params)
        try:
            with self._open(request_url) as response:
                return json.load(response)
        except HTTPError as exc:
            raise HttpClientError(f"request failed: {_redact_url(request_url.full_url)}") from exc
        except json.JSONDecodeError as exc:
            raise HttpClientError(f"invalid json payload: {_redact_url(request_url.full_url)}") from exc

    def get_text(self, url: str, *, params: Mapping[str, object] | None = None) -> str:
        request_url = self._build_request(url, params)
        try:
            with self._open(request_url) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise HttpClientError(f"request failed: {_redact_url(request_url.full_url)}") from exc

    def _build_request(self, url: str, params: Mapping[str, object] | None) -> Request:
        request_url = _build_url(url, params)
        return Request(request_url, headers=dict(self.headers))

    def _open(self, request: Request):
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return urlopen(request, timeout=self.timeout_seconds)
            except HTTPError as exc:
                if exc.code in _RETRYABLE_STATUS_CODES and attempt < self.retry_attempts:
                    last_error = exc
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise
            except (URLError, TimeoutError) as exc:
                last_error = exc
                if attempt >= self.retry_attempts:
                    break
                time.sleep(self.retry_delay_seconds)
        raise HttpClientError(f"request failed: {_redact_url(request.full_url)}") from last_error
