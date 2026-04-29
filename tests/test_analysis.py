from unifi_daily_briefing.analysis import analyze_snapshots, render_markdown


def test_analyze_and_render():
    snapshots = [
        {
            "payload": {
                "clients": [
                    {"name": "media-box", "rx_bytes": 2_000_000_000, "tx_bytes": 300_000_000, "ap_name": "office-ap", "rssi": -74, "tx_retries": 33},
                    {"name": "phone", "rx_bytes": 400_000_000, "tx_bytes": 50_000_000, "ap_name": "office-ap", "rssi": -58},
                ],
                "devices": [{"name": "office-ap", "num_sta": 28}],
                "dpi": [{"app": "YouTube", "total_bytes": 1_500_000_000}],
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["top_clients"][0]["name"] == "media-box"
    assert findings["problem_clients"][0]["name"] == "media-box"
    assert "bandwidth goblin" in markdown or "WiFi" in markdown
    assert "YouTube" in markdown
