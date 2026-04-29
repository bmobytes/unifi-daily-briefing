from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


# Endpoints whose absence on Cloud Gateway Fiber (and similar trimmed-down
# integration APIs) should degrade gracefully rather than abort the snapshot.
OPTIONAL_OFFICIAL_CAPABILITIES = ("health", "wifi", "traffic")

_API_UI_COM_HOST = "api.ui.com"


def _normalize_collection(payload: Any) -> list[Any]:
    """Return a plain list from a UniFi integration-API response.

    The official API may return either a bare list or a paginated wrapper
    shaped like ``{"data": [...], "count": N, "limit": N, "offset": 0,
    "totalCount": N}``. Anything else (None, scalar, unexpected dict) becomes
    an empty list.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
    return []


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_api_ui_com(base_url: str) -> bool:
    return _API_UI_COM_HOST in base_url


@dataclass
class UniFiConfig:
    base_url: str
    verify_ssl: bool
    auth_mode: str
    username: str
    password: str
    api_key: str
    site: str
    console_id: str = ""


class UniFiClient:
    def __init__(self, config: UniFiConfig):
        self.config = config
        self.session = requests.Session()
        self.session.verify = config.verify_ssl
        self.base_url = config.base_url.rstrip("/")

    def collect_snapshot(self) -> dict[str, Any]:
        if self.config.auth_mode == "api_key":
            return self._collect_official()
        self._login_classic()
        site = self.config.site
        return {
            "site": site,
            "clients": self._classic_call(site, "stat/sta") or [],
            "devices": self._classic_call(site, "stat/device") or [],
            "health": self._classic_call(site, "stat/health") or [],
            "dpi": self._classic_call(site, "stat/sitedpi") or [],
            "client_dpi": self._classic_call(site, "stat/stadpi") or [],
            "wan": self._classic_call(site, "stat/widget/wan") or [],
            "wlan": self._classic_call(site, "list/wlanconf") or [],
            "events": self._classic_call(site, "stat/event") or [],
        }

    def _resolve_integration_base(self, console_id: str) -> str:
        """Return the integration API root for local or remote connector mode."""
        if _is_api_ui_com(self.base_url):
            return (
                f"https://{_API_UI_COM_HOST}/v1/connector/consoles"
                f"/{console_id}/proxy/network/integration"
            )
        return f"{self.base_url}/proxy/network/integration"

    def _discover_console_id(
        self, headers: dict[str, str], probe: list[dict[str, Any]]
    ) -> str | None:
        """Attempt GET /v1/hosts to find the first available console ID.

        Returns None and records a probe entry if the call fails (e.g. 401).
        """
        url = f"https://{_API_UI_COM_HOST}/v1/hosts"
        entry: dict[str, Any] = {"label": "discovery/hosts", "url": url}
        try:
            response = self.session.get(url, headers=headers, timeout=30)
            entry["status_code"] = response.status_code
            entry["succeeded"] = response.ok
            if response.ok:
                payload = response.json()
                hosts = _normalize_collection(payload)
                entry["item_count"] = len(hosts)
                if hosts and isinstance(hosts[0], dict):
                    host_id = hosts[0].get("id") or hosts[0].get("hostId")
                    if host_id:
                        entry["discovered_console_id"] = host_id
                        probe.append(entry)
                        return str(host_id)
        except Exception:  # noqa: BLE001
            entry.setdefault("status_code", None)
            entry["succeeded"] = False
        probe.append(entry)
        return None

    def _collect_official(self) -> dict[str, Any]:
        headers = {"X-API-KEY": self.config.api_key, "Accept": "application/json"}
        probe: list[dict[str, Any]] = []

        # Remote connector mode: resolve console ID via config or discovery.
        console_id = self.config.console_id
        if _is_api_ui_com(self.base_url) and not console_id:
            console_id = self._discover_console_id(headers, probe) or ""
        if _is_api_ui_com(self.base_url) and not console_id:
            raise RuntimeError(
                "Remote connector mode: no console ID configured (UDB_UNIFI_CONSOLE_ID)"
                " and discovery via /v1/hosts failed."
                " Check that the API key has host-listing permission."
            )

        integration_base = self._resolve_integration_base(console_id)

        sites = self._probed_collection(
            f"{integration_base}/v1/sites", headers, "sites", probe
        )

        site = self.config.site
        if site == "default" and sites:
            site = sites[0].get("id") or sites[0].get("name") or site
        site_prefix = f"{integration_base}/v1/sites/{site}"

        clients_raw = self._probed_collection(
            f"{site_prefix}/clients", headers, "clients", probe
        )
        devices_raw = self._probed_collection(
            f"{site_prefix}/devices", headers, "devices", probe
        )

        # Build device ID → name map for AP name derivation from uplinkDeviceId.
        device_name_map: dict[str, str] = {
            dev["id"]: dev.get("name") or dev.get("hostname") or dev.get("mac") or dev["id"]
            for dev in devices_raw
            if dev.get("id")
        }

        clients = self._enrich_clients(
            site_prefix, headers, clients_raw, device_name_map, probe
        )
        devices = self._enrich_devices(site_prefix, headers, devices_raw, probe)

        unavailable: list[str] = []
        snapshot: dict[str, Any] = {
            "site": site,
            "sites": sites,
            "clients": clients,
            "devices": devices,
        }
        for capability in OPTIONAL_OFFICIAL_CAPABILITIES:
            snapshot[capability] = self._probed_optional_collection(
                f"{site_prefix}/{capability}", headers, capability, unavailable, probe
            )

        # DPI reference endpoints expose application/category metadata,
        # NOT per-client usage counters.  Store separately so analysis code
        # cannot accidentally treat them as bandwidth data.
        snapshot["dpi_applications_reference"] = self._probed_optional_collection(
            f"{integration_base}/v1/dpi/applications",
            headers, "dpi/applications", None, probe,
        )
        snapshot["dpi_application_categories_reference"] = self._probed_optional_collection(
            f"{integration_base}/v1/dpi/application-categories",
            headers, "dpi/application-categories", None, probe,
        )

        snapshot["unavailable_capabilities"] = unavailable
        snapshot["probe_report"] = {
            "mode": "remote_connector" if _is_api_ui_com(self.base_url) else "local_controller",
            "console_id": console_id or None,
            "endpoints": probe,
        }
        return snapshot

    # ------------------------------------------------------------------
    # Enrichment helpers
    # ------------------------------------------------------------------

    def _enrich_clients(
        self,
        site_prefix: str,
        headers: dict[str, str],
        clients: list[dict[str, Any]],
        device_name_map: dict[str, str],
        probe: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        for client in clients:
            client_id = client.get("id")
            merged = dict(client)
            if client_id:
                detail = self._probed_item(
                    f"{site_prefix}/clients/{client_id}",
                    headers, f"clients/{client_id}", probe,
                )
                if isinstance(detail, dict):
                    merged.update(detail)
            # Derive ap_name from uplinkDeviceId when the list/detail omit it.
            if not merged.get("ap_name") and merged.get("uplinkDeviceId"):
                ap_name = device_name_map.get(merged["uplinkDeviceId"])
                if ap_name:
                    merged["ap_name"] = ap_name
            result.append(merged)
        return result

    def _enrich_devices(
        self,
        site_prefix: str,
        headers: dict[str, str],
        devices: list[dict[str, Any]],
        probe: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        result = []
        for device in devices:
            device_id = device.get("id")
            merged = dict(device)
            if device_id:
                detail = self._probed_item(
                    f"{site_prefix}/devices/{device_id}",
                    headers, f"devices/{device_id}", probe,
                )
                if isinstance(detail, dict):
                    merged.update(detail)
                stats = self._probed_item(
                    f"{site_prefix}/devices/{device_id}/statistics/latest",
                    headers, f"devices/{device_id}/statistics/latest", probe,
                )
                if stats is not None:
                    merged["latest_statistics"] = stats
            result.append(merged)
        return result

    # ------------------------------------------------------------------
    # Probed fetch primitives (record to probe list)
    # ------------------------------------------------------------------

    def _probed_collection(
        self,
        url: str,
        headers: dict[str, str],
        label: str,
        probe: list[dict[str, Any]],
    ) -> list[Any]:
        """Fetch a required collection endpoint and append a probe entry."""
        entry: dict[str, Any] = {"label": label, "url": url}
        response = self.session.get(url, headers=headers, timeout=30)
        entry["status_code"] = response.status_code
        entry["succeeded"] = response.ok
        response.raise_for_status()
        payload = response.json()
        items = _normalize_collection(payload)
        items = self._follow_pagination(url, headers, payload, items)
        entry["item_count"] = len(items)
        if items and isinstance(items[0], dict):
            entry["top_level_fields"] = sorted(items[0].keys())
        probe.append(entry)
        return items

    def _probed_optional_collection(
        self,
        url: str,
        headers: dict[str, str],
        label: str,
        unavailable: list[str] | None,
        probe: list[dict[str, Any]],
    ) -> list[Any]:
        """Fetch an optional collection; 404 → empty list, recorded in probe."""
        entry: dict[str, Any] = {"label": label, "url": url}
        response = self.session.get(url, headers=headers, timeout=30)
        entry["status_code"] = response.status_code
        entry["succeeded"] = response.ok
        if response.status_code == 404:
            entry["item_count"] = 0
            probe.append(entry)
            if unavailable is not None:
                unavailable.append(label)
            return []
        response.raise_for_status()
        payload = response.json()
        items = _normalize_collection(payload)
        items = self._follow_pagination(url, headers, payload, items)
        entry["item_count"] = len(items)
        if items and isinstance(items[0], dict):
            entry["top_level_fields"] = sorted(items[0].keys())
        probe.append(entry)
        return items

    def _probed_item(
        self,
        url: str,
        headers: dict[str, str],
        label: str,
        probe: list[dict[str, Any]],
    ) -> Any:
        """Fetch a single item; any non-OK response → None (best-effort enrichment)."""
        entry: dict[str, Any] = {"label": label, "url": url}
        try:
            response = self.session.get(url, headers=headers, timeout=30)
            entry["status_code"] = response.status_code
            entry["succeeded"] = response.ok
            if not response.ok:
                probe.append(entry)
                return None
            payload = response.json()
            if isinstance(payload, dict):
                entry["top_level_fields"] = sorted(payload.keys())
            elif isinstance(payload, list):
                entry["item_count"] = len(payload)
            probe.append(entry)
            return payload
        except Exception:  # noqa: BLE001
            entry.setdefault("status_code", None)
            entry["succeeded"] = False
            probe.append(entry)
            return None

    def _follow_pagination(
        self,
        url: str,
        headers: dict[str, str],
        payload: Any,
        first_page_items: list[Any],
    ) -> list[Any]:
        """Follow offset-based pagination when the initial response is a paginated wrapper."""
        if not isinstance(payload, dict) or "data" not in payload:
            return first_page_items

        total_count = _int_or_none(payload.get("totalCount"))
        current_offset = _int_or_none(payload.get("offset")) or 0
        page_limit = _int_or_none(payload.get("limit")) or len(first_page_items)
        if not total_count or page_limit <= 0 or len(first_page_items) >= total_count:
            return first_page_items

        collected = list(first_page_items)
        next_offset = current_offset + page_limit
        while next_offset < total_count:
            page_payload = self._safe_get(
                url, headers, params={"offset": next_offset, "limit": page_limit}
            )
            page_items = _normalize_collection(page_payload)
            if not page_items:
                break
            collected.extend(page_items)
            if isinstance(page_payload, dict):
                page_offset = _int_or_none(page_payload.get("offset"))
                page_limit = _int_or_none(page_payload.get("limit")) or page_limit
                next_offset = (
                    page_offset if page_offset is not None else next_offset
                ) + page_limit
            else:
                next_offset += page_limit
        return collected

    def _safe_get(
        self, url: str, headers: dict[str, str], params: dict[str, Any] | None = None
    ) -> Any:
        response = self.session.get(url, headers=headers, timeout=30, params=params)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Classic (cookie-auth) mode
    # ------------------------------------------------------------------

    def _login_classic(self) -> None:
        if not self.config.username or not self.config.password:
            raise ValueError("Classic auth mode requires UniFi username and password")
        payload = {"username": self.config.username, "password": self.config.password, "remember": True}
        paths = ["/api/auth/login", "/api/login"]
        last_error = None
        for path in paths:
            try:
                response = self.session.post(f"{self.base_url}{path}", json=payload, timeout=30)
                if response.ok:
                    token = response.headers.get("x-csrf-token")
                    if token:
                        self.session.headers["X-CSRF-Token"] = token
                    return
                last_error = RuntimeError(f"Login failed at {path}: {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(f"UniFi login failed: {last_error}")

    def _classic_call(self, site: str, suffix: str) -> Any:
        paths = [
            f"/proxy/network/api/s/{site}/{suffix}",
            f"/api/s/{site}/{suffix}",
        ]
        last_error = None
        for path in paths:
            for method in (self.session.get, self.session.post):
                try:
                    response = method(f"{self.base_url}{path}", timeout=30)
                    if not response.ok:
                        last_error = RuntimeError(f"{path} -> {response.status_code}")
                        continue
                    data = response.json()
                    if isinstance(data, dict) and "data" in data:
                        return data["data"]
                    return data
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
        raise RuntimeError(f"UniFi call failed for {suffix}: {last_error}")
