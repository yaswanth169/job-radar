"""The watchdog: decide whether the radar is actually working.

Everything else in this project reports what happened. This module is the
only part that notices what *stopped* happening - and that is the failure
the rest cannot see. A dead poller sends no alerts, and no alerts is
exactly what a quiet weekend looks like.

Two faults are worth waking someone for:

  1. A company has not completed a clean pass in hours. The poll is
     scheduled every five minutes, so silence that long is never normal.
  2. A recipient is not receiving. Telegram answers 403 forever once
     someone blocks a bot, and the watcher deliberately treats that as
     resolved so one dead recipient cannot jam the queue for everybody.
     That design is right, and it is precisely why the fault has to be
     reported from somewhere else.

This runs on its own schedule, separate from the poll, because a check
that only runs when the thing it checks is running cannot report that
thing being down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STALE_AFTER_HOURS = 6.0
# How far back to read delivery outcomes. Long enough to catch a bot that
# broke overnight, short enough that a fault fixed yesterday goes quiet.
DELIVERY_WINDOW = 80
# Do not repeat the same complaint every hour while something stays broken.
REALERT_AFTER_HOURS = 12.0
HEARTBEAT_MARKER = "heartbeat"


def _parse(ts: str) -> Optional[datetime]:
    """Postgres and SQLite hand back slightly different ISO strings."""
    if not ts:
        return None
    text = ts.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Health:
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def report(self) -> str:
        lines = ["\U0001F7E2 <b>job-radar</b> — all healthy"] if self.ok else [
            "\U0001F6A8 <b>job-radar</b> — needs attention", ""
        ]
        lines.extend(f"• {p}" for p in self.problems)
        if self.notes:
            lines.append("")
            lines.extend(f"<i>{n}</i>" for n in self.notes)
        return "\n".join(lines)


def _age_phrase(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() // 60)} min ago"
    if hours < 48:
        return f"{hours:.1f} h ago"
    return f"{hours / 24:.1f} days ago"


def check_freshness(rows: List[dict], companies: List[str], now: datetime) -> Health:
    """Has every company completed a clean pass recently?

    A run carrying an error does not count: Qualcomm answering 429 every
    five minutes would otherwise look like a perfectly live radar.
    """
    health = Health()
    cutoff = now - timedelta(hours=STALE_AFTER_HOURS)

    for company in companies:
        good = [r for r in rows
                if r.get("company") == company and not r.get("error")]
        stamps = sorted(filter(None, (_parse(r.get("ran_at", "")) for r in good)))
        if not stamps:
            health.problems.append(f"{company}: no successful pass on record")
            continue
        last = stamps[-1]
        if last < cutoff:
            health.problems.append(
                f"{company}: last clean pass {_age_phrase(now - last)}"
            )
        else:
            health.notes.append(f"{company} last ok {_age_phrase(now - last)}")
    return health


def check_delivery(
    rows: List[dict],
    token_health: Dict[str, str],
    recipients: Optional[List[str]] = None,
) -> Health:
    """Is every recipient still actually receiving?

    Reads the per-recipient outcomes the watcher now persists. A recipient
    with any rejection, or with failures and no success at all, is broken.
    """
    health = Health()
    totals: Dict[str, Dict[str, int]] = {}

    for row in rows[:DELIVERY_WINDOW]:
        for label, tally in (row.get("delivery") or {}).items():
            bucket = totals.setdefault(label, {})
            for status, count in (tally or {}).items():
                bucket[status] = bucket.get(status, 0) + int(count or 0)

    for label in sorted(totals):
        tally = totals[label]
        rejected, failed, ok = (
            tally.get("rejected", 0), tally.get("failed", 0), tally.get("ok", 0)
        )
        if rejected:
            health.problems.append(
                f"{label}: Telegram refused {rejected} alert(s) — "
                f"they are not receiving anything"
            )
        elif failed and not ok:
            health.problems.append(
                f"{label}: {failed} alert(s) could not be delivered"
            )
        elif ok:
            health.notes.append(f"{label} received {ok} recent alert(s)")

    # A revoked token is detectable without sending anything; a blocked
    # chat is not, which is why the outcomes above carry the real weight.
    for label, status in sorted(token_health.items()):
        if status != "ok":
            health.problems.append(f"{label}: bot token is not responding ({status})")

    # Judged from the configured list, not from the tallies: having sent
    # nothing recently is normal, having nobody to send to is not.
    if recipients is not None and not recipients:
        health.problems.append("no Telegram recipients configured")
    elif not totals:
        health.notes.append("no alerts sent recently - delivery unverified")
    return health


def last_alert_at(rows: List[dict]) -> Optional[datetime]:
    stamps = [
        _parse(r.get("ran_at", "")) for r in rows
        if r.get("company") == HEARTBEAT_MARKER and r.get("error")
    ]
    valid = sorted(s for s in stamps if s)
    return valid[-1] if valid else None


def should_alert(rows: List[dict], now: datetime) -> bool:
    """Throttle: stay quiet if we already complained recently."""
    last = last_alert_at(rows)
    return last is None or (now - last) >= timedelta(hours=REALERT_AFTER_HOURS)


def assess(
    rows: List[dict],
    companies: List[str],
    token_health: Dict[str, str],
    now: Optional[datetime] = None,
    recipients: Optional[List[str]] = None,
) -> Health:
    """Full verdict on the system, from run history alone."""
    now = now or datetime.now(timezone.utc)
    combined = Health()
    for part in (check_freshness(rows, companies, now),
                 check_delivery(rows, token_health, recipients)):
        combined.problems.extend(part.problems)
        combined.notes.extend(part.notes)
    return combined
