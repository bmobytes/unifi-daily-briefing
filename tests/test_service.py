from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unifi_daily_briefing.config import Settings
from unifi_daily_briefing.service import BriefingService


def test_generate_report_only_deserializes_latest_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(database_path=tmp_path / "test.db")
    service = BriefingService(settings)

    base = datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc)
    old_payload = {
        "clients": [{"name": "old-client", "rx_bytes": 1, "tx_bytes": 1, "ap_name": "old-ap"}],
        "devices": [{"name": "old-ap", "num_sta": 1}],
        "source_summary": {"client_inventory": "official"},
    }
    latest_payload = {
        "clients": [{"name": "latest-client", "rx_bytes": 2, "tx_bytes": 3, "ap_name": "latest-ap"}],
        "devices": [{"name": "latest-ap", "num_sta": 1, "type": "uap", "radio_table": [{"name": "wifi0", "channel": 11}]}],
        "source_summary": {"client_inventory": "official", "client_metrics": "classic"},
    }

    service.db.add_snapshot((base - timedelta(hours=2)).isoformat(), old_payload)
    service.db.add_snapshot((base - timedelta(hours=1)).isoformat(), old_payload)
    service.db.add_snapshot(base.isoformat(), latest_payload)

    original_snapshot_row = service.db._snapshot_row
    calls = {"count": 0}

    def counting_snapshot_row(row):
        calls["count"] += 1
        if calls["count"] > 1:
            raise AssertionError("generate_report deserialized more than the latest snapshot")
        return original_snapshot_row(row)

    monkeypatch.setattr(service.db, "_snapshot_row", counting_snapshot_row)
    monkeypatch.setattr("unifi_daily_briefing.service.datetime", type("FrozenDateTime", (), {
        "now": staticmethod(lambda tz=None: base),
    }))

    report = service.generate_report(report_date="2026-05-01")

    assert report["findings"]["snapshot_count"] == 3
    assert report["findings"]["top_clients"][0]["name"] == "latest-client"
    assert calls["count"] == 1
