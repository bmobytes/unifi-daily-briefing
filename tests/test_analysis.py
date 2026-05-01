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


def test_bandwidth_window_label_for_single_snapshot():
    snapshots = [
        {
            "payload": {
                "clients": [
                    {
                        "name": "media-box",
                        "rx_bytes": 2_000_000_000,
                        "tx_bytes": 300_000_000,
                        "ap_name": "office-ap",
                    }
                ],
                "devices": [{"name": "office-ap", "num_sta": 1}],
                "source_summary": {"client_inventory": "official"},
            }
        }
    ]

    findings = analyze_snapshots(snapshots)
    markdown = render_markdown("2026-04-29", findings)

    assert findings["bandwidth_window"] == "cumulative"
    assert "latest cumulative counter" in markdown
    assert "only one snapshot in window" in markdown
    assert findings["counter_resets"] == []


def test_bandwidth_delta_uses_normalized_mac_to_match_clients():
    earliest = {
        "payload": {
            "clients": [
                {
                    "name": "media-box-old-name",
                    "mac": "AA-BB-CC-DD-EE-01",
                    "ip": "10.0.0.10",
                    "rx_bytes": 1_000_000_000,
                    "tx_bytes": 100_000_000,
                    "ap_name": "office-ap",
                },
                {
                    "name": "phone",
                    "mac": "aa:bb:cc:dd:ee:02",
                    "ip": "10.0.0.11",
                    "rx_bytes": 50_000_000,
                    "tx_bytes": 5_000_000,
                    "ap_name": "office-ap",
                },
            ],
            "devices": [{"name": "office-ap", "num_sta": 2}],
            "source_summary": {"client_inventory": "official", "client_metrics": "classic"},
        }
    }
    latest = {
        "payload": {
            "clients": [
                {
                    "name": "media-box",
                    "macAddress": "aa:bb:cc:dd:ee:01",
                    "ip": "10.0.0.10",
                    "rx_bytes": 3_000_000_000,
                    "tx_bytes": 400_000_000,
                    "ap_name": "office-ap",
                },
                {
                    "name": "phone",
                    "mac": "AA:BB:CC:DD:EE:02",
                    "ip": "10.0.0.11",
                    "rx_bytes": 100_000_000,
                    "tx_bytes": 10_000_000,
                    "ap_name": "office-ap",
                },
            ],
            "devices": [{"name": "office-ap", "num_sta": 2}],
            "source_summary": {"client_inventory": "official", "client_metrics": "classic"},
        }
    }

    findings = analyze_snapshots([earliest, latest])
    markdown = render_markdown("2026-04-29", findings)

    assert findings["bandwidth_window"] == "daily"
    top = findings["top_clients"][0]
    assert top["name"] == "media-box"
    assert top["mac"] == "aa:bb:cc:dd:ee:01"
    assert top["ip"] == "10.0.0.10"
    assert top["download_mb"] == round(2_000_000_000 / 1024 / 1024, 1)
    assert top["upload_mb"] == round(300_000_000 / 1024 / 1024, 1)
    assert "(daily delta)" in markdown
    assert "MAC `aa:bb:cc:dd:ee:01`" in markdown


def test_bandwidth_delta_falls_back_to_stable_id_when_mac_missing():
    earliest = {
        "payload": {
            "clients": [
                {
                    "_id": "client-abc",
                    "name": "macless-laptop",
                    "rx_bytes": 100_000_000,
                    "tx_bytes": 10_000_000,
                    "ap_name": "office-ap",
                }
            ],
            "devices": [{"name": "office-ap", "num_sta": 1}],
            "source_summary": {"client_inventory": "official"},
        }
    }
    latest = {
        "payload": {
            "clients": [
                {
                    "_id": "client-abc",
                    "name": "macless-laptop-renamed",
                    "rx_bytes": 600_000_000,
                    "tx_bytes": 60_000_000,
                    "ap_name": "office-ap",
                }
            ],
            "devices": [{"name": "office-ap", "num_sta": 1}],
            "source_summary": {"client_inventory": "official"},
        }
    }

    findings = analyze_snapshots([earliest, latest])

    assert findings["bandwidth_window"] == "daily"
    assert len(findings["top_clients"]) == 1
    assert findings["top_clients"][0]["download_mb"] == round(500_000_000 / 1024 / 1024, 1)
    assert findings["top_clients"][0]["mac"] is None


def test_bandwidth_delta_flags_counter_reset_and_excludes_negative_clients():
    earliest = {
        "payload": {
            "clients": [
                {
                    "name": "rebooted-ap-client",
                    "mac": "aa:bb:cc:dd:ee:99",
                    "rx_bytes": 5_000_000_000,
                    "tx_bytes": 500_000_000,
                    "ap_name": "office-ap",
                },
                {
                    "name": "good-client",
                    "mac": "aa:bb:cc:dd:ee:11",
                    "rx_bytes": 100_000_000,
                    "tx_bytes": 10_000_000,
                    "ap_name": "office-ap",
                },
            ],
            "source_summary": {"client_inventory": "official"},
        }
    }
    latest = {
        "payload": {
            "clients": [
                {
                    "name": "rebooted-ap-client",
                    "mac": "aa:bb:cc:dd:ee:99",
                    "ip": "10.0.0.99",
                    "rx_bytes": 100_000,
                    "tx_bytes": 1_000,
                    "ap_name": "office-ap",
                },
                {
                    "name": "good-client",
                    "mac": "aa:bb:cc:dd:ee:11",
                    "rx_bytes": 200_000_000,
                    "tx_bytes": 20_000_000,
                    "ap_name": "office-ap",
                },
            ],
            "source_summary": {"client_inventory": "official"},
        }
    }

    findings = analyze_snapshots([earliest, latest])
    markdown = render_markdown("2026-04-29", findings)

    names = [client["name"] for client in findings["top_clients"]]
    assert "rebooted-ap-client" not in names
    assert "good-client" in names
    reset_macs = [reset["mac"] for reset in findings["counter_resets"]]
    assert "aa:bb:cc:dd:ee:99" in reset_macs
    assert "Counter resets" in markdown
    assert "MAC `aa:bb:cc:dd:ee:99`" in markdown


def test_bandwidth_delta_skips_clients_with_no_baseline():
    earliest = {
        "payload": {
            "clients": [
                {
                    "name": "early-only",
                    "mac": "aa:bb:cc:dd:ee:01",
                    "rx_bytes": 100,
                    "tx_bytes": 100,
                }
            ],
            "source_summary": {"client_inventory": "official"},
        }
    }
    latest = {
        "payload": {
            "clients": [
                {
                    "name": "fresh-client",
                    "mac": "aa:bb:cc:dd:ee:42",
                    "rx_bytes": 9_000_000_000,
                    "tx_bytes": 1_000_000_000,
                }
            ],
            "source_summary": {"client_inventory": "official"},
        }
    }

    findings = analyze_snapshots([earliest, latest])

    assert findings["bandwidth_window"] == "daily"
    assert findings["top_clients"] == []
    assert findings["counter_resets"] == []


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
