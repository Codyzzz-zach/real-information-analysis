"""Network routing table — every provider host must be classified.

Two lists drifted apart in the 1.3.4 audit: the providers hardcode one set
of hosts (15 sources), while the local MCP config carried a NO_PROXY
allowlist covering only some of them — anything missing silently went
through the proxy, and at least one provider (Kalshi) demonstrably fails
through proxies while working direct.

The invariant this suite locks: every ``https://`` host reachable from the
provider layer appears in ``NETWORK_ROUTES`` below with an explicit route
(``direct`` or ``proxy``), and the workspace MCP config's NO_PROXY list is
exactly the ``direct`` set. Adding a provider host without classifying it
fails here, not in the field.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The routing table. `direct` = must bypass the proxy (observed to fail
# through it, or known-clean); `proxy` = must go through the proxy
# (DNS-polluted on some networks). This table is committed so open-source
# users can generate their own config from it; .zcode/config.json is the
# local, gitignored rendering of it.
NETWORK_ROUTES: dict[str, str] = {
    # prediction markets
    "gamma-api.polymarket.com": "proxy",       # DNS-polluted on some networks
    "clob.polymarket.com": "proxy",
    "api.elections.kalshi.com": "direct",      # fails through local proxy 3/3 (v1.3.4)
    # market data
    "query1.finance.yahoo.com": "direct",
    "query2.finance.yahoo.com": "direct",
    "fc.yahoo.com": "direct",
    "production.dataviz.cnn.io": "direct",     # fear & greed
    "www.deribit.com": "direct",
    "api.coingecko.com": "direct",
    "stooq.com": "direct",
    # official statistics
    "home.treasury.gov": "direct",
    "api.fiscaldata.treasury.gov": "direct",
    "api.stlouisfed.org": "direct",
    "www.sec.gov": "direct",
    "data.sec.gov": "direct",
    "efts.sec.gov": "direct",
    "publicreporting.cftc.gov": "direct",
    "stats.bis.org": "direct",
    "api.worldbank.org": "direct",
    # web search
    "html.duckduckgo.com": "direct",
}

_HTTPS_HOST = re.compile(r"https://([A-Za-z0-9.-]+)")

# Lines that mention a URL without ever requesting it: Referer/Origin
# headers (browser courtesy, not a target) and docstring/comment pointers
# (API-key signup pages etc.).
_NON_REQUEST_LINE = re.compile(r"Referer|Origin|apikeys|#|\"\"\"|'''")


def _provider_hosts() -> set[str]:
    """Extract every https:// host the provider layer actually requests.

    URL constants and endpoint f-strings, not documentation mentions: lines
    carrying a Referer/Origin header or living inside comments/docstrings
    are skipped so the table tracks real traffic, not prose.
    """
    hosts: set[str] = set()
    package_dir = ROOT / "real_information_analysis"
    for path in package_dir.glob("*.py"):
        for line in path.read_text().splitlines():
            if _NON_REQUEST_LINE.search(line):
                continue
            for match in _HTTPS_HOST.finditer(line):
                hosts.add(match.group(1))
    for path in (package_dir / "providers").glob("*.py"):
        for line in path.read_text().splitlines():
            if _NON_REQUEST_LINE.search(line):
                continue
            for match in _HTTPS_HOST.finditer(line):
                hosts.add(match.group(1))
    return hosts


class RoutingTableTests(unittest.TestCase):
    # If the line-filter ever starts over-skipping (e.g. a URL constant
    # gains a trailing inline comment), these sentinels must still be
    # extracted — otherwise hosts would drop out of the table silently.
    _SENTINEL_HOSTS = frozenset({
        "gamma-api.polymarket.com",
        "api.elections.kalshi.com",
        "data.sec.gov",
        "html.duckduckgo.com",
        "api.fiscaldata.treasury.gov",
    })

    def test_extraction_floor_sentinels_present(self) -> None:
        hosts = _provider_hosts()
        missing = self._SENTINEL_HOSTS - hosts
        self.assertEqual(
            missing, set(),
            "extractor lost known request hosts (over-filtering?): " f"{sorted(missing)}",
        )

    def test_every_provider_host_is_classified(self) -> None:
        hosts = _provider_hosts()
        self.assertTrue(hosts, "provider host extraction found nothing — extractor broken")
        unclassified = sorted(hosts - set(NETWORK_ROUTES))
        self.assertEqual(
            unclassified,
            [],
            "provider hosts with no routing classification (add them to "
            f"NETWORK_ROUTES): {unclassified}",
        )

    def test_routes_are_valid_values(self) -> None:
        bad = {host: route for host, route in NETWORK_ROUTES.items() if route not in ("direct", "proxy")}
        self.assertEqual(bad, {})

    def test_local_mcp_config_matches_direct_set(self) -> None:
        """The gitignored workspace config must render the committed table.

        Skipped silently when the local config does not exist (clean checkout,
        CI): the committed table is the contract, the local file is one
        rendering of it.
        """
        config_path = ROOT / ".zcode" / "config.json"
        if not config_path.exists():
            self.skipTest("no local .zcode/config.json (clean checkout)")
        config = json.loads(config_path.read_text())
        env = config.get("mcp", {}).get("servers", {}).get("real-information-analysis", {}).get("env", {})
        no_proxy = {h.strip() for h in env.get("NO_PROXY", "").split(",") if h.strip()}
        expected_direct = {h for h, route in NETWORK_ROUTES.items() if route == "direct"}
        self.assertEqual(
            no_proxy,
            expected_direct,
            ".zcode/config.json NO_PROXY must equal the 'direct' set of "
            "NETWORK_ROUTES in tests/test_network_routing.py",
        )


if __name__ == "__main__":
    unittest.main()
