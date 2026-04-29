from __future__ import annotations

from typing import Any

import pytest
import requests

from unifi_daily_briefing.analysis import analyze_snapshots, render_markdown
from unifi_daily_briefing.unifi import UniFiClient, UniFiConfig, _normalize_collection


def _paginated(items: list[dict[str, Any]], *, limit: int | None = None, offset: int = 0, total: int | None = None) -> dict[str, Any]:
    page_limit = limit or max(len(items), 25)
    return {
        "data": items,
        "count": len(items),
        "limit": page_limit,
        "offset": offset,
        "totalCount": total if total is not None else len(items),
    }


CGF_SITES = _paginated([{"id": "site-uuid", "internalReference": "default", "name": "Home"}])
CGF_CLIENTS = _paginated(
    [
        {
            "id": "client-uuid",
            "name": "redacted-laptop",
            "type": "wireless",
            "connectedAt": "2026-04-29T12:00:00Z",
            "access": {"type": "wifi"},
            "uplinkDeviceId": "ap-uuid",
        }
    ]
)
CGF_CLIENT_DETAIL = {
    "id": "client-uuid",
    "name": "redacted-laptop",
    "type": "wireless",
    "connectedAt": "2026-04-29T12:00:00Z",
    "access": {"type": "wifi"},
    "ipAddress": "192.0.2.44",
    "macAddress": "aa:bb:cc:dd:ee:ff",
    "uplinkDeviceId": "ap-uuid",
}
CGF_DEVICES = _paginated(
    [
        {
            "id": "ap-uuid",
            "name": "Living Room AP",
            "model": "U7 Lite",
            "features": ["accessPoint"],
            "firmwareVersion": "1.0.0",
            "firmwareUpdatable": False,
            "state": "online",
        }
    ]
)
CGF_DEVICE_DETAIL = {
    "id": "ap-uuid",
    "name": "Living Room AP",
    "model": "U7 Lite",
    "features": ["accessPoint"],
    "firmwareVersion": "1.0.0",
    "firmwareUpdatable": False,
    "state": "online",
    "interfaces": {
        "radios": [
            {"wlanStandard": "802.11be", "frequencyGHz": 5, "channelWidthMHz": 80, "channel": 36}
        ]
    },
}
CGF_DEVICE_STATS = {
    "uptimeSec": 12345,
    "cpuUtilizationPct": 14,
    "memoryUtilizationPct": 41,
    "uplink": {"txRateBps": 1000, "rxRateBps": 2000},
    "interfaces": {"radios": [{"frequencyGHz": 5, "txRetriesPct": 12.0}]},
}
DPI_APPLICATIONS = _paginated([{"id": "app-1", "name": "YouTube"}])
DPI_CATEGORIES = _paginated([{"id": "cat-1", "name": "Streaming"}])


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


def _make_client(session: _FakeSession, *, base_url: str = "https://gateway.example", console_id: str = "") -> UniFiClient:
    config = UniFiConfig(
        base_url=base_url,
        verify_ssl=False,
        auth_mode="api_key",
        username="",
        password="",
        api_key="redacted-api-key",
        site="default",
        console_id=console_id,
    )
    client = UniFiClient(config)
    client.session = session  # type: ignore[assignment]
    return client


def _local_routes() -> dict[str, _FakeResponse]:
    return {
        "/proxy/network/integration/v1/sites": _FakeResponse(200, CGF_SITES),
        "/sites/site-uuid/clients": _FakeResponse(200, CGF_CLIENTS),
        "/sites/site-uuid/clients/client-uuid": _FakeResponse(200, CGF_CLIENT_DETAIL),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/devices/ap-uuid": _FakeResponse(200, CGF_DEVICE_DETAIL),
        "/sites/site-uuid/devices/ap-uuid/statistics/latest": _FakeResponse(200, CGF_DEVICE_STATS),
        "/proxy/network/integration/v1/dpi/applications": _FakeResponse(200, DPI_APPLICATIONS),
    }


def _probe_entry(snapshot: dict[str, Any], label: str) -> dict[str, Any]:
    return next(item for item in snapshot["probe_report"]["endpoints"] if item["label"] == label)


