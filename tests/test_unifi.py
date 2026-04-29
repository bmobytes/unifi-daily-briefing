from __future__ import annotations

from typing import Any

import pytest
import requests

from unifi_daily_briefing.analysis import analyze_snapshots, render_markdown
from unifi_daily_briefing.unifi import UniFiClient, UniFiConfig, _normalize_collection


def _paginated(
    items: list[dict[str, Any]], *, limit: int | None = None, offset: int = 0, total: int | None = None
) -> dict[str, Any]:
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
            "macAddress": "11:22:33:44:55:66",
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
    "macAddress": "11:22:33:44:55:66",
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
CLASSIC_SITES = {
    "data": [
        {
            "_id": "classic-site-id",
            "name": "default",
            "desc": "Home",
        }
    ],
    "meta": {"rc": "ok"},
}
CLASSIC_CLIENTS = {
    "data": [
        {
            "_id": "classic-client-1",
            "name": "redacted-laptop",
            "hostname": "redacted-laptop",
            "mac": "aa:bb:cc:dd:ee:ff",
            "signal": -72,
            "rssi": 28,
            "noise": -95,
            "rx_bytes": 1000,
            "tx_bytes": 2000,
            "rx_rate": 300,
            "tx_rate": 400,
            "tx_retries": 7000,
            "essid": "redacted-ssid",
            "last_uplink_name": "Living Room AP",
        }
    ],
    "meta": {"rc": "ok"},
}
CLASSIC_DEVICES = {
    "data": [
        {
            "_id": "classic-device-1",
            "name": "Living Room AP",
            "mac": "11:22:33:44:55:66",
            "type": "uap",
            "num_sta": 14,
            "rx_bytes": 3000,
            "tx_bytes": 5000,
            "satisfaction": 95,
            "radio_table": [
                {"name": "wifi0", "radio": "ng", "channel": 11},
                {"name": "wifi1", "radio": "na", "channel": 36},
            ],
            "uplink": {"uplink_device_name": "Core Switch", "rx_bytes-r": 10, "tx_bytes-r": 20},
        }
    ],
    "meta": {"rc": "ok"},
}
CLASSIC_HEALTH = {
    "data": [{"subsystem": "wlan", "status": "ok", "num_user": 12}],
    "meta": {"rc": "ok"},
}
CLASSIC_DPI = {
    "data": [{"app": "YouTube", "total_bytes": 987654321}],
    "meta": {"rc": "ok"},
}
CLASSIC_CLIENT_DPI = {
    "data": [{"app": "HTTPS", "total_bytes": 1234567}],
    "meta": {"rc": "ok"},
}
CLASSIC_WAN = {"data": [{"latency": 11, "uptime": 1234}], "meta": {"rc": "ok"}}
CLASSIC_WLAN = {"data": [{"name": "redacted-ssid", "enabled": True}], "meta": {"rc": "ok"}}


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class _FakeSession:
    def __init__(self, routes: dict[tuple[str, str], _FakeResponse]):
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.verify = True
        self.calls: list[str] = []
        self.request_kwargs: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        verify: bool | None = None,
    ):
        if params:
            encoded = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
            url = f"{url}?{encoded}"
        self.calls.append(f"{method.upper()} {url}")
        self.request_kwargs.append(
            {
                "method": method.upper(),
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "params": params,
                "json": json,
                "verify": verify,
            }
        )
        for (expected_method, suffix), response in self.routes.items():
            if method.upper() == expected_method and url.endswith(suffix):
                return response
        return _FakeResponse(500, {"error": f"no fake route for {method} {url}"})

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        params: dict[str, Any] | None = None,
        verify: bool | None = None,
    ):
        return self.request(
            "GET",
            url,
            headers=headers,
            timeout=timeout,
            params=params,
            verify=verify,
        )

    def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        verify: bool | None = None,
    ):
        return self.request(
            "POST",
            url,
            headers=headers,
            timeout=timeout,
            params=params,
            json=json,
            verify=verify,
        )


