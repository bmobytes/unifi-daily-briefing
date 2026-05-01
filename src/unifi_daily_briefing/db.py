import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collected_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    delivered_discord INTEGER NOT NULL DEFAULT 0,
    written_brain INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def add_snapshot(self, collected_at: str, payload: dict) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (collected_at, payload) VALUES (?, ?)",
                (collected_at, json.dumps(payload)),
            )
            return int(cur.lastrowid)

    def list_snapshots_since(self, iso_ts: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE collected_at >= ? ORDER BY collected_at ASC",
                (iso_ts,),
            ).fetchall()
        return [self._snapshot_row(row) for row in rows]

    def count_snapshots_since(self, iso_ts: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM snapshots WHERE collected_at >= ?",
                (iso_ts,),
            ).fetchone()
        return int(row[0]) if row else 0

    def latest_snapshot_since(self, iso_ts: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE collected_at >= ? ORDER BY collected_at DESC LIMIT 1",
                (iso_ts,),
            ).fetchone()
        return self._snapshot_row(row) if row else None

    def earliest_snapshot_since(self, iso_ts: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE collected_at >= ? ORDER BY collected_at ASC LIMIT 1",
                (iso_ts,),
            ).fetchone()
        return self._snapshot_row(row) if row else None

    def latest_snapshot(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM snapshots ORDER BY collected_at DESC LIMIT 1").fetchone()
        return self._snapshot_row(row) if row else None

    def add_report(
        self,
        report_date: str,
        created_at: str,
        title: str,
        markdown: str,
        findings: dict,
        delivered_discord: bool,
        written_brain: bool,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO reports (
                    report_date, created_at, title, markdown, findings_json,
                    delivered_discord, written_brain
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_date,
                    created_at,
                    title,
                    markdown,
                    json.dumps(findings),
                    int(delivered_discord),
                    int(written_brain),
                ),
            )
            return int(cur.lastrowid)

    def list_reports(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
        return [self._report_row(row) for row in rows]

    def latest_report(self) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._report_row(row) if row else None

    def get_report(self, report_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return self._report_row(row) if row else None

    @staticmethod
    def _snapshot_row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "collected_at": row["collected_at"],
            "payload": json.loads(row["payload"]),
        }

    @staticmethod
    def _report_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "report_date": row["report_date"],
            "created_at": row["created_at"],
            "title": row["title"],
            "markdown": row["markdown"],
            "findings": json.loads(row["findings_json"]),
            "delivered_discord": bool(row["delivered_discord"]),
            "written_brain": bool(row["written_brain"]),
        }
