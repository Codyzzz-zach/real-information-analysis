"""Tests for real_information_analysis.http - URL building and error redaction."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from real_information_analysis.http import (
    HttpClientError,
    UrllibJsonClient,
    _build_url,
    _redact_url,
)


class RedactUrlTests(unittest.TestCase):
    def test_masks_sensitive_query_values(self) -> None:
        url = "https://api.example.com/fred/series?series_id=VIXCLS&api_key=SUPERSECRET&file_type=json"
        redacted = _redact_url(url)
        self.assertNotIn("SUPERSECRET", redacted)
        self.assertIn("api_key=***", redacted)
        # Non-sensitive params survive untouched.
        self.assertIn("series_id=VIXCLS", redacted)
        self.assertIn("file_type=json", redacted)

    def test_masks_all_common_credential_param_names(self) -> None:
        for key in ("apikey", "key", "token", "access_token", "api_token"):
            url = f"https://example.com/x?{key}=SECRET"
            self.assertNotIn("SECRET", _redact_url(url), key)

    def test_key_matching_is_case_insensitive(self) -> None:
        redacted = _redact_url("https://example.com/x?API_KEY=SECRET")
        self.assertNotIn("SECRET", redacted)

    def test_url_without_query_returned_unchanged(self) -> None:
        url = "https://example.com/data.json"
        self.assertEqual(_redact_url(url), url)

    def test_valueless_params_preserved(self) -> None:
        url = "https://example.com/x?flag&api_key=S"
        redacted = _redact_url(url)
        self.assertIn("flag&", redacted)
        self.assertNotIn("=S", redacted)


class TransientStatusRetryTests(unittest.TestCase):
    """429/5xx are retried; other statuses fail immediately."""

    @staticmethod
    def _http_error(url: str, code: int) -> HTTPError:
        return HTTPError(url, code, "error", None, None)  # type: ignore[arg-type]

    def test_429_retried_until_success(self) -> None:
        calls: list[str] = []

        def flaky(request, timeout):  # noqa: ANN001 - urllib stub
            calls.append(request.full_url)
            if len(calls) < 3:
                raise self._http_error(request.full_url, 429)
            return io.BytesIO(b'{"ok": true}')

        client = UrllibJsonClient(retry_attempts=3, retry_delay_seconds=0.0)
        with patch("real_information_analysis.http.urlopen", side_effect=flaky):
            payload = client.get_json("https://api.example.com/x")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(calls), 3)

    def test_503_retried(self) -> None:
        calls: list[str] = []

        def flaky(request, timeout):  # noqa: ANN001 - urllib stub
            calls.append(request.full_url)
            if len(calls) < 2:
                raise self._http_error(request.full_url, 503)
            return io.BytesIO(b'{"ok": true}')

        client = UrllibJsonClient(retry_attempts=2, retry_delay_seconds=0.0)
        with patch("real_information_analysis.http.urlopen", side_effect=flaky):
            self.assertEqual(client.get_json("https://api.example.com/x"), {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_403_not_retried(self) -> None:
        calls: list[str] = []

        def forbidden(request, timeout):  # noqa: ANN001 - urllib stub
            calls.append(request.full_url)
            raise self._http_error(request.full_url, 403)

        client = UrllibJsonClient(retry_attempts=3, retry_delay_seconds=0.0)
        with patch("real_information_analysis.http.urlopen", side_effect=forbidden):
            with self.assertRaises(HttpClientError):
                client.get_json("https://api.example.com/x")
        self.assertEqual(len(calls), 1)

    def test_retryable_status_exhausted_raises(self) -> None:
        def always_429(request, timeout):  # noqa: ANN001 - urllib stub
            raise self._http_error(request.full_url, 429)

        client = UrllibJsonClient(retry_attempts=2, retry_delay_seconds=0.0)
        with patch("real_information_analysis.http.urlopen", side_effect=always_429):
            with self.assertRaises(HttpClientError):
                client.get_text("https://api.example.com/x")


class BuildUrlTests(unittest.TestCase):
    def test_none_params_skipped(self) -> None:
        self.assertEqual(_build_url("https://x.io/a", {"a": 1, "b": None}), "https://x.io/a?a=1")

    def test_bool_serialised_lowercase(self) -> None:
        self.assertEqual(_build_url("https://x.io/a", {"a": True}), "https://x.io/a?a=true")


class ErrorRedactionIntegrationTests(unittest.TestCase):
    """A failing request must not echo credentials back in the exception."""

    def _raise_http_error(self, request, timeout):  # noqa: ANN001 - urllib stub
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)  # type: ignore[arg-type]

    def test_http_error_message_redacts_api_key(self) -> None:
        client = UrllibJsonClient(retry_attempts=1)
        with patch("real_information_analysis.http.urlopen", side_effect=self._raise_http_error):
            with self.assertRaises(HttpClientError) as ctx:
                client.get_json(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={"series_id": "VIXCLS", "api_key": "SUPERSECRET123"},
                )
        message = str(ctx.exception)
        self.assertNotIn("SUPERSECRET123", message)
        self.assertIn("api_key=***", message)

    def test_connection_error_message_redacts_api_key(self) -> None:
        import socket
        from urllib.error import URLError

        def _raise_url_error(request, timeout):  # noqa: ANN001 - urllib stub
            raise URLError(socket.timeout("timed out"))

        client = UrllibJsonClient(retry_attempts=1, retry_delay_seconds=0.0)
        with patch("real_information_analysis.http.urlopen", side_effect=_raise_url_error):
            with self.assertRaises(HttpClientError) as ctx:
                client.get_text(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={"api_key": "SUPERSECRET123"},
                )
        self.assertNotIn("SUPERSECRET123", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
