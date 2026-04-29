# UniFi Daily Briefing

FastAPI service plus CLI for collecting UniFi telemetry, storing snapshots and reports in SQLite, generating spicy daily markdown briefings, exposing a small web UI, and optionally delivering reports to Discord and the Rackshack Brain.

## Features

- Read-only UniFi polling
- Supports classic UniFi OS cookie auth with username/password
- Supports optional official local Integration API key mode
- Supports optional remote `api.ui.com` connector mode with explicit or auto-discovered console ID
- In local API key mode, automatically attempts classic local Network API enrichment when username/password are also present, so official inventory can be combined with richer classic traffic and WiFi counters
- Official API collection enriches client and device inventory with per-item detail lookups, per-device latest statistics, DPI reference probes, and a stored endpoint capability report
- Hybrid snapshots store source metadata plus source-specific capability gaps so reports can say which metrics came from official versus classic
- SQLite persistence on a mounted volume
- Manual report runs plus web UI for latest and historic reports
- Optional Discord delivery by webhook or bot token
- Optional Brain markdown writer when a writable path is mounted
- CLI commands for Kubernetes CronJobs

## Environment

| Variable | Purpose | Default |
| --- | --- | --- |
| `UDB_DATABASE_PATH` | SQLite DB path | `/data/unifi_daily_briefing.db` |
| `UDB_UNIFI_BASE_URL` | UniFi console URL | none |
| `UDB_UNIFI_SITE` | Site name for classic API | `default` |
| `UDB_UNIFI_VERIFY_SSL` | Verify UniFi TLS | `false` |
| `UDB_UNIFI_AUTH_MODE` | `classic` or `api_key` | `classic` |
| `UDB_UNIFI_USERNAME` | Classic API username. In `api_key` mode on a local console URL, presence of both username and password enables additive classic enrichment. | none |
| `UDB_UNIFI_PASSWORD` | Classic API password | none |
| `UDB_UNIFI_API_KEY` | Integration API key | none |
| `UDB_UNIFI_CONSOLE_ID` | Optional remote connector console ID for `https://api.ui.com` mode. If omitted, the collector will try `GET /v1/hosts` with the same API key and use the first host ID it can read. | empty |
| `UDB_REPORT_CHANNEL_ID` | Discord channel ID | `1475528008998588647` |
| `UDB_DISCORD_WEBHOOK_URL` | Discord webhook for direct post | empty |
| `UDB_DISCORD_BOT_TOKEN` | Discord bot token for channel post | empty |
| `UDB_BRAIN_REPORTS_DIR` | Mounted Rackshack Brain report path | empty |
| `UDB_DAILY_REPORT_HOUR` | Intended daily report hour, docs only | `8` |
| `UDB_SAMPLE_CRON` | Collector cron string, docs only | `*/15 * * * *` |
| `UDB_REPORT_CRON` | Report cron string, docs only | `5 8 * * *` |

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn unifi_daily_briefing.web:app --reload
```

Collect once:

```bash
unifi-daily-briefing collect
```

Generate a report:

```bash
unifi-daily-briefing report
```

## Collection modes

- `api_key` with a local console URL: official Integration API inventory plus classic local Network API enrichment when classic credentials are present.
- `api_key` with `https://api.ui.com`: official remote connector mode only.
- `classic`: classic local Network API only.

Stored snapshots include:

- merged client and device records
- per-source capability maps
- per-source unavailable capability lists
- source summaries for inventory, bandwidth, WiFi, health, traffic, and DPI reference data
- probe reports for the active source or sources

## Kubernetes shape

Use one Deployment for the web UI and two CronJobs that run the same image:

- collector cron, every 15 minutes by default
- daily report cron, every morning

All pods share the same SQLite PVC.

Project manifests live in `k8s/` and follow the plain YAML + Kustomize layout used in the bartos-cloud GitOps repo. See `k8s/NOTES.md` for the required secret keys and current deployment blockers.

## Safety

- Collector is read-only. It only performs login plus telemetry reads.
- Discord delivery is skipped unless a webhook or bot token is configured.
- Brain writing is skipped unless `UDB_BRAIN_REPORTS_DIR` is mounted and writable.
