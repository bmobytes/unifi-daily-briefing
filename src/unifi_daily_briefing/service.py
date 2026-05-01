from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .analysis import analyze_snapshots, render_markdown
from .config import Settings
from .db import Database
from .delivery import BrainWriter, DiscordDelivery
from .unifi import UniFiClient, UniFiConfig


class BriefingService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.unifi = UniFiClient(
            UniFiConfig(
                base_url=settings.unifi_base_url,
                verify_ssl=settings.unifi_verify_ssl,
                auth_mode=settings.unifi_auth_mode,
                username=settings.unifi_username,
                password=settings.unifi_password,
                api_key=settings.unifi_api_key,
                site=settings.unifi_site,
                console_id=settings.unifi_console_id,
            )
        )
        self.discord = DiscordDelivery(
            webhook_url=settings.discord_webhook_url,
            bot_token=settings.discord_bot_token,
            channel_id=settings.report_channel_id,
        )
        self.brain = BrainWriter(settings.brain_reports_dir)

    def collect(self) -> dict:
        payload = self.unifi.collect_snapshot()
        collected_at = datetime.now(timezone.utc).isoformat()
        snapshot_id = self.db.add_snapshot(collected_at, payload)
        return {"snapshot_id": snapshot_id, "collected_at": collected_at, "payload": payload}

    def generate_report(self, report_date: str | None = None) -> dict:
        now = datetime.now(timezone.utc)
        report_date = report_date or now.date().isoformat()
        since = (now - timedelta(days=1)).isoformat()
        snapshot_count = self.db.count_snapshots_since(since)
        latest_snapshot = self.db.latest_snapshot_since(since)
        if snapshot_count == 0 or latest_snapshot is None:
            self.collect()
            snapshot_count = self.db.count_snapshots_since(since)
            latest_snapshot = self.db.latest_snapshot_since(since)
        snapshots_for_analysis: list[dict] = []
        if latest_snapshot is not None:
            if snapshot_count >= 2:
                earliest_snapshot = self.db.earliest_snapshot_since(since)
                if earliest_snapshot and earliest_snapshot["id"] != latest_snapshot["id"]:
                    snapshots_for_analysis = [earliest_snapshot, latest_snapshot]
                else:
                    snapshots_for_analysis = [latest_snapshot]
            else:
                snapshots_for_analysis = [latest_snapshot]
        findings = analyze_snapshots(snapshots_for_analysis, snapshot_count=snapshot_count)
        markdown = render_markdown(report_date, findings)
        delivered = self.discord.send(markdown) if self.discord.enabled() else False
        written_brain = self.brain.write(report_date, markdown) if self.brain.enabled() else False
        report_id = self.db.add_report(
            report_date=report_date,
            created_at=now.isoformat(),
            title=f"UniFi Daily Briefing, {report_date}",
            markdown=markdown,
            findings=findings,
            delivered_discord=delivered,
            written_brain=written_brain,
        )
        return self.db.get_report(report_id)

    def latest_report(self) -> dict | None:
        return self.db.latest_report()

    def list_reports(self) -> list[dict]:
        return self.db.list_reports()

    def get_report(self, report_id: int) -> dict | None:
        return self.db.get_report(report_id)
