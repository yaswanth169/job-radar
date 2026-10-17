"""One-time: copy the local SQLite state into Supabase.

Run once. Idempotent - rows upsert on job_key, so re-running is safe.
Without this, the first Supabase-backed poll would treat every currently
open job as new and alert on all of them.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(str(Path(__file__).resolve().parent.parent / ".env"))

from src.store_supabase import SupabaseStore  # noqa: E402

LOCAL = Path(__file__).resolve().parent.parent / "data" / "radar.db"


def main() -> int:
    if not LOCAL.exists():
        print(f"No local database at {LOCAL} - nothing to migrate.")
        return 0

    conn = sqlite3.connect(LOCAL)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    print(f"Local SQLite : {len(rows)} jobs")

    store = SupabaseStore()
    before = store.counts()
    print(f"Supabase now : {before}")

    sent = 0
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        record = {
            "job_key": r["job_key"],
            "company": r["company"],
            "job_id": r["job_id"],
            "title": r["title"],
            "verdict": r["verdict"],
            "reason": r["reason"],
            "experience": r["experience"],
            "location": r["location"],
            "apply_url": r["apply_url"],
            "payload": payload,
            "first_seen": r["first_seen"],
            "notified": bool(r["notified"]),
            "notified_at": r["notified_at"],
        }
        store._request(
            "POST", "jobs",
            data=json.dumps(record),
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        sent += 1

    after = store.counts()
    print(f"Pushed       : {sent}")
    print(f"Supabase now : {after}")

    ok = after.get("total") == len(rows)
    print("\nMIGRATION OK" if ok else "\nMISMATCH - check manually")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
