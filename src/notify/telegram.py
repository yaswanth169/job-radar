"""Telegram delivery.

Messages are built to a measured length and split before they overflow -
never blind-sliced, which produces malformed HTML and a 400 from Telegram.
"""

from __future__ import annotations

import html
import logging
import os
import time
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000

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


class Telegram:
    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.dry_run = dry_run

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if self.dry_run:
            print("\n" + "-" * 60)
            print(_strip_html(text))
            print("-" * 60)
            return True
        if not self.configured:
            logger.error("Telegram not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": _safe_trim(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = requests.post(API.format(token=self.token), json=payload, timeout=30)
                resp.raise_for_status()
                return True
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status is not None and 400 <= status < 500 and status != 429:
                    body = exc.response.text[:200] if exc.response is not None else ""
                    logger.error("Telegram rejected the message (%s): %s", status, body)
                    raise
                last = exc
            except (requests.RequestException, OSError) as exc:
                last = exc
            time.sleep(1.5 * (attempt + 1))
        if last:
            raise last
        return False

    def send_jobs(self, jobs: List[Dict]) -> List[Dict]:
        """Send one message per job. Returns only what was delivered.

        Each send is isolated, so one failure never abandons the rest -
        and undelivered jobs stay queued for the next cycle.
        """
        delivered: List[Dict] = []
        for i, job in enumerate(jobs):
            try:
                if self.send(format_job(job)):
                    delivered.append(job)
            except Exception as exc:
                logger.warning(
                    "Telegram send failed for %s (%s) - will retry next cycle: %s",
                    job.get("title"), job.get("job_id"), exc,
                )
            if i < len(jobs) - 1:
                time.sleep(0.5)
        return delivered

    def send_test(self) -> bool:
        return self.send(
            "\U0001F7E2 <b>job-radar</b>\nTelegram is wired up correctly."
        )


def _strip_html(text: str) -> str:
    import re
    text = re.sub(r'<a href="([^"]+)">([^<]*)</a>', r"\2: \1", text)
    return re.sub(r"</?[bi]>", "", text)
