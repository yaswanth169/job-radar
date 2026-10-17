"""Telegram delivery.

Messages are built to a measured length and split before they overflow -
never blind-sliced, which produces malformed HTML and a 400 from Telegram.
"""

from __future__ import annotations

import html
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
GET_ME = "https://api.telegram.org/bot{token}/getMe"
MAX_LEN = 4000

# What became of one message for one person. The distinction that matters
# is REJECTED vs FAILED: a rejection will never be accepted no matter how
# often we try, a failure is worth another go next cycle.
OK = "ok"
REJECTED = "rejected"
FAILED = "failed"

MAX_TITLE = 120
MAX_LOCATION = 80
MAX_EVIDENCE = 220
MAX_SKILLS = 140

BADGE = {"APPLY": "\U0001F7E2", "CHECK": "\U0001F7E1", "SKIP": "\U0001F534"}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _esc(text: str, limit: Optional[int] = None) -> str:
    if limit:
        text = _clip(text, limit)
    return html.escape(text or "")


def format_job(job: Dict) -> str:
    verdict = job.get("verdict", "CHECK")
    badge = BADGE.get(verdict, "")
    lines = [f"{badge} <b>{verdict}</b> · {_esc(job.get('company', ''), 60)}", ""]
    lines.append(f"<b>{_esc(job.get('title', ''), MAX_TITLE)}</b>")

    where = " · ".join(
        p for p in (
            job.get("location", ""),
            job.get("remote_type", ""),
            job.get("employment_type", ""),
        ) if p
    )
    if where:
        lines.append(f"\U0001F4CD {_esc(where, MAX_LOCATION + 40)}")

    lines.append("")
    exp = job.get("experience") or "Not stated"
    lines.append(f"⏳ <b>{_esc(exp, 60)}</b> — {_esc(job.get('reason', ''), 90)}")
    evidence = job.get("experience_evidence")
    if evidence:
        lines.append(f"<i>“{_esc(evidence, MAX_EVIDENCE)}”</i>")

    tags = " · ".join(
        p for p in (job.get("requisition_type", ""), job.get("category", "")) if p
    )
    if tags:
        lines.append(f"\U0001F3F7 {_esc(tags, 90)}")

    matched = job.get("matched_skills") or []
    total = job.get("total_skills") or 0
    if matched:
        lines.append(
            f"\U0001F6E0 You match {len(matched)}/{total}: "
            f"{_esc(', '.join(matched), MAX_SKILLS)}"
        )
    elif total:
        lines.append(f"\U0001F6E0 No overlap with your {total} listed skills")

    meta = " · ".join(
        p for p in (
            f"Posted {job['posted_date']}" if job.get("posted_date") else "",
            f"req {job['job_id']}" if job.get("job_id") else "",
        ) if p
    )
    if meta:
        lines.append(f"\U0001F4C5 {_esc(meta, 60)}")

    apply_url = job.get("apply_url") or job.get("job_url") or ""
    if apply_url:
        lines.append("")
        lines.append(f'\U0001F517 <a href="{html.escape(apply_url, quote=True)}">Apply</a>')
    return "\n".join(lines)


