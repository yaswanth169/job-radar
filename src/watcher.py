"""The polling loop: one pass per company, on its own interval."""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.http import Blocked
from src.matching.verdict import evaluate
from src.models import CompanyConfig, Verdict
from src.notify.telegram import Telegram
from src.profile import Profile
from src.sources.registry import build_source
from src.store import Store, _job_dict

logger = logging.getLogger(__name__)

MAX_RETRY_PER_CYCLE = 25
BLOCK_BACKOFF_SECONDS = 1800   # 30 min cool-off if a site starts refusing us
DETAIL_FETCH_PAUSE = 0.4       # be polite when a batch of new jobs lands


@dataclass
class PassResult:
    company: str
    fetched: int = 0
    new_jobs: int = 0
    enriched: int = 0
    alerted: int = 0
    by_verdict: Optional[Dict[str, int]] = None
    error: str = ""


def run_pass(
    config: CompanyConfig,
    profile: Profile,
    store: Store,
    telegram: Telegram,
    seed_only: bool = False,
) -> PassResult:
    """Fetch one company, evaluate, persist, notify."""
    result = PassResult(company=config.name, by_verdict={})
    try:
        source = build_source(config)
        jobs = source.fetch()
    except Blocked as exc:
        result.error = f"blocked: {exc}"
        logger.error("%s is refusing requests: %s", config.name, exc)
        store.log_run(config.name, error=result.error)
        return result
    except Exception as exc:
        result.error = str(exc)[:300]
        logger.error("%s fetch failed: %s", config.name, exc)
        store.log_run(config.name, error=result.error)
        return result

    result.fetched = len(jobs)
    fresh: List[dict] = []

    for job in jobs:
        known = store.is_known(job.key)

        if known:
            # Known jobs are deliberately not re-enriched, so judging them
            # again from the teaser would understate them. Report the
            # verdict we stored when we had the full JD in hand.
            stored = store.verdict_of(job.key)
            if stored:
                result.by_verdict[stored] = result.by_verdict.get(stored, 0) + 1
                continue

        # Only a job we have never seen earns the extra detail request. The
        # search API's teaser rarely states an experience requirement; the
        # full JD usually does, and that is what the verdict turns on.
        if not known:
            try:
                source.enrich(job)
            except Exception as exc:
                logger.warning("%s: enrich failed for %s: %s", config.name, job.job_id, exc)
            result.enriched += 1 if job.detail_fetched else 0
            time.sleep(DETAIL_FETCH_PAUSE)

        match = evaluate(job, profile)
        result.by_verdict[match.verdict.value] = result.by_verdict.get(match.verdict.value, 0) + 1

        if known:
            continue
        result.new_jobs += 1

        # Seeding records the current board as "already seen" so the first
        # ever run does not fire dozens of alerts for pre-existing jobs.
        store.record(job, match, notified=seed_only)
        if not seed_only and match.is_alertable:
            fresh.append(_job_dict(job, match))

    if seed_only:
        store.log_run(config.name, result.fetched, result.new_jobs, 0)
        return result

    # Anything saved earlier but never delivered gets another chance.
    retry = [p for p in store.pending(MAX_RETRY_PER_CYCLE)
             if p.get("job_id") not in {f.get("job_id") for f in fresh}]
    if retry:
        logger.info("retrying %d undelivered alert(s)", len(retry))

    to_send = retry + fresh
    if to_send:
        delivered = telegram.send_jobs(to_send)
        for item in delivered:
            store.mark_notified(f"{item['company'].lower()}:{item['job_id']}")
        result.alerted = len(delivered)
        missing = len(to_send) - len(delivered)
        if missing:
            logger.warning("%d alert(s) undelivered, queued for next cycle", missing)

    store.log_run(config.name, result.fetched, result.new_jobs, result.alerted)
    return result


class Watcher:
    def __init__(
        self,
        configs: List[CompanyConfig],
        profile: Profile,
        store: Store,
        telegram: Telegram,
    ):
        self.configs = [c for c in configs if c.enabled]
        self.profile = profile
        self.store = store
        self.telegram = telegram
        self._stop = False
        self._next_due: Dict[str, float] = {}
        self._backoff: Dict[str, float] = {}

    def _handle_signal(self, *_args) -> None:
        logger.info("shutting down after the current pass...")
        self._stop = True

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        now = time.time()
        for c in self.configs:
            self._next_due[c.name] = now

        logger.info(
            "watching %d compan%s: %s",
            len(self.configs),
            "y" if len(self.configs) == 1 else "ies",
            ", ".join(f"{c.name} every {c.poll_seconds}s" for c in self.configs),
        )

        while not self._stop:
            now = time.time()
            due = [c for c in self.configs if self._next_due.get(c.name, 0) <= now]

            for config in due:
                if self._stop:
                    break
                res = run_pass(config, self.profile, self.store, self.telegram)
                interval = config.poll_seconds
                if res.error.startswith("blocked"):
                    interval = max(interval, BLOCK_BACKOFF_SECONDS)
                    logger.warning(
                        "%s: backing off for %d minutes", config.name, interval // 60
                    )
                self._next_due[config.name] = time.time() + interval
                logger.info(
                    "%s: fetched=%d new=%d full-jd=%d alerted=%d %s%s",
                    res.company, res.fetched, res.new_jobs, res.enriched,
                    res.alerted, res.by_verdict or {},
                    f" ERROR: {res.error}" if res.error else "",
                )

            if self._stop:
                break
            sleep_for = max(1.0, min(
                (self._next_due[c.name] for c in self.configs), default=time.time() + 60
            ) - time.time())
            # Sleep in short slices so Ctrl+C / SIGTERM is acted on promptly
            # instead of waiting out the whole interval.
            deadline = time.time() + sleep_for
            while not self._stop and time.time() < deadline:
                time.sleep(min(2.0, deadline - time.time()))

        logger.info("stopped.")