def test_normalize_collection_handles_paginated_and_bare_payloads():
    items = [{"id": "a"}, {"id": "b"}]
    assert _normalize_collection(_paginated(items)) == items
    assert _normalize_collection(items) == items
    assert _normalize_collection(None) == []
    assert _normalize_collection({"error": "nope"}) == []
    assert _normalize_collection("garbage") == []


def test_collect_snapshot_enriches_clients_devices_and_probe_report():
    routes = _local_routes() | {
        "/sites/site-uuid/health": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/wifi": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/traffic": _FakeResponse(404, {"error": "not found"}),
        "/proxy/network/integration/v1/dpi/application-categories": _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert snapshot["site"] == "site-uuid"
    assert snapshot["clients"][0]["ipAddress"] == "192.0.2.44"
    assert snapshot["clients"][0]["ap_name"] == "Living Room AP"
    assert snapshot["devices"][0]["interfaces"]["radios"][0]["channel"] == 36
    assert snapshot["devices"][0]["latest_statistics"]["uplink"]["rxRateBps"] == 2000
    assert snapshot["dpi_applications_reference"][0]["name"] == "YouTube"
    assert snapshot["dpi_application_categories_reference"] == []
    assert sorted(snapshot["unavailable_capabilities"]) == ["health", "traffic", "wifi"]
    assert snapshot["probe_report"]["mode"] == "local_controller"
    assert snapshot["probe_report"]["console_id"] is None
    assert _probe_entry(snapshot, "clients")["top_level_fields"] == [
        "access",
        "connectedAt",
        "id",
        "name",
        "type",
        "uplinkDeviceId",
    ]
    assert _probe_entry(snapshot, "clients/client-uuid")["status_code"] == 200
    assert _probe_entry(snapshot, "devices/ap-uuid/statistics/latest")["status_code"] == 200
    assert _probe_entry(snapshot, "dpi/application-categories")["status_code"] == 404


def test_collect_snapshot_keeps_capabilities_when_endpoints_present():
    routes = _local_routes() | {
        "/sites/site-uuid/health": _FakeResponse(200, _paginated([{"subsystem": "wlan", "status": "ok"}])),
        "/sites/site-uuid/wifi": _FakeResponse(200, _paginated([{"ssid": "redacted-ssid"}])),
        "/sites/site-uuid/traffic": _FakeResponse(200, _paginated([{"app": "HTTPS", "total_bytes": 999}])),
        "/proxy/network/integration/v1/dpi/application-categories": _FakeResponse(200, DPI_CATEGORIES),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert snapshot["unavailable_capabilities"] == []
    assert snapshot["health"][0]["subsystem"] == "wlan"
    assert snapshot["wifi"][0]["ssid"] == "redacted-ssid"
    assert snapshot["traffic"][0]["app"] == "HTTPS"
    assert snapshot["dpi_application_categories_reference"][0]["name"] == "Streaming"


def test_non_404_failure_for_optional_capability_still_raises():
    routes = _local_routes() | {
        "/sites/site-uuid/health": _FakeResponse(500, {"error": "boom"}),
        "/sites/site-uuid/wifi": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/traffic": _FakeResponse(404, {"error": "not found"}),
        "/proxy/network/integration/v1/dpi/application-categories": _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(_FakeSession(routes))

    with pytest.raises(requests.HTTPError):
        client.collect_snapshot()


def test_collect_snapshot_follows_paginated_client_pages():
    client_page_one = _paginated(
        [{"id": "client-1", "name": "redacted-laptop", "uplinkDeviceId": "ap-uuid"}],
        limit=1,
        total=2,
    )
    client_page_two = _paginated(
        [{"id": "client-2", "name": "redacted-phone", "uplinkDeviceId": "ap-uuid"}],
        limit=1,
        offset=1,
        total=2,
    )
    routes = {
        "/proxy/network/integration/v1/sites": _FakeResponse(200, CGF_SITES),
        "/sites/site-uuid/clients": _FakeResponse(200, client_page_one),
        "/sites/site-uuid/clients?limit=1&offset=1": _FakeResponse(200, client_page_two),
        "/sites/site-uuid/clients/client-1": _FakeResponse(200, {"id": "client-1", "uplinkDeviceId": "ap-uuid"}),
        "/sites/site-uuid/clients/client-2": _FakeResponse(200, {"id": "client-2", "uplinkDeviceId": "ap-uuid"}),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/devices/ap-uuid": _FakeResponse(200, CGF_DEVICE_DETAIL),
        "/sites/site-uuid/devices/ap-uuid/statistics/latest": _FakeResponse(200, CGF_DEVICE_STATS),
        "/sites/site-uuid/health": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/wifi": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/traffic": _FakeResponse(404, {"error": "not found"}),
        "/proxy/network/integration/v1/dpi/applications": _FakeResponse(200, DPI_APPLICATIONS),
        "/proxy/network/integration/v1/dpi/application-categories": _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert [item["id"] for item in snapshot["clients"]] == ["client-1", "client-2"]
    assert all(item["ap_name"] == "Living Room AP" for item in snapshot["clients"])


def test_remote_connector_discovers_console_id_and_uses_connector_paths():
    routes = {
        "/v1/hosts": _FakeResponse(200, _paginated([{"id": "console-1", "name": "gateway"}])),
        "/v1/connector/consoles/console-1/proxy/network/integration/v1/sites": _FakeResponse(200, CGF_SITES),
        "/sites/site-uuid/clients": _FakeResponse(200, CGF_CLIENTS),
        "/sites/site-uuid/clients/client-uuid": _FakeResponse(200, CGF_CLIENT_DETAIL),
        "/sites/site-uuid/devices": _FakeResponse(200, CGF_DEVICES),
        "/sites/site-uuid/devices/ap-uuid": _FakeResponse(200, CGF_DEVICE_DETAIL),
        "/sites/site-uuid/devices/ap-uuid/statistics/latest": _FakeResponse(200, CGF_DEVICE_STATS),
        "/sites/site-uuid/health": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/wifi": _FakeResponse(404, {"error": "not found"}),
        "/sites/site-uuid/traffic": _FakeResponse(404, {"error": "not found"}),
        "/v1/connector/consoles/console-1/proxy/network/integration/v1/dpi/applications": _FakeResponse(200, DPI_APPLICATIONS),
        "/v1/connector/consoles/console-1/proxy/network/integration/v1/dpi/application-categories": _FakeResponse(404, {"error": "not found"}),
    }
    session = _FakeSession(routes)
    client = _make_client(session, base_url="https://api.ui.com")

    snapshot = client.collect_snapshot()

    assert snapshot["probe_report"]["mode"] == "remote_connector"
    assert snapshot["probe_report"]["console_id"] == "console-1"
    assert any(url.endswith("/v1/hosts") for url in session.calls)
    assert any(
        url.endswith("/v1/connector/consoles/console-1/proxy/network/integration/v1/sites")
        for url in session.calls
    )


def test_findings_and_markdown_flag_unavailable_capabilities():
    snapshots = [
        {
            "payload": {
                "site": "default",
                "sites": [{"id": "site-uuid", "name": "default"}],
                "clients": [
                    {
                        "name": "redacted-laptop",
                        "ap_name": "redacted-ap",
                    }
                ],
                "devices": [{"name": "redacted-ap", "num_sta": 3}],
                "health": [],
                "wifi": [],
                "traffic": [],
                "dpi_applications_reference": [{"id": "app-1", "name": "YouTube"}],
                "unavailable_capabilities": ["health", "wifi", "traffic"],
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["top_clients"] == []
    assert findings["has_bandwidth_data"] is False
    assert findings["dpi_reference_count"] == 1
    assert findings["unavailable_capabilities"] == ["health", "traffic", "wifi"]
    assert any("Cloud-managed gateway did not expose" in rec for rec in findings["recommendations"])
    assert any("did not expose rx/tx byte counters" in rec for rec in findings["recommendations"])

    assert "## Unavailable controller capabilities" in markdown
    assert "`health` endpoint was not exposed" in markdown
    assert "`wifi` endpoint was not exposed" in markdown
    assert "`traffic` endpoint was not exposed" in markdown
    assert "Controller exposed DPI reference metadata only" in markdown
    assert "Controller client endpoints did not expose byte counters" in markdown
    assert "UniFi gear looked healthy" not in markdown
