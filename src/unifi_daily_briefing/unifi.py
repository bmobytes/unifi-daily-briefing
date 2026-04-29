from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


# Endpoints whose absence on Cloud Gateway Fiber (and similar trimmed-down
# integration APIs) should degrade gracefully rather than abort the snapshot.
OPTIONAL_OFFICIAL_CAPABILITIES = ("health", "wifi", "traffic")


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


@dataclass
class UniFiConfig:
    base_url: str
    verify_ssl: bool
    auth_mode: str
    username: str
    password: str
    api_key: str
    site: str


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

    def _collect_official(self) -> dict[str, Any]:
        headers = {"X-API-KEY": self.config.api_key, "Accept": "application/json"}
        sites = self._get_collection(f"{self.base_url}/proxy/network/integration/v1/sites", headers)
        site = self.config.site
        if site == "default" and sites:
            site = sites[0].get("id") or sites[0].get("name") or site
        prefix = f"{self.base_url}/proxy/network/integration/v1/sites/{site}"

        unavailable: list[str] = []
        snapshot: dict[str, Any] = {
            "site": site,
            "sites": sites,
            "clients": self._get_collection(f"{prefix}/clients", headers),
            "devices": self._get_collection(f"{prefix}/devices", headers),
        }
        for capability in OPTIONAL_OFFICIAL_CAPABILITIES:
            snapshot[capability] = self._get_collection(
                f"{prefix}/{capability}", headers, capability, unavailable
            )
        snapshot["unavailable_capabilities"] = unavailable
        return snapshot

    def _safe_get(self, url: str, headers: dict[str, str], params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(url, headers=headers, timeout=30, params=params)
        response.raise_for_status()
        return response.json()

    def _get_collection(
        self,
        url: str,
        headers: dict[str, str],
        capability: str | None = None,
        unavailable: list[str] | None = None,
    ) -> list[Any]:
        """Collect a full list from the official API, following pagination.

        When ``capability`` is provided, HTTP 404 is treated as a missing
        capability and returns an empty list instead of aborting the snapshot.
        """
        response = self.session.get(url, headers=headers, timeout=30)
        if capability and response.status_code == 404:
            if unavailable is not None:
                unavailable.append(capability)
            return []
        response.raise_for_status()
        payload = response.json()
        items = _normalize_collection(payload)
        if not isinstance(payload, dict) or "data" not in payload:
            return items

        total_count = _int_or_none(payload.get("totalCount"))
        current_offset = _int_or_none(payload.get("offset")) or 0
        page_limit = _int_or_none(payload.get("limit")) or len(items)
        if not total_count or page_limit <= 0 or len(items) >= total_count:
            return items

        collected = list(items)
        next_offset = current_offset + page_limit
        while next_offset < total_count:
            page_payload = self._safe_get(url, headers, params={"offset": next_offset, "limit": page_limit})
            page_items = _normalize_collection(page_payload)
            if not page_items:
                break
            collected.extend(page_items)
            if isinstance(page_payload, dict):
                page_offset = _int_or_none(page_payload.get("offset"))
                page_limit = _int_or_none(page_payload.get("limit")) or page_limit
                next_offset = (page_offset if page_offset is not None else next_offset) + page_limit
            else:
                next_offset += page_limit
        return collected

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
