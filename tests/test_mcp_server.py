"""MCP acceptance suite A1–A5 (PRODUCTIZATION_PLAN.md §R6).

A1 protocol handshake + tool surface · A2 golden replay determinism ·
A3 error contract (no tracebacks, redacted secrets) · A4 security surface
(untrusted delimiters, SSRF refusal) · A5 zero-dependency stdio smoke
(subprocess, no network).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from real_information_analysis.mcp_server import McpOverrides, build_server, serve
from real_information_analysis.providers.web import UNTRUSTED_CONTENT_BANNER
from real_information_analysis.snapshots import RecordingHttpClient, ReplayHttpClient

EXPECTED_TOOL_NAMES = {
    "prediction_markets_search",
    "prediction_market_book",
    "price_history",
    "options_chain",
    "yield_curve",
    "cot_positions",
    "insider_trades",
    "policy_rates",
    "credit_gap",
    "fear_greed",
    "rate_probabilities",
    "web_search",
    "web_fetch",
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeJsonClient:
    """Deterministic JSON responses keyed by URL substring."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, url: str, *, params=None):
        self.calls.append(url)
        if "fearandgreed" in url or "fear" in url:
            return {"fear_and_greed": {"score": "42", "rating": "Fear", "previous_close": "40"}}
        if "cftc" in url.lower() or "72hh" in url:
            return [{
                "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
                "report_date_as_yyyy_mm_dd": "2026-03-04T00:00:00.000",
                "commodity_name": "GOLD",
                "open_interest_all": "550000",
                "m_money_positions_long_all": "180000",
                "m_money_positions_short_all": "45000",
            }]
        return {"ok": True, "url": url}


DDG_CANNED_HTML = (
    "<html><body>"
    '<a class="result__a" href="https://cboe.com/vix">CBOE VIX</a>'
    '<a class="result__snippet" href="https://cboe.com/vix">VIX at 14.2</a>'
    "</body></html>"
)


class FakeSearchClient:
    def __init__(self) -> None:
        self.search_queries: list[str] = []

    def fetch(self, url: str, *, headers=None) -> str:
        return "<html><head><title>T</title></head><body><p>VIX closed at 14.2</p></body></html>"

    def post_form(self, url: str, *, data, headers=None) -> str:
        self.search_queries.append(data["q"])
        return DDG_CANNED_HTML


