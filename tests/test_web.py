from pathlib import Path

from fastapi.testclient import TestClient

from unifi_daily_briefing.config import Settings
from unifi_daily_briefing.service import BriefingService
from unifi_daily_briefing.web import create_app


class FakeService(BriefingService):
    def __init__(self, tmp_path: Path):
        super().__init__(Settings(database_path=tmp_path / "test.db"))

    def collect(self):
        payload = {
            "site": "default",
            "clients": [{"name": "laptop", "rx_bytes": 10, "tx_bytes": 5, "ap_name": "office-ap", "rssi": -60}],
            "devices": [{"name": "office-ap", "num_sta": 1}],
            "dpi": [{"app": "HTTPS", "total_bytes": 15}],
        }
        collected_at = "2026-04-29T00:00:00Z"
        snapshot_id = self.db.add_snapshot(collected_at, payload)
        return {"snapshot_id": snapshot_id, "collected_at": collected_at, "payload": payload}


def test_web_endpoints(tmp_path: Path):
    service = FakeService(tmp_path)
    app = create_app(service)
    client = TestClient(app)

    assert client.get("/healthz").json() == {"ok": True}
    collect_response = client.post("/api/collect")
    assert collect_response.status_code == 200

    report_response = client.post("/api/reports/run")
    assert report_response.status_code == 200
    report_id = report_response.json()["id"]

    latest = client.get("/api/reports/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == report_id

    page = client.get("/")
    assert page.status_code == 200
    assert "UniFi Daily Briefing" in page.text
