"""SQLite persistence: what we have seen, and what we have delivered.

Two lessons from the previous project are baked in here:

  * A job is only marked `notified` AFTER Telegram confirms delivery.
    Saving first and sending second meant a failed send silenced a job
    forever, because the next run deduped it away.
  * State lives in a plain local file, not a CI cache that can be evicted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.models import Job, MatchResult, Verdict

ROOT = Path(__file__).resolve().parent.parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else ROOT / "data" / "radar.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_key     TEXT PRIMARY KEY,
                    company     TEXT NOT NULL,
                    job_id      TEXT NOT NULL,
                    title       TEXT NOT NULL,
                    verdict     TEXT NOT NULL,
                    reason      TEXT,
                    experience  TEXT,
                    location    TEXT,
                    apply_url   TEXT,
                    payload     TEXT,
                    first_seen  TEXT NOT NULL,
                    notified    INTEGER NOT NULL DEFAULT 0,
                    notified_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    company     TEXT NOT NULL,
                    ran_at      TEXT NOT NULL,
                    fetched     INTEGER,
                    new_jobs    INTEGER,
                    alerted     INTEGER,
                    error       TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_notified ON jobs(notified)")
            conn.commit()

    # ---------- jobs ----------

    def is_known(self, job_key: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM jobs WHERE job_key = ?", (job_key,)
            ).fetchone() is not None

    def verdict_of(self, job_key: str) -> Optional[str]:
        """The verdict recorded when we first judged this job.

        Known jobs are never re-enriched, so re-evaluating them from the
        teaser alone would report a weaker verdict than the one we actually
        acted on. Report what we stored.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT verdict FROM jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
        return row["verdict"] if row else None

    def record(self, job: Job, match: MatchResult, notified: bool = False) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    job_key, company, job_id, title, verdict, reason,
                    experience, location, apply_url, payload, first_seen,
                    notified, notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.key, job.company, job.job_id, job.title,
                    match.verdict.value, match.reason,
                    match.experience.describe(), job.location, job.apply_url,
                    json.dumps(_job_dict(job, match)),
                    _now(), 1 if notified else 0, _now() if notified else None,
                ),
            )
            conn.commit()

    def mark_notified(self, job_key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET notified = 1, notified_at = ? WHERE job_key = ?",
                (_now(), job_key),
            )
            conn.commit()

    def pending(self, limit: int = 25) -> List[dict]:
        """Alertable jobs saved but never delivered. Retried next cycle."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM jobs
                WHERE notified = 0 AND verdict IN (?, ?)
                ORDER BY first_seen
                LIMIT ?
                """,
                (Verdict.APPLY.value, Verdict.CHECK.value, limit),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows if r["payload"]]

    def find(self, needle: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM jobs WHERE job_id = ? OR job_key = ?",
                (needle, needle),
            ).fetchone()
        return json.loads(row["payload"]) if row and row["payload"] else None

    def counts(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM jobs GROUP BY verdict"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        out = {r["verdict"]: r["n"] for r in rows}
        out["total"] = total
        return out

    # ---------- runs ----------

    def log_run(
        self,
        company: str,
        fetched: int = 0,
        new_jobs: int = 0,
        alerted: int = 0,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (company, ran_at, fetched, new_jobs, alerted, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (company, _now(), fetched, new_jobs, alerted, error or None),
            )
            conn.commit()

    def last_runs(self, limit: int = 10) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()


def _job_dict(job: Job, match: MatchResult) -> dict:
    return {
        "company": job.company,
        "job_id": job.job_id,
        "title": job.title,
        "apply_url": job.apply_url,
        "job_url": job.job_url,
        "location": job.location,
        "remote_type": job.remote_type,
        "employment_type": job.employment_type,
        "category": job.category,
        "requisition_type": job.requisition_type,
        "posted_date": job.posted_date,
        "skills": job.skills,
        "verdict": match.verdict.value,
        "reason": match.reason,
        "experience": match.experience.describe(),
        "experience_evidence": match.experience.evidence,
        "matched_skills": match.matched_skills,
        "total_skills": match.total_skills,
    }
