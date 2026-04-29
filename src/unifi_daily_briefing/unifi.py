from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


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
        sites = self.session.get(
            f"{self.base_url}/proxy/network/integration/v1/sites",
            headers=headers,
            timeout=30,
        ).json()
        site = self.config.site
        if site == "default" and sites:
            site = sites[0].get("name") or sites[0].get("id") or site
        prefix = f"{self.base_url}/proxy/network/integration/v1/sites/{site}"
        return {
            "site": site,
            "sites": sites,
            "clients": self._safe_get(f"{prefix}/clients", headers),
            "devices": self._safe_get(f"{prefix}/devices", headers),
            "health": self._safe_get(f"{prefix}/health", headers),
            "wifi": self._safe_get(f"{prefix}/wifi", headers),
            "traffic": self._safe_get(f"{prefix}/traffic", headers),
        }

    def _safe_get(self, url: str, headers: dict[str, str]) -> Any:
        response = self.session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

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
