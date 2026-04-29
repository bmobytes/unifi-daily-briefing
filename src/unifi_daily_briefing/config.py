from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UDB_", extra="ignore")

    database_path: Path = Path("/data/unifi_daily_briefing.db")
    unifi_base_url: str = ""
    unifi_site: str = "default"
    unifi_verify_ssl: bool = False
    unifi_auth_mode: str = "classic"
    unifi_username: str = ""
    unifi_password: str = ""
    unifi_api_key: str = ""
    unifi_console_id: str = ""

    report_channel_id: str = "1475528008998588647"
    discord_webhook_url: str = ""
    discord_bot_token: str = ""

    brain_reports_dir: str = ""
    sample_cron: str = "*/15 * * * *"
    report_cron: str = "5 8 * * *"
    daily_report_hour: int = Field(default=8, ge=0, le=23)

    ingress_host: str = "unifi-daily-briefing.lab.bartos.media"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