class ExplodingJsonClient:
    """Simulates a provider outage with a secret-bearing URL (A3)."""

    def get_json(self, url: str, *, params=None):
        raise RuntimeError(f"upstream 500 for https://api.example.com/data?api_key=SECRETVALUE")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_request(method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    request = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def result_text(response: dict) -> str:
    return response["result"]["content"][0]["text"]


def build_test_server(json_client=None) -> object:
    return build_server(McpOverrides(
        json_client=json_client if json_client is not None else FakeJsonClient(),
        page_client=FakeSearchClient(),
    ))


# ---------------------------------------------------------------------------
# A1 — protocol handshake and tool surface
# ---------------------------------------------------------------------------


class A1ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = build_test_server()

    def test_initialize_negotiates_supported_version(self) -> None:
        response = self.server.handle(make_request("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response["result"]["serverInfo"]["name"], "real-information-analysis")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_initialize_falls_back_to_latest_for_unknown_version(self) -> None:
        response = self.server.handle(make_request("initialize", {"protocolVersion": "1999-01-01"}))
        self.assertEqual(response["result"]["protocolVersion"], "2026-07-28")

    def test_tools_list_exposes_expected_vendor_neutral_surface(self) -> None:
        response = self.server.handle(make_request("tools/list"))
        tools = {t["name"]: t for t in response["result"]["tools"]}
        self.assertEqual(set(tools), EXPECTED_TOOL_NAMES)
        for tool in tools.values():
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_required_arguments_declared_in_schema(self) -> None:
        response = self.server.handle(make_request("tools/list"))
        tools = {t["name"]: t for t in response["result"]["tools"]}
        self.assertEqual(tools["prediction_markets_search"]["inputSchema"]["required"], ["query"])
        self.assertEqual(tools["web_fetch"]["inputSchema"]["required"], ["url"])
        self.assertEqual(tools["yield_curve"]["inputSchema"]["required"], [])

    def test_ping_and_notifications(self) -> None:
        self.assertEqual(self.server.handle(make_request("ping"))["result"], {})
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method_is_jsonrpc_error(self) -> None:
        response = self.server.handle(make_request("resources/list"))
        self.assertEqual(response["error"]["code"], -32601)


# ---------------------------------------------------------------------------
# A2 — golden replay determinism
# ---------------------------------------------------------------------------


class A2ReplayTests(unittest.TestCase):
    def test_identical_calls_give_identical_results(self) -> None:
        server = build_test_server()
        request = make_request("tools/call", {"name": "fear_greed", "arguments": {}})
        first = result_text(server.handle(request))
        second = result_text(server.handle(request))
        self.assertEqual(first, second)

    def test_record_then_replay_roundtrip(self) -> None:
        """A2 in its canonical form: record snapshots, replay them, byte-equal."""
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RecordingHttpClient(snapshot_dir=tmp, json_client=FakeJsonClient())
            live = build_server(McpOverrides(json_client=recorder, page_client=FakeSearchClient()))
            request = make_request("tools/call", {"name": "cot_positions", "arguments": {"commodity": "GOLD"}})
            recorded = result_text(live.handle(request))

            replayer = ReplayHttpClient(tmp)
            replayed = build_server(McpOverrides(json_client=replayer, page_client=FakeSearchClient()))
            self.assertEqual(result_text(replayed.handle(request)), recorded)

    def test_replay_dir_flag_routes_json_providers_through_replay_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RecordingHttpClient(snapshot_dir=tmp, json_client=FakeJsonClient())
            seed = build_server(McpOverrides(json_client=recorder, page_client=FakeSearchClient()))
            seed.handle(make_request("tools/call", {"name": "fear_greed", "arguments": {}}))

            replay_server = build_server(McpOverrides(replay_dir=tmp, page_client=FakeSearchClient()))
            response = replay_server.handle(make_request("tools/call", {"name": "fear_greed", "arguments": {}}))
            self.assertFalse(response["result"]["isError"])
            self.assertIn("42", result_text(response))


# ---------------------------------------------------------------------------
# A3 — error contract
# ---------------------------------------------------------------------------


class A3ErrorContractTests(unittest.TestCase):
    def test_provider_failure_becomes_iserror_without_traceback(self) -> None:
        server = build_server(McpOverrides(json_client=ExplodingJsonClient(), page_client=FakeSearchClient()))
        response = server.handle(make_request("tools/call", {"name": "fear_greed", "arguments": {}}))
        self.assertTrue(response["result"]["isError"])
        self.assertIn("upstream 500", result_text(response))
        self.assertNotIn("Traceback", result_text(response))

    def test_secrets_never_leak_through_tool_errors(self) -> None:
        server = build_server(McpOverrides(json_client=ExplodingJsonClient(), page_client=FakeSearchClient()))
        response = server.handle(make_request("tools/call", {"name": "fear_greed", "arguments": {}}))
        self.assertNotIn("SECRETVALUE", json.dumps(response))

    def test_unknown_tool_is_iserror(self) -> None:
        server = build_test_server()
        response = server.handle(make_request("tools/call", {"name": "nonexistent", "arguments": {}}))
        self.assertTrue(response["result"]["isError"])
        self.assertIn("unknown tool", result_text(response))

    def test_missing_required_argument_is_iserror(self) -> None:
        server = build_test_server()
        response = server.handle(make_request("tools/call", {"name": "prediction_markets_search", "arguments": {}}))
        self.assertTrue(response["result"]["isError"])

    def test_non_object_arguments_rejected(self) -> None:
        server = build_test_server()
        response = server.handle(make_request("tools/call", {"name": "fear_greed", "arguments": ["bad"]}))
        self.assertTrue(response["result"]["isError"])


# ---------------------------------------------------------------------------
# A4 — security surface
# ---------------------------------------------------------------------------


class A4SecurityTests(unittest.TestCase):
    def test_web_fetch_returns_untrusted_delimited_content(self) -> None:
        server = build_test_server()
        response = server.handle(make_request("tools/call", {"name": "web_fetch", "arguments": {"url": "https://example.com/news"}}))
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(result_text(response))
        self.assertTrue(payload["untrusted"])
        self.assertEqual(payload["text"].count(UNTRUSTED_CONTENT_BANNER), 2)
        self.assertIn("VIX closed at 14.2", payload["text"])

    def test_web_fetch_refuses_private_hosts(self) -> None:
        server = build_test_server()
        for url in ("http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data/"):
            with self.subTest(url=url):
                response = server.handle(make_request("tools/call", {"name": "web_fetch", "arguments": {"url": url}}))
                self.assertTrue(response["result"]["isError"])

    def test_web_search_render_marks_query_and_sources(self) -> None:
        server = build_test_server()
        response = server.handle(make_request("tools/call", {"name": "web_search", "arguments": {"query": "VIX"}}))
        self.assertFalse(response["result"]["isError"])
        self.assertIn('Search: "VIX"', result_text(response))
        self.assertIn("[1] CBOE VIX", result_text(response))

    def test_web_search_goes_through_injected_client(self) -> None:
        """Search traffic must be interceptable — no direct urlopen (A2/A5 seam)."""
        page_client = FakeSearchClient()
        server = build_server(McpOverrides(json_client=FakeJsonClient(), page_client=page_client))
        server.handle(make_request("tools/call", {"name": "web_search", "arguments": {"query": "VIX"}}))
        self.assertEqual(page_client.search_queries, ["VIX"])


# ---------------------------------------------------------------------------
# A5 — zero-dependency stdio smoke (subprocess, no network)
# ---------------------------------------------------------------------------


class A5StdioSmokeTests(unittest.TestCase):
    def test_subprocess_handshake_and_tools_list(self) -> None:
        messages = [
            make_request("initialize", {"protocolVersion": "2025-06-18"}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            make_request("tools/list"),
        ]
        payload = "\n".join(json.dumps(m) for m in messages) + "\n"
        completed = subprocess.run(
            [sys.executable, "-m", "real_information_analysis.mcp_server"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)  # notification produced no response
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "real-information-analysis")
        tool_names = {t["name"] for t in lines[1]["result"]["tools"]}
        self.assertEqual(tool_names, EXPECTED_TOOL_NAMES)

    def test_parse_error_returns_jsonrpc_error(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "real_information_analysis.mcp_server"],
            input="not-json\n",
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(ROOT),
        )
        self.assertEqual(completed.returncode, 0)
        response = json.loads(completed.stdout.strip())
        self.assertEqual(response["error"]["code"], -32700)

    def test_serve_loops_over_stdin_lines(self) -> None:
        server = build_test_server()
        import io

        stdin = io.StringIO(json.dumps(make_request("tools/list")) + "\n\n" + "   \n")
        stdout = io.StringIO()
        serve(server, stdin=stdin, stdout=stdout)
        response = json.loads(stdout.getvalue().strip())
        self.assertEqual(len(response["result"]["tools"]), len(EXPECTED_TOOL_NAMES))


if __name__ == "__main__":
    unittest.main()
