from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


def _client_name(client: dict[str, Any]) -> str:
    return client.get("name") or client.get("hostname") or client.get("mac") or "unknown-client"


def _has_byte_counters(client: dict[str, Any]) -> bool:
    return any(key in client and client.get(key) is not None for key in ("rx_bytes", "tx_bytes"))


def _bytes_used(client: dict[str, Any]) -> int:
    return int(client.get("rx_bytes", 0)) + int(client.get("tx_bytes", 0))


def _pick_top_clients(clients: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    measurable = [item for item in clients if _has_byte_counters(item)]
    ordered = sorted(measurable, key=_bytes_used, reverse=True)
    return [
        {
            "name": _client_name(item),
            "download_mb": round(int(item.get("rx_bytes", 0)) / 1024 / 1024, 1),
            "upload_mb": round(int(item.get("tx_bytes", 0)) / 1024 / 1024, 1),
            "ap": item.get("ap_name") or item.get("essid") or "unknown",
            "rssi": item.get("rssi"),
            "signal": item.get("signal") or item.get("rssi"),
        }
        for item in ordered[:limit]
    ]


def _wifi_problem_clients(clients: list[dict[str, Any]]) -> list[dict[str, Any]]:
    noisy = []
    for client in clients:
        rssi = client.get("rssi") or client.get("signal")
        retries = client.get("tx_retries") or client.get("retries") or 0
        if (isinstance(rssi, (int, float)) and rssi < -70) or int(retries) > 20:
            noisy.append(
                {
                    "name": _client_name(client),
                    "rssi": rssi,
                    "retries": retries,
                    "ap": client.get("ap_name") or "unknown",
                }
            )
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
        name = device.get("name") or device.get("hostname") or device.get("mac") or "unknown-device"
        if device.get("state") in (0, "disconnected") or device.get("status") == "offline":
            complaints.append(f"{name} is offline")
        elif int(device.get("num_sta", 0)) > 40:
            complaints.append(f"{name} is carrying {device.get('num_sta')} clients, stop dogpiling one AP")
    return complaints[:6]


def analyze_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    latest = snapshots[-1]["payload"] if snapshots else {}
    clients = latest.get("clients") or []
    top_clients = _pick_top_clients(clients)
    problem_clients = _wifi_problem_clients(clients)
    dpi = _top_dpi(latest)
    health = _device_health(latest)
    unavailable = sorted({str(c) for c in (latest.get("unavailable_capabilities") or []) if c})
    has_bandwidth_data = any(_has_byte_counters(client) for client in clients)
    dpi_reference_count = len(latest.get("dpi_applications_reference") or [])

    aps = Counter((client.get("ap_name") or "unknown") for client in clients)
    busiest_aps = [{"ap": name, "clients": count} for name, count in aps.most_common(5)]

    recommendations = []
    if problem_clients:
        recommendations.append("Your WiFi has at least one client hanging on for dear life. Fix AP placement, band steering, or device pinning before blaming the ISP.")
    if busiest_aps and busiest_aps[0]["clients"] > 25:
        recommendations.append(f"{busiest_aps[0]['ap']} is hoarding clients. Rebalance radios or tweak minimum RSSI so sticky clients get kicked loose.")
    if top_clients and top_clients[0]["download_mb"] > 10000:
        recommendations.append(f"{top_clients[0]['name']} absolutely body-slammed the WAN today. If that was backups, fine. If not, go investigate the bandwidth goblin.")
    if unavailable:
        recommendations.append(
            "Cloud-managed gateway did not expose: "
            + ", ".join(unavailable)
            + ". Some sections below report no data because the controller never returned any."
        )
    if clients and not has_bandwidth_data:
        recommendations.append(
            "The official client endpoints did not expose rx/tx byte counters, so this report cannot rank bandwidth hogs yet."
        )
    if not recommendations:
        recommendations.append("Nothing is on fire. Do not get cocky, it just means the network behaved for one whole day.")

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "top_clients": top_clients,
        "problem_clients": problem_clients,
        "top_dpi": dpi,
        "busiest_aps": busiest_aps,
        "device_health": health,
        "unavailable_capabilities": unavailable,
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
        "## Top bandwidth clients",
    ]
    if findings["top_clients"]:
        for client in findings["top_clients"]:
            lines.append(
                f"- **{client['name']}**: {client['download_mb']} MB down, {client['upload_mb']} MB up, AP `{client['ap']}`, RSSI `{client['rssi']}`"
            )
    elif not findings.get("has_bandwidth_data", True):
        lines.append("- Controller client endpoints did not expose byte counters; no bandwidth ranking available")
    else:
        lines.append("- No client data collected")

    unavailable = findings.get("unavailable_capabilities") or []

    lines.extend(["", "## WiFi improvement targets"])
    if findings["problem_clients"]:
        for client in findings["problem_clients"]:
            lines.append(
                f"- **{client['name']}** on `{client['ap']}` looks crusty, RSSI `{client['rssi']}`, retries `{client['retries']}`"
            )
    elif "wifi" in unavailable:
        lines.append("- Controller did not expose the WiFi capability; no SSID-level summary available")
    else:
        lines.append("- No obvious garbage-fire clients in the latest sample")

    lines.extend(["", "## Busiest APs"])
    for ap in findings["busiest_aps"] or [{"ap": "unknown", "clients": 0}]:
        lines.append(f"- `{ap['ap']}`: {ap['clients']} clients")

    lines.extend(["", "## Top apps / categories"])
    if findings["top_dpi"]:
        for item in findings["top_dpi"]:
            lines.append(f"- **{item['name']}**: {round(item['bytes'] / 1024 / 1024, 1)} MB")
    elif "traffic" in unavailable and findings.get("dpi_reference_count", 0):
        lines.append("- Controller exposed DPI reference metadata only; no traffic usage counters were available")
    elif "traffic" in unavailable:
        lines.append("- Controller did not expose the traffic capability; no DPI-style breakdown available")
    else:
        lines.append("- DPI data was not exposed by this controller sample")

    lines.extend(["", "## Device health"])
    if findings["device_health"]:
        for complaint in findings["device_health"]:
            lines.append(f"- {complaint}")
    elif "health" in unavailable:
        lines.append("- Controller did not expose the health capability; no controller-level health summary available")
    else:
        lines.append("- UniFi gear looked healthy in the sampled data")

    if unavailable:
        lines.extend(["", "## Unavailable controller capabilities"])
        for capability in unavailable:
            lines.append(f"- `{capability}` endpoint was not exposed by this controller")

    lines.extend(["", "## Recommendations"])
    for item in findings["recommendations"]:
        lines.append(f"- {item}")

    return "\n".join(lines)