def _make_client(
    session: _FakeSession,
    *,
    base_url: str = "https://gateway.example",
    console_id: str = "",
    auth_mode: str = "api_key",
    username: str = "",
    password: str = "",
) -> UniFiClient:
    config = UniFiConfig(
        base_url=base_url,
        verify_ssl=False,
        auth_mode=auth_mode,
        username=username,
        password=password,
        api_key="redacted-api-key",
        site="default",
        console_id=console_id,
    )
    client = UniFiClient(config)
    client.session = session  # type: ignore[assignment]
    return client


def _local_routes() -> dict[tuple[str, str], _FakeResponse]:
    return {
        ("GET", "/proxy/network/integration/v1/sites"): _FakeResponse(200, CGF_SITES),
        ("GET", "/sites/site-uuid/clients"): _FakeResponse(200, CGF_CLIENTS),
        ("GET", "/sites/site-uuid/clients/client-uuid"): _FakeResponse(200, CGF_CLIENT_DETAIL),
        ("GET", "/sites/site-uuid/devices"): _FakeResponse(200, CGF_DEVICES),
        ("GET", "/sites/site-uuid/devices/ap-uuid"): _FakeResponse(200, CGF_DEVICE_DETAIL),
        ("GET", "/sites/site-uuid/devices/ap-uuid/statistics/latest"): _FakeResponse(200, CGF_DEVICE_STATS),
        ("GET", "/proxy/network/integration/v1/dpi/applications"): _FakeResponse(200, DPI_APPLICATIONS),
    }


def _classic_routes() -> dict[tuple[str, str], _FakeResponse]:
    return {
        ("POST", "/api/auth/login"): _FakeResponse(200, {"ok": True}, headers={"x-csrf-token": "redacted"}),
        ("GET", "/proxy/network/api/self/sites"): _FakeResponse(200, CLASSIC_SITES),
        ("GET", "/proxy/network/api/s/default/stat/sta"): _FakeResponse(200, CLASSIC_CLIENTS),
        ("GET", "/proxy/network/api/s/default/stat/device"): _FakeResponse(200, CLASSIC_DEVICES),
        ("GET", "/proxy/network/api/s/default/stat/health"): _FakeResponse(200, CLASSIC_HEALTH),
        ("GET", "/proxy/network/api/s/default/stat/sitedpi"): _FakeResponse(200, CLASSIC_DPI),
        ("GET", "/proxy/network/api/s/default/stat/stadpi"): _FakeResponse(200, CLASSIC_CLIENT_DPI),
        ("GET", "/proxy/network/api/s/default/stat/widget/wan"): _FakeResponse(200, CLASSIC_WAN),
        ("GET", "/proxy/network/api/s/default/list/wlanconf"): _FakeResponse(200, CLASSIC_WLAN),
        ("GET", "/proxy/network/api/s/default/stat/event"): _FakeResponse(404, {"meta": {"rc": "error"}, "data": []}),
    }


def _probe_entry(snapshot: dict[str, Any], label: str, *, source: str | None = None) -> dict[str, Any]:
    probe = snapshot["probe_report"]
    endpoints = probe["endpoints"] if source is None else probe[source]["endpoints"]
    return next(item for item in endpoints if item["label"] == label)


def test_normalize_collection_handles_paginated_and_bare_payloads():
    items = [{"id": "a"}, {"id": "b"}]
    assert _normalize_collection(_paginated(items)) == items
    assert _normalize_collection(items) == items
    assert _normalize_collection(None) == []
    assert _normalize_collection({"error": "nope"}) == []
    assert _normalize_collection("garbage") == []


