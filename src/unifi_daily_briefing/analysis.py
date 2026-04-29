from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any


def _client_name(client: dict[str, Any]) -> str:
    return client.get("name") or client.get("hostname") or client.get("mac") or client.get("macAddress") or "unknown-client"


def _has_byte_counters(client: dict[str, Any]) -> bool:
    return any(key in client and client.get(key) is not None for key in ("rx_bytes", "tx_bytes"))


def _bytes_used(client: dict[str, Any]) -> int:
    return int(client.get("rx_bytes", 0)) + int(client.get("tx_bytes", 0))


def _upload_bytes(client: dict[str, Any]) -> int:
    return int(client.get("tx_bytes", 0))


def _download_bytes(client: dict[str, Any]) -> int:
    return int(client.get("rx_bytes", 0))


def _top_bandwidth_clients(
    clients: list[dict[str, Any]], *, limit: int = 5, key: str = "total"
) -> list[dict[str, Any]]:
    measurable = [item for item in clients if _has_byte_counters(item)]
    if key == "upload":
        ordered = sorted(measurable, key=_upload_bytes, reverse=True)
    elif key == "download":
        ordered = sorted(measurable, key=_download_bytes, reverse=True)
    else:
        ordered = sorted(measurable, key=_bytes_used, reverse=True)
    return [
        {
            "name": _client_name(item),
            "download_mb": round(_download_bytes(item) / 1024 / 1024, 1),
            "upload_mb": round(_upload_bytes(item) / 1024 / 1024, 1),
            "ap": item.get("ap_name") or item.get("last_uplink_name") or item.get("essid") or "unknown",
            "rssi": item.get("signal") if item.get("signal") is not None else item.get("rssi"),
            "signal": item.get("signal") if item.get("signal") is not None else item.get("rssi"),
            "retries": item.get("tx_retries") or item.get("retries") or 0,
        }
        for item in ordered[:limit]
    ]


