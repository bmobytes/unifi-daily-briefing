from __future__ import annotations

from typing import Any

import pytest
import requests

from unifi_daily_briefing.analysis import analyze_snapshots, render_markdown
from unifi_daily_briefing.unifi import UniFiClient, UniFiConfig, _normalize_collection


# ---------------------------------------------------------------------------
# Sanitized Cloud Gateway Fiber fixtures.
#
# The official integration API returns paginated wrappers shaped like
# ``{"data": [...], "count": N, "limit": N, "offset": 0, "totalCount": N}``.
# Cloud-managed gateways such as Cloud Gateway Fiber additionally omit several
# endpoints (health, wifi, traffic) and respond with HTTP 404 for them.
# ---------------------------------------------------------------------------


def _paginated(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data": items,
        "count": len(items),
        "limit": max(len(items), 25),
        "offset": 0,
        "totalCount": len(items),
    }


CGF_SITES = _paginated([{"id": "site-uuid", "name": "default"}])
CGF_CLIENTS = _paginated(
    [
        {
            "id": "client-uuid",
            "name": "redacted-laptop",
            "mac": "aa:bb:cc:dd:ee:ff",
            "ap_name": "redacted-ap",
            "rx_bytes": 12_345_678,
            "tx_bytes": 1_234_567,
            "rssi": -55,
        }
    ]
)
CGF_DEVICES = _paginated(
    [
        {
            "id": "device-uuid",
            "name": "redacted-ap",
            "mac": "11:22:33:44:55:66",
            "num_sta": 3,
        }
    ]
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class _FakeSession:
    """Minimal ``requests.Session`` stand-in keyed by URL suffix."""

    def __init__(self, routes: dict[str, _FakeResponse]):
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.verify = True
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        params: dict[str, Any] | None = None,
    ):
        if params:
            encoded = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
            url = f"{url}?{encoded}"
        self.calls.append(url)
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        return _FakeResponse(500, {"error": f"no fake route for {url}"})


def _make_client(session: _FakeSession) -> UniFiClient:
    config = UniFiConfig(
        base_url="https://gateway.example",
        verify_ssl=False,
        auth_mode="api_key",
        username="",
        password="",
        api_key="redacted-api-key",
        site="default",
    )
    client = UniFiClient(config)
    client.session = session  # type: ignore[assignment]
    return client


def test_normalize_collection_handles_paginated_and_bare_payloads():
    items = [{"id": "a"}, {"id": "b"}]
    assert _normalize_collection(_paginated(items)) == items
    assert _normalize_collection(items) == items
    assert _normalize_collection(None) == []
    assert _normalize_collection({"error": "nope"}) == []
    assert _normalize_collection("garbage") == []


def test_collect_snapshot_normalizes_cloud_gateway_fiber_pagination():
    routes = {
        "/proxy/network/integration/v1/sites": _FakeResponse(200, _paginated([{"id": "site-uuid", "name": "Home"}])),
        "/sites/site-uuid/clients": _FakeResponse(200, CGF_CLIENTS),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/health": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/wifi": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/traffic": _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert snapshot["site"] == "site-uuid"
    assert isinstance(snapshot["sites"], list)
    assert snapshot["sites"][0]["id"] == "site-uuid"
    assert isinstance(snapshot["clients"], list)
    assert snapshot["clients"][0]["name"] == "redacted-laptop"
    assert isinstance(snapshot["devices"], list)
    assert snapshot["devices"][0]["name"] == "redacted-ap"
    assert snapshot["health"] == []
    assert snapshot["wifi"] == []
    assert snapshot["traffic"] == []
    assert sorted(snapshot["unavailable_capabilities"]) == ["health", "traffic", "wifi"]


def test_collect_snapshot_keeps_capabilities_when_endpoints_present():
    routes = {
        "/proxy/network/integration/v1/sites": _FakeResponse(200, CGF_SITES),
        "/sites/site-uuid/clients": _FakeResponse(200, CGF_CLIENTS),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/health": _FakeResponse(200, _paginated([{"subsystem": "wlan", "status": "ok"}])),
        "/sites/site-uuid/wifi": _FakeResponse(200, _paginated([{"ssid": "redacted-ssid"}])),
        "/sites/site-uuid/traffic": _FakeResponse(200, _paginated([{"app": "HTTPS", "total_bytes": 999}])),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert snapshot["unavailable_capabilities"] == []
    assert snapshot["health"][0]["subsystem"] == "wlan"
    assert snapshot["wifi"][0]["ssid"] == "redacted-ssid"
    assert snapshot["traffic"][0]["app"] == "HTTPS"


def test_non_404_failure_for_optional_capability_still_raises():
    routes = {
        "/proxy/network/integration/v1/sites": _FakeResponse(200, CGF_SITES),
        "/sites/site-uuid/clients": _FakeResponse(200, CGF_CLIENTS),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/health": _FakeResponse(500, {"error": "boom"}),
    }
    client = _make_client(_FakeSession(routes))

    with pytest.raises(requests.HTTPError):
        client.collect_snapshot()


def test_collect_snapshot_follows_paginated_client_pages():
    client_page_one = {
        "data": [
            {"id": "client-1", "name": "redacted-laptop", "ap_name": "redacted-ap", "rx_bytes": 10, "tx_bytes": 1},
        ],
        "count": 1,
        "limit": 1,
        "offset": 0,
        "totalCount": 2,
    }
    client_page_two = {
        "data": [
            {"id": "client-2", "name": "redacted-phone", "ap_name": "redacted-ap", "rx_bytes": 20, "tx_bytes": 2},
        ],
        "count": 1,
        "limit": 1,
        "offset": 1,
        "totalCount": 2,
    }
    routes = {
        "/proxy/network/integration/v1/sites": _FakeResponse(200, CGF_SITES),
        "/sites/site-uuid/clients": _FakeResponse(200, client_page_one),
        "/sites/site-uuid/clients?limit=1&offset=1": _FakeResponse(200, client_page_two),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/health": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/wifi": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/traffic": _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert [item["id"] for item in snapshot["clients"]] == ["client-1", "client-2"]


def test_findings_and_markdown_flag_unavailable_capabilities():
    snapshots = [
        {
            "payload": {
                "site": "default",
                "sites": [{"id": "site-uuid", "name": "default"}],
                "clients": [
                    {
                        "name": "redacted-laptop",
                        "rx_bytes": 12_345_678,
                        "tx_bytes": 1_234_567,
                        "ap_name": "redacted-ap",
                        "rssi": -55,
                    }
                ],
                "devices": [{"name": "redacted-ap", "num_sta": 3}],
                "health": [],
                "wifi": [],
                "traffic": [],
                "unavailable_capabilities": ["health", "wifi", "traffic"],
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["unavailable_capabilities"] == ["health", "traffic", "wifi"]
    assert any("Cloud-managed gateway did not expose" in rec for rec in findings["recommendations"])

    assert "## Unavailable controller capabilities" in markdown
    assert "`health` endpoint was not exposed" in markdown
    assert "`wifi` endpoint was not exposed" in markdown
    assert "`traffic` endpoint was not exposed" in markdown
    assert "Controller did not expose the traffic capability" in markdown
    assert "Controller did not expose the health capability" in markdown
    assert "UniFi gear looked healthy" not in markdown
    assert "No obvious garbage-fire clients" not in markdown
