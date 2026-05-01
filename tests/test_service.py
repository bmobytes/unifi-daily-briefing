from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unifi_daily_briefing.config import Settings
from unifi_daily_briefing.service import BriefingService


def test_generate_report_only_deserializes_window_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(database_path=tmp_path / "test.db")
    service = BriefingService(settings)

    base = datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc)
    earliest_payload = {
        "clients": [
            {
                "name": "media-box",
                "mac": "AA:BB:CC:DD:EE:01",
                "ip": "10.0.0.10",
                "rx_bytes": 1_000_000_000,
                "tx_bytes": 100_000_000,
                "ap_name": "office-ap",
            }
        ],
        "devices": [{"name": "office-ap", "num_sta": 1}],
        "source_summary": {"client_inventory": "official"},
    }
    middle_payload = {
        "clients": [
            {
                "name": "media-box",
                "mac": "AA:BB:CC:DD:EE:01",
                "ip": "10.0.0.10",
                "rx_bytes": 1_500_000_000,
                "tx_bytes": 200_000_000,
                "ap_name": "office-ap",
            }
        ],
        "devices": [{"name": "office-ap", "num_sta": 1}],
        "source_summary": {"client_inventory": "official"},
    }
    latest_payload = {
        "clients": [
            {
                "name": "media-box",
                "mac": "AA:BB:CC:DD:EE:01",
                "ip": "10.0.0.10",
                "rx_bytes": 3_000_000_000,
                "tx_bytes": 400_000_000,
                "ap_name": "office-ap",
            }
        ],
        "devices": [
            {
                "name": "office-ap",
                "num_sta": 1,
                "type": "uap",
                "radio_table": [{"name": "wifi0", "channel": 11}],
            }
        ],
        "source_summary": {"client_inventory": "official", "client_metrics": "classic"},
    }

    service.db.add_snapshot((base - timedelta(hours=22)).isoformat(), earliest_payload)
    service.db.add_snapshot((base - timedelta(hours=12)).isoformat(), middle_payload)
    service.db.add_snapshot(base.isoformat(), latest_payload)

    original_snapshot_row = service.db._snapshot_row
    calls = {"count": 0}

    def counting_snapshot_row(row):
        calls["count"] += 1
        if calls["count"] > 2:
            raise AssertionError("generate_report deserialized more than the window endpoints")
        return original_snapshot_row(row)

    monkeypatch.setattr(service.db, "_snapshot_row", counting_snapshot_row)
    monkeypatch.setattr("unifi_daily_briefing.service.datetime", type("FrozenDateTime", (), {
        "now": staticmethod(lambda tz=None: base),
    }))

    report = service.generate_report(report_date="2026-05-01")

    assert report["findings"]["snapshot_count"] == 3
    assert report["findings"]["bandwidth_window"] == "daily"
    top_client = report["findings"]["top_clients"][0]
    assert top_client["name"] == "media-box"
    # Daily delta: 3.0GB - 1.0GB = 2.0GB down; 0.4GB - 0.1GB = 0.3GB up.
    assert top_client["download_mb"] == round(2_000_000_000 / 1024 / 1024, 1)
    assert top_client["upload_mb"] == round(300_000_000 / 1024 / 1024, 1)
    assert top_client["mac"] == "aa:bb:cc:dd:ee:01"
    assert top_client["ip"] == "10.0.0.10"
    assert calls["count"] == 2