def _wifi_problem_clients(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    noisy = []
    for client in clients:
        signal = client.get("signal")
        rssi = client.get("rssi")
        retries = int(client.get("tx_retries") or client.get("retries") or 0)
        effective_signal = signal if isinstance(signal, (int, float)) else rssi
        weak = isinstance(effective_signal, (int, float)) and effective_signal <= -70
        retry_heavy = retries >= 5000
        if weak or retry_heavy:
            noisy.append(
                {
                    "name": _client_name(client),
                    "signal": effective_signal,
                    "rssi": rssi,
                    "retries": retries,
                    "ap": client.get("ap_name") or client.get("last_uplink_name") or "unknown",
                    "essid": client.get("essid") or "unknown",
                }
            )
    noisy.sort(key=lambda item: (item["signal"] or 0, -(item["retries"] or 0)))
    return noisy[:5]


def _top_dpi(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    dpi = snapshot.get("dpi") or snapshot.get("traffic") or []
    flattened = []
    for item in dpi:
        if isinstance(item, dict):
            flattened.append(
                {
                    "name": item.get("app") or item.get("cat") or item.get("name") or "unknown",
                    "bytes": int(item.get("total_bytes") or item.get("bytes") or item.get("rx_bytes") or 0),
                }
            )
    return sorted(flattened, key=lambda item: item["bytes"], reverse=True)[:8]


def _device_health(snapshot: dict[str, Any]) -> list[str]:
    devices = snapshot.get("devices") or []
    complaints = []
    for device in devices:
        name = device.get("name") or device.get("hostname") or device.get("mac") or device.get("macAddress") or "unknown-device"
        state = device.get("state")
        if state in (0, "disconnected") or device.get("status") == "offline":
            complaints.append(f"{name} is offline")
        elif int(device.get("num_sta", 0)) > 40:
            complaints.append(f"{name} is carrying {device.get('num_sta')} clients, stop dogpiling one AP")
    return complaints[:6]


def _ap_radio_issues(snapshot: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    clients = snapshot.get("clients") or []
    retries_by_ap: defaultdict[str, int] = defaultdict(int)
    weak_by_ap: defaultdict[str, int] = defaultdict(int)
    for client in clients:
        ap_name = client.get("ap_name") or client.get("last_uplink_name") or "unknown"
        retries_by_ap[ap_name] += int(client.get("tx_retries") or client.get("retries") or 0)
        signal = client.get("signal") if client.get("signal") is not None else client.get("rssi")
        if isinstance(signal, (int, float)) and signal <= -70:
            weak_by_ap[ap_name] += 1

    for device in snapshot.get("devices") or []:
        name = device.get("name") or device.get("hostname") or "unknown-device"
        radio_table = device.get("radio_table") or []
        for radio in radio_table:
            if not isinstance(radio, dict):
                continue
            channel = radio.get("channel")
            radio_name = radio.get("name") or radio.get("radio") or "radio"
            if retries_by_ap.get(name, 0) >= 20000:
                issues.append(
                    f"{name} {radio_name} on channel {channel} is attached to retry-happy clients, {retries_by_ap[name]} client retries in the latest sample"
                )
            if weak_by_ap.get(name, 0) >= 2:
                issues.append(
                    f"{name} {radio_name} has {weak_by_ap[name]} weak clients hanging on, channel {channel} needs a reality check"
                )
    return issues[:6]


def _metric_sources(snapshot: dict[str, Any], has_bandwidth_data: bool) -> dict[str, str]:
    summary = snapshot.get("source_summary") or {}
    metric_sources: dict[str, str] = {}
    if summary.get("client_inventory"):
        metric_sources["client_inventory"] = summary["client_inventory"]
    if summary.get("device_inventory"):
        metric_sources["device_inventory"] = summary["device_inventory"]
    if has_bandwidth_data:
        metric_sources["bandwidth"] = summary.get("client_metrics") or "unknown"
    elif summary.get("client_metrics"):
        metric_sources["bandwidth"] = summary["client_metrics"]
    if summary.get("health"):
        metric_sources["health"] = summary["health"]
    if summary.get("wifi_networks"):
        metric_sources["wifi"] = summary["wifi_networks"]
    if summary.get("traffic_usage"):
        metric_sources["traffic"] = summary["traffic_usage"]
    if summary.get("dpi_reference"):
        metric_sources["dpi_reference"] = summary["dpi_reference"]
    if summary.get("ap_radio_metrics"):
        metric_sources["ap_radio_metrics"] = summary["ap_radio_metrics"]
    return metric_sources


def analyze_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    latest = snapshots[-1]["payload"] if snapshots else {}
    clients = latest.get("clients") or []
    top_clients = _top_bandwidth_clients(clients, key="total")
    top_download_clients = _top_bandwidth_clients(clients, key="download")
    top_upload_clients = _top_bandwidth_clients(clients, key="upload")
    problem_clients = _wifi_problem_clients(clients)
    dpi = _top_dpi(latest)
    health = _device_health(latest)
    ap_radio_issues = _ap_radio_issues(latest)
    unavailable = sorted({str(c) for c in (latest.get("unavailable_capabilities") or []) if c})
    has_bandwidth_data = any(_has_byte_counters(client) for client in clients)
    dpi_reference_count = len(latest.get("dpi_applications_reference") or [])
    source_map = _metric_sources(latest, has_bandwidth_data)

    aps = Counter(
        (client.get("ap_name") or client.get("last_uplink_name") or "unknown")
        for client in clients
    )
    busiest_aps = [{"ap": name, "clients": count} for name, count in aps.most_common(5)]

    recommendations = []
    if problem_clients:
        recommendations.append("Your WiFi has at least one client hanging on for dear life. Fix AP placement, band steering, or device pinning before blaming the ISP.")
    if busiest_aps and busiest_aps[0]["clients"] > 25:
        recommendations.append(f"{busiest_aps[0]['ap']} is hoarding clients. Rebalance radios or tweak minimum RSSI so sticky clients get kicked loose.")
    if top_download_clients and top_download_clients[0]["download_mb"] > 10000:
        recommendations.append(f"{top_download_clients[0]['name']} absolutely body-slammed the WAN today. If that was backups, fine. If not, go investigate the bandwidth goblin.")
    if ap_radio_issues:
        recommendations.append("One or more AP radios are soaking up retries or weak clients. The radios section below has names and channels, go bully the worst offender first.")
    if unavailable:
        recommendations.append(
            "Controller capability gaps still exist: "
            + ", ".join(unavailable)
            + ". Some sections below report no data because no available source exposed that metric."
        )
    if clients and not has_bandwidth_data:
        recommendations.append(
            "Available client endpoints still did not expose rx/tx byte counters, so this report cannot rank bandwidth hogs yet."
        )
    if not recommendations:
        recommendations.append("Nothing is on fire. Do not get cocky, it just means the network behaved for one whole day.")

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "top_clients": top_clients,
        "top_download_clients": top_download_clients,
        "top_upload_clients": top_upload_clients,
        "problem_clients": problem_clients,
        "top_dpi": dpi,
        "busiest_aps": busiest_aps,
        "device_health": health,
        "ap_radio_issues": ap_radio_issues,
        "metric_sources": source_map,
        "unavailable_capabilities": unavailable,
        "unavailable_capabilities_by_source": latest.get("unavailable_capabilities_by_source") or {},
        "has_bandwidth_data": has_bandwidth_data,
        "dpi_reference_count": dpi_reference_count,
        "recommendations": recommendations,
        "snapshot_count": len(snapshots),
    }


def render_markdown(report_date: str, findings: dict[str, Any]) -> str:
    lines = [
        f"# UniFi Daily Briefing, {report_date}",
        "",
        "## Network mood",
        findings["recommendations"][0],
        "",
        "## Metric sources",
    ]
    if findings.get("metric_sources"):
        for metric, source in sorted(findings["metric_sources"].items()):
            lines.append(f"- `{metric}`: `{source}`")
    else:
        lines.append("- No source metadata was stored for this snapshot")

    lines.extend(["", "## Top bandwidth clients"])
    if findings["top_clients"]:
        for client in findings["top_clients"]:
            lines.append(
                f"- **{client['name']}**: {client['download_mb']} MB down, {client['upload_mb']} MB up, AP `{client['ap']}`, signal `{client['rssi']}`"
            )
    elif not findings.get("has_bandwidth_data", True):
        lines.append("- Controller client endpoints did not expose byte counters; no bandwidth ranking available")
    else:
        lines.append("- No client data collected")

    lines.extend(["", "## Top download clients"])
    if findings["top_download_clients"]:
        for client in findings["top_download_clients"]:
            lines.append(f"- **{client['name']}**: {client['download_mb']} MB down via `{client['ap']}`")
    else:
        lines.append("- No download ranking available")

    lines.extend(["", "## Top upload clients"])
    if findings["top_upload_clients"]:
        for client in findings["top_upload_clients"]:
            lines.append(f"- **{client['name']}**: {client['upload_mb']} MB up via `{client['ap']}`")
    else:
        lines.append("- No upload ranking available")

    unavailable = findings.get("unavailable_capabilities") or []

    lines.extend(["", "## WiFi improvement targets"])
    if findings["problem_clients"]:
        for client in findings["problem_clients"]:
            lines.append(
                f"- **{client['name']}** on `{client['ap']}` looks crusty, signal `{client['signal']}`, retries `{client['retries']}`, SSID `{client['essid']}`"
            )
    elif "wifi" in unavailable:
        lines.append("- Controller did not expose any WiFi-capable source; no SSID-level summary available")
    else:
        lines.append("- No obvious garbage-fire clients in the latest sample")

    lines.extend(["", "## Busiest APs"])
    for ap in findings["busiest_aps"] or [{"ap": "unknown", "clients": 0}]:
        lines.append(f"- `{ap['ap']}`: {ap['clients']} clients")

    lines.extend(["", "## AP / radio issues"])
    if findings["ap_radio_issues"]:
        for issue in findings["ap_radio_issues"]:
            lines.append(f"- {issue}")
    else:
        lines.append("- No retry-heavy or weak-client AP radio patterns jumped out in the latest sample")

    lines.extend(["", "## Top apps / categories"])
    if findings["top_dpi"]:
        for item in findings["top_dpi"]:
            lines.append(f"- **{item['name']}**: {round(item['bytes'] / 1024 / 1024, 1)} MB")
    elif "traffic" in unavailable and findings.get("dpi_reference_count", 0):
        lines.append("- Controller exposed DPI reference metadata only; no traffic usage counters were available")
    elif "traffic" in unavailable:
        lines.append("- Controller did not expose the traffic capability; no DPI-style breakdown available")
    else:
        lines.append("- DPI data was exposed but the latest sample had no ranked usage to report")

    lines.extend(["", "## Device health"])
    if findings["device_health"]:
        for complaint in findings["device_health"]:
            lines.append(f"- {complaint}")
    elif "health" in unavailable:
        lines.append("- Controller did not expose the health capability; no controller-level health summary available")
    else:
        lines.append("- UniFi gear looked healthy in the sampled data")

    by_source = findings.get("unavailable_capabilities_by_source") or {}
    if unavailable or any(by_source.values()):
        lines.extend(["", "## Unavailable controller capabilities"])
        for capability in unavailable:
            lines.append(f"- Effective report gap: `{capability}` was not exposed by any available source")
        for source_name in sorted(by_source):
            source_capabilities = by_source.get(source_name) or []
            if not source_capabilities:
                continue
            lines.append(f"- `{source_name}` source missing: {', '.join(f'`{item}`' for item in source_capabilities)}")

    lines.extend(["", "## Recommendations"])
    for item in findings["recommendations"]:
        lines.append(f"- {item}")

    return "\n".join(lines)
