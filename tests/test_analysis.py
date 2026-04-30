from unifi_daily_briefing.analysis import analyze_snapshots, render_markdown


def test_analyze_and_render():
    snapshots = [
        {
            "payload": {
                "clients": [
                    {
                        "name": "media-box",
                        "rx_bytes": 2_000_000_000,
                        "tx_bytes": 300_000_000,
                        "ap_name": "office-ap",
                        "signal": -74,
                        "tx_retries": 33_000,
                        "essid": "office-wifi",
                    },
                    {
                        "name": "phone",
                        "rx_bytes": 400_000_000,
                        "tx_bytes": 50_000_000,
                        "ap_name": "office-ap",
                        "signal": -58,
                    },
                ],
                "devices": [
                    {
                        "name": "office-ap",
                        "num_sta": 28,
                        "radio_table": [{"name": "wifi0", "channel": 11}],
                    }
                ],
                "dpi": [{"app": "YouTube", "total_bytes": 1_500_000_000}],
                "source_summary": {
                    "client_inventory": "official",
                    "client_metrics": "classic",
                    "traffic_usage": "classic",
                    "ap_radio_metrics": "classic",
                },
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["top_clients"][0]["name"] == "media-box"
    assert findings["top_download_clients"][0]["name"] == "media-box"
    assert findings["top_upload_clients"][0]["name"] == "media-box"
    assert findings["problem_clients"][0]["name"] == "media-box"
    assert findings["has_bandwidth_data"] is True
    assert findings["metric_sources"]["bandwidth"] == "classic"
    assert "Top upload clients" in markdown
    assert "Metric sources" in markdown
    assert "YouTube" in markdown


def test_analyze_skips_fake_bandwidth_when_counters_are_missing():
    snapshots = [
        {
            "payload": {
                "clients": [{"name": "laptop", "ap_name": "office-ap"}],
                "devices": [{"name": "office-ap", "num_sta": 4}],
                "traffic": [],
                "dpi_applications_reference": [{"id": "app-1", "name": "YouTube"}],
                "source_summary": {"client_inventory": "official", "dpi_reference": "official"},
                "unavailable_capabilities": ["traffic"],
                "unavailable_capabilities_by_source": {"official": ["traffic"], "classic": []},
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["top_clients"] == []
    assert findings["has_bandwidth_data"] is False
    assert findings["dpi_reference_count"] == 1
    assert "no bandwidth ranking available" in markdown
    assert "reference metadata only" in markdown


def test_switches_with_many_wired_clients_do_not_get_ap_advice():
    wired_clients = [
        {"name": f"wired-{idx}", "last_uplink_name": "MSN-LS-USW-AS-01"}
        for idx in range(30)
    ]
    snapshots = [
        {
            "payload": {
                "clients": wired_clients,
                "devices": [
                    {
                        "name": "MSN-LS-USW-AS-01",
                        "type": "usw",
                        "model": "USW-Pro-48",
                        "num_sta": 30,
                        "port_table": [{"port_idx": 1}],
                    },
                    {
                        "name": "office-ap",
                        "type": "uap",
                        "model": "U6-Pro",
                        "num_sta": 3,
                        "radio_table": [{"name": "wifi0", "channel": 11}],
                    },
                ],
                "source_summary": {"client_inventory": "official", "device_inventory": "official"},
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert all(item["ap"] != "MSN-LS-USW-AS-01" for item in findings["busiest_aps"])
    assert "MSN-LS-USW-AS-01 is hoarding clients" not in markdown
    assert "minimum RSSI" not in markdown
    assert "sticky clients" not in markdown
    assert "stop dogpiling one AP" not in markdown
