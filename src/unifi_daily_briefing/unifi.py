from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


OPTIONAL_OFFICIAL_CAPABILITIES = ("health", "wifi", "traffic")
CLASSIC_CAPABILITIES = {
    "clients": "stat/sta",
    "devices": "stat/device",
    "health": "stat/health",
    "dpi": "stat/sitedpi",
    "client_dpi": "stat/stadpi",
    "wan": "stat/widget/wan",
    "wlan": "list/wlanconf",
    "events": "stat/event",
}
_REPORT_CAPABILITY_FALLBACKS = {
    "health": (("official", "health"), ("classic", "health")),
    "wifi": (("official", "wifi"), ("classic", "wlan")),
    "traffic": (("official", "traffic"), ("classic", "dpi"), ("classic", "client_dpi")),
}
_API_UI_COM_HOST = "api.ui.com"


def _normalize_collection(payload: Any) -> list[Any]:
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


def _normalize_mac(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", ":")


def _is_missing(value: Any) -> bool:
    return value in (None, "", [], {})


def _has_usable_value(items: list[dict[str, Any]], key: str) -> bool:
    return any(isinstance(item, dict) and not _is_missing(item.get(key)) for item in items)


def _nested_get(mapping: dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


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
            official = self._collect_official()
            if not self._should_attempt_classic_enrichment():
                return official
            try:
                classic = self._collect_classic()
            except Exception as exc:  # noqa: BLE001
                official.setdefault("classic_enrichment", {})
                official["classic_enrichment"] = {
                    "attempted": True,
                    "succeeded": False,
                    "error": str(exc),
                }
                return official
            return self._merge_official_and_classic(official, classic)
        return self._collect_classic()

    def _should_attempt_classic_enrichment(self) -> bool:
        return (
            not _is_api_ui_com(self.base_url)
            and bool(self.config.username)
            and bool(self.config.password)
        )

    def _resolve_integration_base(self, console_id: str) -> str:
        if _is_api_ui_com(self.base_url):
            return (
                f"https://{_API_UI_COM_HOST}/v1/connector/consoles"
                f"/{console_id}/proxy/network/integration"
            )
        return f"{self.base_url}/proxy/network/integration"

    def _discover_console_id(
        self, headers: dict[str, str], probe: list[dict[str, Any]]
    ) -> str | None:
        url = f"https://{_API_UI_COM_HOST}/v1/hosts"
        entry: dict[str, Any] = {"label": "discovery/hosts", "url": url}
        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=30,
                verify=self.config.verify_ssl,
            )
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
        capabilities = {"sites": True, "clients": True, "devices": True}
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
            capabilities[capability] = capability not in unavailable

        snapshot["dpi_applications_reference"] = self._probed_optional_collection(
            f"{integration_base}/v1/dpi/applications",
            headers,
            "dpi/applications",
            None,
            probe,
        )
        snapshot["dpi_application_categories_reference"] = self._probed_optional_collection(
            f"{integration_base}/v1/dpi/application-categories",
            headers,
            "dpi/application-categories",
            None,
            probe,
        )
        capabilities["dpi_applications_reference"] = True
        capabilities["dpi_application_categories_reference"] = True

        snapshot["unavailable_capabilities"] = unavailable
        snapshot["unavailable_capabilities_by_source"] = {"official": unavailable, "classic": []}
        snapshot["capabilities_by_source"] = {"official": capabilities, "classic": {}}
        snapshot["source_summary"] = self._official_source_summary(snapshot)
        snapshot["probe_report"] = {
            "mode": "remote_connector" if _is_api_ui_com(self.base_url) else "local_controller",
            "console_id": console_id or None,
            "endpoints": probe,
        }
        return snapshot

    def _official_source_summary(self, snapshot: dict[str, Any]) -> dict[str, str]:
        summary = {
            "client_inventory": "official",
            "device_inventory": "official",
            "dpi_reference": "official",
        }
        if snapshot.get("health"):
            summary["health"] = "official"
        if snapshot.get("wifi"):
            summary["wifi_networks"] = "official"
        if snapshot.get("traffic"):
            summary["traffic_usage"] = "official"
        if _has_usable_value(snapshot.get("clients") or [], "rx_bytes") or _has_usable_value(
            snapshot.get("clients") or [], "tx_bytes"
        ):
            summary["client_metrics"] = "official"
        if _has_usable_value(snapshot.get("devices") or [], "num_sta"):
            summary["device_metrics"] = "official"
        return summary

    def _merge_official_and_classic(
        self, official: dict[str, Any], classic: dict[str, Any]
    ) -> dict[str, Any]:
        snapshot = dict(official)
        snapshot["clients"] = self._merge_client_records(
            official.get("clients") or [], classic.get("clients") or []
        )
        snapshot["devices"] = self._merge_device_records(
            official.get("devices") or [], classic.get("devices") or []
        )
        snapshot["health"] = classic.get("health") or official.get("health") or []
        snapshot["dpi"] = classic.get("dpi") or []
        snapshot["client_dpi"] = classic.get("client_dpi") or []
        snapshot["wan"] = classic.get("wan") or []
        snapshot["wlan"] = classic.get("wlan") or []
        snapshot["classic_sites"] = classic.get("sites") or []
        snapshot["classic_enrichment"] = {"attempted": True, "succeeded": True}
        snapshot["source_summary"] = self._hybrid_source_summary(snapshot, official, classic)
        snapshot["capabilities_by_source"] = {
            "official": official.get("capabilities_by_source", {}).get("official", {}),
            "classic": classic.get("capabilities_by_source", {}).get("classic", {}),
        }
        snapshot["unavailable_capabilities_by_source"] = {
            "official": official.get("unavailable_capabilities_by_source", {}).get(
                "official", official.get("unavailable_capabilities") or []
            ),
            "classic": classic.get("unavailable_capabilities_by_source", {}).get(
                "classic", classic.get("unavailable_capabilities") or []
            ),
        }
        snapshot["unavailable_capabilities"] = self._effective_report_unavailable(snapshot)
        snapshot["probe_report"] = {
            "mode": "hybrid_local_enrichment",
            "official": official.get("probe_report"),
            "classic": classic.get("probe_report"),
        }
        return snapshot

    def _hybrid_source_summary(
        self,
        snapshot: dict[str, Any],
        official: dict[str, Any],
        classic: dict[str, Any],
    ) -> dict[str, str]:
        summary = dict(official.get("source_summary") or {})
        summary["client_inventory"] = "official"
        summary["device_inventory"] = "official"
        if any(
            not _is_missing(client.get(key))
            for client in classic.get("clients") or []
            for key in ("rx_bytes", "tx_bytes", "signal", "noise", "rssi", "tx_retries", "essid")
        ):
            summary["client_metrics"] = "classic"
        elif summary.get("client_metrics") is None and _has_usable_value(snapshot.get("clients") or [], "rx_bytes"):
            summary["client_metrics"] = "official"
        if classic.get("devices"):
            summary["device_metrics"] = "classic"
        if classic.get("health"):
            summary["health"] = "classic"
        elif official.get("health"):
            summary["health"] = "official"
        if classic.get("wlan"):
            summary["wifi_networks"] = "classic"
        elif official.get("wifi"):
            summary["wifi_networks"] = "official"
        if classic.get("dpi") or classic.get("client_dpi"):
            summary["traffic_usage"] = "classic"
        elif official.get("traffic"):
            summary["traffic_usage"] = "official"
        if classic.get("devices"):
            summary["ap_radio_metrics"] = "classic"
        if classic.get("wan"):
            summary["wan"] = "classic"
        return summary

    def _effective_report_unavailable(self, snapshot: dict[str, Any]) -> list[str]:
        unavailable: list[str] = []
        by_source = snapshot.get("unavailable_capabilities_by_source") or {}
        for report_capability, fallback_paths in _REPORT_CAPABILITY_FALLBACKS.items():
            for source_name, source_capability in fallback_paths:
                source_unavailable = by_source.get(source_name) or []
                if source_capability not in source_unavailable:
                    break
            else:
                unavailable.append(report_capability)
        return unavailable

    def _merge_client_records(
        self,
        official_clients: list[dict[str, Any]],
        classic_clients: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        classic_by_mac = {
            _normalize_mac(client.get("mac")): client
            for client in classic_clients
            if _normalize_mac(client.get("mac"))
        }
        merged: list[dict[str, Any]] = []
        seen_macs: set[str] = set()
        for official in official_clients:
            merged_client = dict(official)
            mac = _normalize_mac(official.get("macAddress") or official.get("mac"))
            classic = classic_by_mac.get(mac)
            if classic:
                seen_macs.add(mac)
                merged_client.update(
                    {
                        key: value
                        for key, value in classic.items()
                        if key not in {"_id", "mac", "name", "hostname"}
                        and not _is_missing(value)
                    }
                )
                merged_client["classic_client_id"] = classic.get("_id")
                if not merged_client.get("ap_name"):
                    merged_client["ap_name"] = classic.get("last_uplink_name")
                if not merged_client.get("mac") and classic.get("mac"):
                    merged_client["mac"] = classic.get("mac")
            merged.append(merged_client)

        for classic in classic_clients:
            mac = _normalize_mac(classic.get("mac"))
            if mac and mac in seen_macs:
                continue
            merged.append(dict(classic))
        return merged

    def _merge_device_records(
        self,
        official_devices: list[dict[str, Any]],
        classic_devices: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        classic_by_mac = {
            _normalize_mac(device.get("mac")): device
            for device in classic_devices
            if _normalize_mac(device.get("mac"))
        }
        classic_by_name = {
            str(device.get("name", "")).strip().lower(): device
            for device in classic_devices
            if str(device.get("name", "")).strip()
        }
        merged: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for official in official_devices:
            merged_device = dict(official)
            mac = _normalize_mac(official.get("macAddress") or official.get("mac"))
            classic = classic_by_mac.get(mac)
            if classic is None:
                classic = classic_by_name.get(str(official.get("name", "")).strip().lower())
            if classic:
                if mac:
                    seen_keys.add(mac)
                elif classic.get("name"):
                    seen_keys.add(str(classic["name"]).strip().lower())
                merged_device.update(
                    {
                        key: value
                        for key, value in classic.items()
                        if key not in {"_id", "mac", "name", "ip"}
                        and not _is_missing(value)
                    }
                )
                merged_device["classic_device_id"] = classic.get("_id")
                if not merged_device.get("mac") and classic.get("mac"):
                    merged_device["mac"] = classic.get("mac")
                if not merged_device.get("ip") and classic.get("ip"):
                    merged_device["ip"] = classic.get("ip")
            merged.append(merged_device)

        for classic in classic_devices:
            mac = _normalize_mac(classic.get("mac"))
            name_key = str(classic.get("name", "")).strip().lower()
            if (mac and mac in seen_keys) or (name_key and name_key in seen_keys):
                continue
            merged.append(dict(classic))
        return merged

    def _collect_classic(self) -> dict[str, Any]:
        self._login_classic()
        probe: list[dict[str, Any]] = []
        capabilities: dict[str, bool] = {}
        unavailable: list[str] = []

        sites = self._classic_collection("/proxy/network/api/self/sites", "sites", probe)
        capabilities["sites"] = True
        site = self._resolve_classic_site(sites)

        snapshot: dict[str, Any] = {"site": site, "sites": sites}
        for capability, suffix in CLASSIC_CAPABILITIES.items():
            items = self._classic_collection(
                f"/proxy/network/api/s/{site}/{suffix}",
                capability,
                probe,
                unavailable,
                allow_404=True,
            )
            snapshot[capability] = items
            capabilities[capability] = capability not in unavailable

        snapshot["unavailable_capabilities"] = unavailable
        snapshot["unavailable_capabilities_by_source"] = {"official": [], "classic": unavailable}
        snapshot["capabilities_by_source"] = {"official": {}, "classic": capabilities}
        snapshot["source_summary"] = self._classic_source_summary(snapshot)
        snapshot["probe_report"] = {
            "mode": "classic_local",
            "console_id": None,
            "endpoints": probe,
        }
        return snapshot

    def _resolve_classic_site(self, sites: list[dict[str, Any]]) -> str:
        if self.config.site != "default":
            return self.config.site
        if not sites:
            return self.config.site
        first = sites[0]
        return str(
            first.get("name")
            or first.get("desc")
            or first.get("internalReference")
            or self.config.site
        )

    def _classic_source_summary(self, snapshot: dict[str, Any]) -> dict[str, str]:
        summary = {
            "client_inventory": "classic",
            "device_inventory": "classic",
            "client_metrics": "classic",
            "device_metrics": "classic",
        }
        if snapshot.get("health"):
            summary["health"] = "classic"
        if snapshot.get("wlan"):
            summary["wifi_networks"] = "classic"
        if snapshot.get("dpi") or snapshot.get("client_dpi"):
            summary["traffic_usage"] = "classic"
        if snapshot.get("wan"):
            summary["wan"] = "classic"
        summary["ap_radio_metrics"] = "classic"
        return summary

    def _classic_collection(
        self,
        path: str,
        label: str,
        probe: list[dict[str, Any]],
        unavailable: list[str] | None = None,
        *,
        allow_404: bool = False,
    ) -> list[Any]:
        entry: dict[str, Any] = {"label": label, "url": f"{self.base_url}{path}"}
        response = self.session.get(
            f"{self.base_url}{path}",
            timeout=30,
            verify=self.config.verify_ssl,
        )
        entry["status_code"] = response.status_code
        entry["succeeded"] = response.ok
        if response.status_code == 404 and allow_404:
            entry["item_count"] = 0
            probe.append(entry)
            if unavailable is not None:
                unavailable.append(label)
            return []
        response.raise_for_status()
        payload = response.json()
        items = _normalize_collection(payload)
        entry["item_count"] = len(items)
        if items and isinstance(items[0], dict):
            entry["top_level_fields"] = sorted(items[0].keys())
        probe.append(entry)
        return items

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
                    headers,
                    f"clients/{client_id}",
                    probe,
                )
                if isinstance(detail, dict):
                    merged.update(detail)
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
                    headers,
                    f"devices/{device_id}",
                    probe,
                )
                if isinstance(detail, dict):
                    merged.update(detail)
                stats = self._probed_item(
                    f"{site_prefix}/devices/{device_id}/statistics/latest",
                    headers,
                    f"devices/{device_id}/statistics/latest",
                    probe,
                )
                if stats is not None:
                    merged["latest_statistics"] = stats
            result.append(merged)
        return result

    def _probed_collection(
        self,
        url: str,
        headers: dict[str, str],
        label: str,
        probe: list[dict[str, Any]],
    ) -> list[Any]:
        entry: dict[str, Any] = {"label": label, "url": url}
        response = self.session.get(
            url,
            headers=headers,
            timeout=30,
            verify=self.config.verify_ssl,
        )
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
        entry: dict[str, Any] = {"label": label, "url": url}
        response = self.session.get(
            url,
            headers=headers,
            timeout=30,
            verify=self.config.verify_ssl,
        )
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
        entry: dict[str, Any] = {"label": label, "url": url}
        try:
            response = self.session.get(
                url,
                headers=headers,
                timeout=30,
                verify=self.config.verify_ssl,
            )
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
        response = self.session.get(
            url,
            headers=headers,
            timeout=30,
            params=params,
            verify=self.config.verify_ssl,
        )
        response.raise_for_status()
        return response.json()

    def _login_classic(self) -> None:
        if not self.config.username or not self.config.password:
            raise ValueError("Classic auth mode requires UniFi username and password")
        payload = {
            "username": self.config.username,
            "password": self.config.password,
            "remember": True,
        }
        paths = ["/api/auth/login", "/api/login"]
        last_error = None
        for path in paths:
            try:
                response = self.session.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    timeout=30,
                    verify=self.config.verify_ssl,
                )
                if response.ok:
                    token = response.headers.get("x-csrf-token")
                    if token:
                        self.session.headers["X-CSRF-Token"] = token
                    return
                last_error = RuntimeError(f"Login failed at {path}: {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(f"UniFi login failed: {last_error}")
