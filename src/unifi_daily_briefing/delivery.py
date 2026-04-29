from __future__ import annotations

from datetime import date
from pathlib import Path

import requests


class DiscordDelivery:
    def __init__(self, webhook_url: str = "", bot_token: str = "", channel_id: str = ""):
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel_id = channel_id

    def enabled(self) -> bool:
        return bool(self.webhook_url or (self.bot_token and self.channel_id))

    def send(self, content: str) -> bool:
        if self.webhook_url:
            response = requests.post(self.webhook_url, json={"content": content[:1900]}, timeout=30)
            response.raise_for_status()
            return True
        if self.bot_token and self.channel_id:
            response = requests.post(
                f"https://discord.com/api/v10/channels/{self.channel_id}/messages",
                headers={"Authorization": f"Bot {self.bot_token}"},
                json={"content": content[:1900]},
                timeout=30,
            )
            response.raise_for_status()
            return True
        return False


class BrainWriter:
    def __init__(self, root: str = ""):
        self.root = Path(root) if root else None

    def enabled(self) -> bool:
        return bool(self.root)

    def write(self, report_date: str, markdown: str) -> bool:
        if not self.root:
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{date.fromisoformat(report_date).isoformat()}-unifi-daily-briefing.md"
        path.write_text(markdown + "\n", encoding="utf-8")
        return True
