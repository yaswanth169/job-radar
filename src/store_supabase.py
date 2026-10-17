"""Supabase (Postgres) store, over the PostgREST API.

Why REST rather than a direct Postgres connection: Supabase's direct
database host is IPv6-only on free projects, and GitHub Actions runners
are IPv4-only. A psycopg connection works on a laptop and then fails in
CI with a confusing timeout. REST needs no new dependency, no connection
pool, and works from anywhere.

Reads and writes go through the service_role key, which bypasses RLS.
The anon key sees nothing, which matters because the repo is public.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set

import requests

from src.models import Job, MatchResult, Verdict

logger = logging.getLogger(__name__)

TIMEOUT = 30
RETRIES = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseStore:
    """Same surface as SqliteStore, backed by Postgres."""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not self.url or not self.key:
            raise ValueError(
                "SupabaseStore needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
            )
        self.rest = f"{self.url}/rest/v1"
        # Cleared the first time Postgres tells us the column is not there.
        self._delivery_column = True
        self._session = requests.Session()
        self._session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        })

    # ---------- transport ----------

    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.rest}/{path}"
        last: Optional[Exception] = None
        for attempt in range(RETRIES):
            try:
                resp = self._session.request(method, url, timeout=TIMEOUT, **kwargs)
                if resp.status_code >= 500:
                    resp.raise_for_status()
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"Supabase {method} {path} -> {resp.status_code}: {resp.text[:300]}"
                    )
                if not resp.content:
                    return None
                try:
                    return resp.json()
                except ValueError:
                    return None
            except (requests.RequestException, OSError) as exc:
                last = exc
                if attempt < RETRIES - 1:
                    import time
                    time.sleep(1.5 * (attempt + 1))
        assert last is not None
        raise last

    # ---------- reads ----------

    def known_verdicts(self, company: Optional[str] = None) -> Dict[str, str]:
        """Map of job_key -> stored verdict for everything already recorded.

        One request per poll. Returning the verdict as well as the key means
        the watcher never needs a follow-up `verdict_of` call per known job -
        which was turning a 3-call poll into an 18-call one.
        """
        path = "jobs?select=job_key,verdict"
        if company:
            path += f"&company=eq.{requests.utils.quote(company)}"
        rows = self._request("GET", path) or []
        return {r["job_key"]: r["verdict"] for r in rows}

    def known_keys(self, company: Optional[str] = None) -> Set[str]:
        return set(self.known_verdicts(company))

    def is_known(self, job_key: str) -> bool:
        rows = self._request(
            "GET", f"jobs?select=job_key&job_key=eq.{requests.utils.quote(job_key)}&limit=1"
        ) or []
        return bool(rows)

    def verdict_of(self, job_key: str) -> Optional[str]:
        rows = self._request(
            "GET", f"jobs?select=verdict&job_key=eq.{requests.utils.quote(job_key)}&limit=1"
        ) or []
        return rows[0]["verdict"] if rows else None

    def pending(self, limit: int = 25) -> List[dict]:
        rows = self._request(
            "GET",
            "jobs?select=payload&notified=is.false"
            f"&verdict=in.({Verdict.APPLY.value},{Verdict.CHECK.value})"
            f"&order=first_seen.asc&limit={limit}",
        ) or []
        out = []
        for r in rows:
            p = r.get("payload")
            if isinstance(p, str):
                p = json.loads(p)
            if p:
                out.append(p)
        return out

    def find(self, needle: str) -> Optional[dict]:
        q = requests.utils.quote(needle)
        rows = self._request("GET", f"jobs?select=payload&job_id=eq.{q}&limit=1") or []
        if not rows:
            rows = self._request("GET", f"jobs?select=payload&job_key=eq.{q}&limit=1") or []
        if not rows:
            return None
        p = rows[0].get("payload")
        return json.loads(p) if isinstance(p, str) else p

    def counts(self) -> dict:
        rows = self._request("GET", "jobs?select=verdict") or []
        out: Dict[str, int] = {}
        for r in rows:
            out[r["verdict"]] = out.get(r["verdict"], 0) + 1
        out["total"] = len(rows)
        return out

    def last_runs(self, limit: int = 10) -> List[dict]:
        return self._request("GET", f"runs?select=*&order=ran_at.desc&limit={limit}") or []

    # ---------- writes ----------

    def record(self, job: Job, match: MatchResult, notified: bool = False) -> None:
        from src.store import _job_dict
        row = {
            "job_key": job.key,
            "company": job.company,
            "job_id": job.job_id,
            "title": job.title,
            "verdict": match.verdict.value,
            "reason": match.reason,
            "experience": match.experience.describe(),
            "location": job.location,
            "apply_url": job.apply_url,
            "payload": _job_dict(job, match),
            "first_seen": _now(),
            "notified": bool(notified),
            "notified_at": _now() if notified else None,
        }
        # merge-duplicates makes this an upsert on the job_key primary key,
        # so a retried poll can never raise on a row that already exists.
        self._request(
            "POST", "jobs",
            data=json.dumps(row),
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )

    def mark_notified(self, job_key: str) -> None:
        self._request(
            "PATCH", f"jobs?job_key=eq.{requests.utils.quote(job_key)}",
            data=json.dumps({"notified": True, "notified_at": _now()}),
            headers={"Prefer": "return=minimal"},
        )

    def log_run(
        self,
        company: str,
        fetched: int = 0,
        new_jobs: int = 0,
        alerted: int = 0,
        enriched: int = 0,
        error: str = "",
        delivery: Optional[dict] = None,
    ) -> None:
        record = {
            "company": company, "ran_at": _now(),
            "fetched": fetched, "new_jobs": new_jobs,
            "enriched": enriched, "alerted": alerted,
            "error": error or None,
        }
        if delivery and self._delivery_column:
            record["delivery"] = delivery
        try:
            self._post_run(record)
        except RuntimeError as exc:
            # The delivery column is added by hand in the dashboard, so the
            # code has to survive running against a table that predates it.
            # Losing one field is acceptable; losing the run record is not.
            if "delivery" not in record or "delivery" not in str(exc):
                raise
            logger.warning("runs.delivery column missing - logging without it")
            self._delivery_column = False
            record.pop("delivery")
            self._post_run(record)

    def _post_run(self, record: dict) -> None:
        self._request(
            "POST", "runs",
            data=json.dumps(record),
            headers={"Prefer": "return=minimal"},
        )

    def prune_runs(self, keep: int = 1000) -> int:
        """Trim run history. 288 rows/day would otherwise grow unbounded."""
        rows = self._request(
            "GET", f"runs?select=id&order=id.desc&limit=1&offset={keep}"
        ) or []
        if not rows:
            return 0
        cutoff = rows[0]["id"]
        self._request("DELETE", f"runs?id=lt.{cutoff}", headers={"Prefer": "return=minimal"})
        return cutoff

    # ---------- misc ----------

    @property
    def db_path(self) -> str:
        return f"{self.url} (Supabase)"

    def ping(self) -> bool:
        self._request("GET", "jobs?select=job_key&limit=1")
        return True