def _safe_trim(text: str, max_len: int = MAX_LEN) -> str:
    """Trim that can never leave a half-written tag behind."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if cut.rfind("<") > cut.rfind(">"):
        cut = cut[: cut.rfind("<")]
    nl = cut.rfind("\n")
    if nl > max_len // 2:
        cut = cut[:nl]
    for tag in ("a", "b", "i"):
        if cut.count(f"<{tag}") > cut.count(f"</{tag}>"):
            cut += f"</{tag}>"
    return cut.rstrip() + "…"


class Recipient:
    """One person: their own bot token and their own chat.

    Each recipient brings their own bot, so the token travels with the
    send instead of living on the Telegram object. That is the whole
    reason this is a class and not just a chat id.
    """

    def __init__(self, label: str, token: str, chat_id: str):
        self.label = label
        self.token = token
        self.chat_id = chat_id

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Recipient {self.label} chat={self.chat_id}>"


def load_recipients() -> List[Recipient]:
    """Read every configured recipient from the environment.

    Recipient 1 is the unsuffixed pair, so an existing single-person
    setup keeps working untouched. Further people are _2, _3, ... and
    the scan stops at the first gap.
    """
    found: List[Recipient] = []
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        found.append(Recipient("primary", token, chat))

    index = 2
    while True:
        token = os.getenv(f"TELEGRAM_BOT_TOKEN_{index}", "").strip()
        chat = os.getenv(f"TELEGRAM_CHAT_ID_{index}", "").strip()
        if not token or not chat:
            break
        found.append(Recipient(f"recipient{index}", token, chat))
        index += 1
    return found


def resolved(outcomes: Dict[str, str]) -> bool:
    """True once nobody is still owed a retry.

    An empty result means nothing was attempted, which is not success.
    """
    return bool(outcomes) and all(s != FAILED for s in outcomes.values())


@dataclass
class Delivery:
    """What happened to a batch of messages, per person.

    `delivered` keeps the old contract - the jobs safe to mark notified.
    `outcomes` is the part that was previously thrown away: who actually
    received them. Without it a recipient whose bot is dead looks exactly
    like one who is reading every alert.
    """

    delivered: List[Dict] = field(default_factory=list)
    outcomes: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def record(self, label: str, status: str) -> None:
        tally = self.outcomes.setdefault(label, {})
        tally[status] = tally.get(status, 0) + 1

    @property
    def unhealthy(self) -> List[str]:
        """Recipients who did not cleanly receive everything."""
        return sorted(
            label for label, tally in self.outcomes.items()
            if tally.get(REJECTED) or tally.get(FAILED)
        )


class Telegram:
    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        dry_run: bool = False,
        recipients: Optional[List[Recipient]] = None,
    ):
        if recipients is not None:
            self.recipients = list(recipients)
        elif token or chat_id:
            self.recipients = [Recipient("primary", token or "", chat_id or "")]
        else:
            self.recipients = load_recipients()
        self.dry_run = dry_run

    @property
    def configured(self) -> bool:
        return any(r.token and r.chat_id for r in self.recipients)

    def _post(self, recipient: Recipient, text: str) -> str:
        """Deliver to one recipient. Returns OK, REJECTED or FAILED.

        REJECTED still counts as resolved: the message will never be
        accepted, so holding the job unsent would re-send it to everyone
        else on every cycle, forever. It is reported separately from OK
        precisely because that silence is otherwise indistinguishable
        from success.
        """
        payload = {
            "chat_id": recipient.chat_id,
            "text": _safe_trim(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        url = API.format(token=recipient.token)
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, timeout=30)
                resp.raise_for_status()
                return OK
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status != 429:
                    body = exc.response.text[:200] if exc.response is not None else ""
                    logger.error(
                        "Telegram permanently rejected the message for %s (%s): %s",
                        recipient.label, status, body,
                    )
                    return REJECTED
                last = exc
            except (requests.RequestException, OSError) as exc:
                last = exc
            time.sleep(1.5 * (attempt + 1))
        logger.warning(
            "Telegram delivery to %s failed, will retry next cycle: %s",
            recipient.label, last,
        )
        return FAILED

    def send(self, text: str) -> Dict[str, str]:
        """Send the same message to every recipient.

        Returns each recipient's outcome. Callers that only care whether
        the job is finished with should use `resolved()` - a job is only
        done once nobody is still owed a retry.
        """
        if self.dry_run:
            print("\n" + "-" * 60)
            print(_strip_html(text))
            print("-" * 60)
            return {r.label: OK for r in self.recipients} or {"dry-run": OK}
        if not self.configured:
            logger.error(
                "Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
            )
            return {}

        outcomes: Dict[str, str] = {}
        for i, recipient in enumerate(self.recipients):
            outcomes[recipient.label] = self._post(recipient, text)
            if i < len(self.recipients) - 1:
                time.sleep(0.3)
        return outcomes

    def send_jobs(self, jobs: List[Dict]) -> Delivery:
        """Send one message per job, reporting what reached whom.

        Each send is isolated, so one failure never abandons the rest -
        and undelivered jobs stay queued for the next cycle.
        """
        report = Delivery()
        for i, job in enumerate(jobs):
            try:
                outcomes = self.send(format_job(job))
                for label, status in outcomes.items():
                    report.record(label, status)
                if resolved(outcomes):
                    report.delivered.append(job)
            except Exception as exc:
                logger.warning(
                    "Telegram send failed for %s (%s) - will retry next cycle: %s",
                    job.get("title"), job.get("job_id"), exc,
                )
                for r in self.recipients:
                    report.record(r.label, FAILED)
            if i < len(jobs) - 1:
                time.sleep(0.5)
        return report

    def send_test(self) -> bool:
        return resolved(self.send(
            "\U0001F7E2 <b>job-radar</b>\nTelegram is wired up correctly."
        ))

    def token_health(self) -> Dict[str, str]:
        """Ask Telegram whether each bot token is still alive.

        A revoked token is the one delivery fault we can detect without
        sending anything. A chat the person has blocked is not: that only
        surfaces on a real send, which is why outcomes are persisted.
        """
        health: Dict[str, str] = {}
        for recipient in self.recipients:
            try:
                resp = requests.get(GET_ME.format(token=recipient.token), timeout=20)
                health[recipient.label] = OK if resp.ok else REJECTED
            except (requests.RequestException, OSError):
                health[recipient.label] = FAILED
        return health


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2: \1", text)
    return re.sub(r"</?[bi]>", "", text)
