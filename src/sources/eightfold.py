"""Eightfold AI career sites (Qualcomm and others).

Verified against careers.qualcomm.com. Three things differ sharply from
Phenom, and the whole design follows from them:

  1. There is NO early-career filter. The site advertises a `seniority`
     facet, but passing it changes nothing - `seniority=Entry Level`
     still returns all 2100 jobs. Cisco's raasJobRequisitionType does the
     first cut for us server-side; here the experience parser does all of
     it. We therefore never gate on the job title: Qualcomm names
     early-career roles "Engineer", "Associate Engineer" and plenty of
     other things, and a plain "Engineer" title routinely hides a 2+ year
     floor. The description is the only truth.

  2. `num` is capped at 10 regardless of what we ask for. Reading all 601
     India jobs would be 61 requests every five minutes. Instead we sort
     newest-first and read only the first few pages: measured on Qualcomm
     India, 50 jobs spans about three days of postings, so nothing can
     scroll out of the window between two 5-minute polls.

  3. The search response carries no description at all. The full JD comes
     from position_details, the same endpoint the site's own job page
     calls: ~16 KB of JSON against ~308 KB of rendered HTML, and nothing
     that breaks the next time they restyle the page. It is keyed by the
     internal position id (Job.external_ref), not the requisition number
     shown to applicants.

The older /api/apply/v2/jobs endpoint most Eightfold tenants expose is
disabled here - it answers 403 "Not authorized for PCSX". /api/pcsx/search
is what the site itself calls, and it needs no cookie or token.
"""

from __future__ import annotations

import logging
import re
import time
from html import unescape
from typing import Any, Dict, List, Optional

from src.http import get_json
from src.models import CompanyConfig, Job
from src.sources.base import BaseSource

logger = logging.getLogger(__name__)

PAGE_SIZE = 10          # the API's hard ceiling; asking for more is ignored
DEFAULT_MAX_PAGES = 5
# Qualcomm answers 429 to a burst. A routine poll is only five requests,
# but spacing them keeps us comfortably under whatever the limit is.
PAGE_PAUSE = 0.5


class EightfoldSource(BaseSource):
    def __init__(self, config: CompanyConfig):
        super().__init__(config)
        self.base = config.base_url.rstrip("/")
        self.endpoint = f"{self.base}/api/pcsx/search"
        self.details_endpoint = f"{self.base}/api/pcsx/position_details"
        self.domain = config.extra.get("domain") or _domain_of(self.base)
        self.max_pages = int(config.extra.get("max_pages") or DEFAULT_MAX_PAGES)

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.base}/careers",
        }

    def _params(self, start: int) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "domain": self.domain,
            "start": start,
            "num": PAGE_SIZE,
            # Newest first. Without this the order is relevance-ranked and a
            # job posted an hour ago can sit on page 40.
            "sort_by": "timestamp",
            "query": self.config.keywords or "",
        }
        location = self.config.filters.get("location")
        if location:
            # The API takes a single location string, not a list.
            params["location"] = location[0] if isinstance(location, list) else location
        return params

    def fetch(self) -> List[Job]:
        jobs: List[Job] = []
        seen: set = set()

        for page in range(self.max_pages):
            data = get_json(self.endpoint, self._params(page * PAGE_SIZE), self._headers())
            block = (data or {}).get("data") or {}
            positions = block.get("positions") or []
            if not positions:
                break

            for item in positions:
                job = self._to_job(item)
                if job and job.job_id not in seen:
                    seen.add(job.job_id)
                    jobs.append(job)

            total = int(block.get("count") or 0)
            if (page + 1) * PAGE_SIZE >= total:
                break
            time.sleep(PAGE_PAUSE)

        return jobs

    def _to_job(self, item: Dict[str, Any]) -> Optional[Job]:
        title = str(item.get("name") or "").strip()
        job_id = str(item.get("displayJobId") or item.get("atsJobId") or "").strip()
        pid = str(item.get("id") or "").strip()
        if not title or not job_id:
            return None

        locations = [str(x) for x in (item.get("locations") or []) if x]
        url = f"{self.base}{item.get('positionUrl') or f'/careers/job/{pid}'}"

        return Job(
            company=self.name,
            job_id=job_id,
            title=title,
            apply_url=url,
            job_url=url,
            location=" | ".join(locations[:3]),
            city=locations[0] if locations else "",
            country=_country_of(locations[0] if locations else ""),
            remote_type=str(item.get("workLocationOption") or ""),
            department=str(item.get("department") or ""),
            posted_date=_date_of(item.get("postedTs")),
            created_date=_date_of(item.get("creationTs")),
            # Search returns no description whatsoever - enrich() is not an
            # optimisation here, it is the only source of a requirement.
            description="",
            source="eightfold",
            external_ref=pid,
        )

    def enrich(self, job: Job) -> Job:
        """Pull the full JD from the position_details endpoint.

        Unlike Phenom there is no teaser to fall back on, so a failure here
        leaves the job with no description at all - which the verdict engine
        correctly reports as UNKNOWN rather than guessing.
        """
        if not self.config.fetch_details or not job.external_ref:
            return job
        try:
            data = get_json(
                self.details_endpoint,
                {"position_id": job.external_ref, "domain": self.domain},
                self._headers(),
            )
        except Exception as exc:
            logger.warning(
                "%s: could not load JD for %s (%s): %s",
                self.name, job.title, job.job_id, exc,
            )
            return job

        record = (data or {}).get("data") or {}
        full = _to_text(str(record.get("jobDescription") or ""))
        if len(full) < 200:
            logger.debug("%s: no usable JD for %s", self.name, job.job_id)
            return job

        job.description = full
        job.detail_fetched = True
        if not job.posted_date:
            job.posted_date = _date_of(record.get("postedTs"))
        return job


def _to_text(text: str) -> str:
    """Flatten the JD to readable text.

    jobDescription comes back as markup with some markdown mixed in, so
    both get stripped before the experience parser sees it.
    """
    if not text:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"^\s*[*#>-]+\s*", "", text, flags=re.M)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _date_of(ts: Any) -> str:
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _country_of(location: str) -> str:
    return location.rsplit(",", 1)[-1].strip() if "," in location else ""


def _domain_of(base_url: str) -> str:
    host = re.sub(r"^https?://", "", base_url).split("/")[0]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host