def test_collect_snapshot_enriches_clients_devices_and_probe_report():
    routes = _local_routes() | {
        ("GET", "/sites/site-uuid/health"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(404, {"error": "not found"}),
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
    assert snapshot["source_summary"]["client_inventory"] == "official"


def test_collect_snapshot_keeps_capabilities_when_endpoints_present():
    routes = _local_routes() | {
        ("GET", "/sites/site-uuid/health"): _FakeResponse(200, _paginated([{"subsystem": "wlan", "status": "ok"}])),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(200, _paginated([{"ssid": "redacted-ssid"}])),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(200, _paginated([{"app": "HTTPS", "total_bytes": 999}])),
        ("GET", "/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(200, DPI_CATEGORIES),
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
        ("GET", "/sites/site-uuid/health"): _FakeResponse(500, {"error": "boom"}),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(404, {"error": "not found"}),
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
        ("GET", "/proxy/network/integration/v1/sites"): _FakeResponse(200, CGF_SITES),
        ("GET", "/sites/site-uuid/clients"): _FakeResponse(200, client_page_one),
        ("GET", "/sites/site-uuid/clients?limit=1&offset=1"): _FakeResponse(200, client_page_two),
        ("GET", "/sites/site-uuid/clients/client-1"): _FakeResponse(200, {"id": "client-1", "uplinkDeviceId": "ap-uuid"}),
        ("GET", "/sites/site-uuid/clients/client-2"): _FakeResponse(200, {"id": "client-2", "uplinkDeviceId": "ap-uuid"}),
        ("GET", "/sites/site-uuid/devices"): _FakeResponse(200, CGF_DEVICES),
        ("GET", "/sites/site-uuid/devices/ap-uuid"): _FakeResponse(200, CGF_DEVICE_DETAIL),
        ("GET", "/sites/site-uuid/devices/ap-uuid/statistics/latest"): _FakeResponse(200, CGF_DEVICE_STATS),
        ("GET", "/sites/site-uuid/health"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/proxy/network/integration/v1/dpi/applications"): _FakeResponse(200, DPI_APPLICATIONS),
        ("GET", "/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(_FakeSession(routes))

    snapshot = client.collect_snapshot()

    assert [item["id"] for item in snapshot["clients"]] == ["client-1", "client-2"]
    assert all(item["ap_name"] == "Living Room AP" for item in snapshot["clients"])


def test_remote_connector_discovers_console_id_and_uses_connector_paths():
    routes = {
        ("GET", "/v1/hosts"): _FakeResponse(200, _paginated([{"id": "console-1", "name": "gateway"}])),
        ("GET", "/v1/connector/consoles/console-1/proxy/network/integration/v1/sites"): _FakeResponse(200, CGF_SITES),
        ("GET", "/sites/site-uuid/clients"): _FakeResponse(200, CGF_CLIENTS),
        ("GET", "/sites/site-uuid/clients/client-uuid"): _FakeResponse(200, CGF_CLIENT_DETAIL),
        ("GET", "/sites/site-uuid/devices"): _FakeResponse(200, CGF_DEVICES),
        ("GET", "/sites/site-uuid/devices/ap-uuid"): _FakeResponse(200, CGF_DEVICE_DETAIL),
        ("GET", "/sites/site-uuid/devices/ap-uuid/statistics/latest"): _FakeResponse(200, CGF_DEVICE_STATS),
        ("GET", "/sites/site-uuid/health"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/v1/connector/consoles/console-1/proxy/network/integration/v1/dpi/applications"): _FakeResponse(200, DPI_APPLICATIONS),
        ("GET", "/v1/connector/consoles/console-1/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(404, {"error": "not found"}),
    }
    session = _FakeSession(routes)
    client = _make_client(session, base_url="https://api.ui.com")

    snapshot = client.collect_snapshot()

    assert snapshot["probe_report"]["mode"] == "remote_connector"
    assert snapshot["probe_report"]["console_id"] == "console-1"
    assert any(call.endswith("/v1/hosts") for call in session.calls)
    assert any(
        call.endswith("/v1/connector/consoles/console-1/proxy/network/integration/v1/sites")
        for call in session.calls
    )


def test_client_passes_verify_ssl_flag_on_every_request():
    routes = _local_routes() | _classic_routes() | {
        ("GET", "/sites/site-uuid/health"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(404, {"error": "not found"}),
    }
    session = _FakeSession(routes)
    client = _make_client(
        session,
        auth_mode="hybrid",
        username="redacted-user",
        password="redacted-pass",
    )

    client.collect_snapshot()

    assert session.request_kwargs
    assert all(item["verify"] is False for item in session.request_kwargs)


def test_hybrid_collection_merges_classic_metrics_into_official_inventory():
    routes = _local_routes() | _classic_routes() | {
        ("GET", "/sites/site-uuid/health"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/wifi"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/sites/site-uuid/traffic"): _FakeResponse(404, {"error": "not found"}),
        ("GET", "/proxy/network/integration/v1/dpi/application-categories"): _FakeResponse(404, {"error": "not found"}),
    }
    client = _make_client(
        _FakeSession(routes),
        auth_mode="api_key",
        username="classic-user",
        password="classic-pass",
    )

    snapshot = client.collect_snapshot()

    merged_client = snapshot["clients"][0]
    merged_device = snapshot["devices"][0]

    assert snapshot["probe_report"]["mode"] == "hybrid_local_enrichment"
    assert snapshot["health"][0]["subsystem"] == "wlan"
    assert snapshot["dpi"][0]["app"] == "YouTube"
    assert snapshot["wlan"][0]["name"] == "redacted-ssid"
    assert snapshot["unavailable_capabilities"] == []
    assert snapshot["unavailable_capabilities_by_source"]["official"] == ["health", "wifi", "traffic"]
    assert snapshot["unavailable_capabilities_by_source"]["classic"] == ["events"]
    assert snapshot["source_summary"]["client_inventory"] == "official"
    assert snapshot["source_summary"]["client_metrics"] == "classic"
    assert snapshot["source_summary"]["traffic_usage"] == "classic"
    assert snapshot["source_summary"]["dpi_reference"] == "official"
    assert merged_client["ipAddress"] == "192.0.2.44"
    assert merged_client["rx_bytes"] == 1000
    assert merged_client["tx_bytes"] == 2000
    assert merged_client["signal"] == -72
    assert merged_client["ap_name"] == "Living Room AP"
    assert merged_device["latest_statistics"]["uplink"]["rxRateBps"] == 2000
    assert merged_device["num_sta"] == 14
    assert merged_device["radio_table"][0]["channel"] == 11
    assert _probe_entry(snapshot, "clients", source="official")["status_code"] == 200
    assert _probe_entry(snapshot, "events", source="classic")["status_code"] == 404


def test_classic_only_collection_uses_local_network_api():
    client = _make_client(
        _FakeSession(_classic_routes()),
        auth_mode="classic",
        username="classic-user",
        password="classic-pass",
    )

    snapshot = client.collect_snapshot()

    assert snapshot["probe_report"]["mode"] == "classic_local"
    assert snapshot["site"] == "default"
    assert snapshot["clients"][0]["rx_bytes"] == 1000
    assert snapshot["devices"][0]["num_sta"] == 14
    assert snapshot["unavailable_capabilities_by_source"]["classic"] == ["events"]


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
                "source_summary": {"client_inventory": "official", "dpi_reference": "official"},
                "unavailable_capabilities": ["health", "wifi", "traffic"],
                "unavailable_capabilities_by_source": {"official": ["health", "wifi", "traffic"], "classic": []},
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["top_clients"] == []
    assert findings["has_bandwidth_data"] is False
    assert findings["dpi_reference_count"] == 1
    assert findings["unavailable_capabilities"] == ["health", "traffic", "wifi"]
    assert any("capability gaps still exist" in rec for rec in findings["recommendations"])
    assert any("did not expose rx/tx byte counters" in rec for rec in findings["recommendations"])
    assert "## Metric sources" in markdown
    assert "Unavailable controller capabilities" in markdown
    assert "`official` source missing" in markdown
    assert "reference metadata only" in markdown
