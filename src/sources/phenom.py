"""Phenom People career sites (Cisco and many other large employers).

Verified against careers.cisco.com: the search API answers with no cookies,
no JWT and no CSRF token. The tenant is identified by `refNum` inside the
POST body - which is why probing the URL with query parameters only ever
returned "Tenant not identified".

Adding another Phenom company is a YAML file with a different ref_num.
No code change.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List
from urllib.parse import urlencode

from html import unescape

from src.http import post_json
from src.models import CompanyConfig, Job
from src.sources.base import BaseSource

logger = logging.getLogger(__name__)

FACETS = [
    "category",
    "raasJobRequisitionType",
    "country",
    "state",
    "city",
    "type",
    "RemoteType",
]


class PhenomSource(BaseSource):
    def __init__(self, config: CompanyConfig):
        super().__init__(config)
        self.endpoint = f"{config.base_url.rstrip('/')}/widgets"

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "*/*",
            "Origin": self.config.base_url.rstrip("/"),
            "Referer": f"{self.config.base_url.rstrip('/')}/{self.config.locale_path}/search-results",
        }

    def _payload(self, start: int, size: int) -> Dict[str, Any]:
        return {
            "sortBy": "Most recent",
            "subsearch": "",
            "from": start,
            "jobs": True,
            "counts": True,
            "all_fields": FACETS,
            "pageName": "search-results",
            "size": size,
            "clearAll": False,
            "jdsource": "facets",
            "isSliderEnable": False,
            "pageId": "page4",
            "siteType": "external",
            "keywords": self.config.keywords or "",
            "global": True,
            "selected_fields": self.config.filters or {},
            "sort": {"order": "desc", "field": "postedDate"},
            "lang": self.config.lang,
            "deviceType": "desktop",
            "country": "global",
            "refNum": self.config.ref_num,
            "ddoKey": "refineSearch",
        }

    def fetch(self) -> List[Job]:
        jobs: List[Job] = []
        seen: set = set()
        size = max(1, min(self.config.page_size, 100))
        start = 0

        while True:
            data = post_json(self.endpoint, self._payload(start, size), self._headers())
            block = (data or {}).get("refineSearch") or {}
            if block.get("status") not in (200, "200", None):
                logger.warning("%s: unexpected status %s", self.name, block.get("status"))
            payload = block.get("data") or {}
            raw_jobs = payload.get("jobs") or []
            total = int(block.get("totalHits") or 0)

            for item in raw_jobs:
                job = self._to_job(item)
                if job and job.job_id not in seen:
                    seen.add(job.job_id)
                    jobs.append(job)

            start += size
            if not raw_jobs or start >= total or len(jobs) >= total:
                break

        return jobs

    def _to_job(self, item: Dict[str, Any]) -> Job | None:
        title = str(item.get("title") or "").strip()
        job_id = str(item.get("jobId") or item.get("reqId") or "").strip()
        if not title or not job_id:
            return None

        seq = str(item.get("jobSeqNo") or "").strip()

        return Job(
            company=self.name,
            job_id=job_id,
            title=title,
            apply_url=self._apply_url(seq, job_id),
            job_url=self._job_url(seq, job_id, title),
            location=str(item.get("location") or item.get("cityStateCountry") or ""),
            city=str(item.get("city") or ""),
            country=str(item.get("country") or ""),
            remote_type=str(item.get("RemoteType") or ""),
            employment_type=str(item.get("type") or ""),
            category=str(item.get("category") or ""),
            department=str(item.get("department") or ""),
            requisition_type=self._requisition_type(item),
            posted_date=str(item.get("postedDate") or "")[:10],
            created_date=str(item.get("dateCreated") or ""),
            description=self._description(item),
            skills=[str(s) for s in (item.get("ml_skills") or []) if s],
            source="phenom",
            external_ref=seq,
        )

    def _requisition_type(self, item: Dict[str, Any]) -> str:
        """Cisco returns this facet in the aggregations, not on each job.

        We filtered on it server-side, so every job that came back already
        satisfies it - carrying it through lets the seniority classifier
        use the ATS's own early-career label instead of guessing.
        """
        raw = str(item.get("raasJobRequisitionType") or "").strip()
        if raw:
            return raw
        values = [str(v) for v in (self.config.filters.get("raasJobRequisitionType") or [])]
        if not values:
            return ""
        if len(values) == 1:
            return values[0]
        return "Early career"

    def enrich(self, job: Job) -> Job:
        """Replace the teaser with the job's full description.

        The search API returns a ~300 character marketing teaser that rarely
        states an experience requirement. The same `/widgets` endpoint with
        ddoKey=jobDetail returns the real 4-9k character JD, which usually
        does. Measured on Cisco: 10 of 15 jobs went from "Not stated" to a
        concrete range once the full JD was read.

        Called only for jobs we have not seen before, so a routine poll
        costs one request, not one per job.
        """
        if not self.config.fetch_details or not job.external_ref:
            return job
        try:
            data = post_json(
                self.endpoint,
                {
                    "lang": self.config.lang,
                    "deviceType": "desktop",
                    "country": "global",
                    "pageName": "job-details",
                    "ddoKey": "jobDetail",
                    "siteType": "external",
                    "jobSeqNo": job.external_ref,
                    "refNum": self.config.ref_num,
                },
                self._detail_headers(job),
            )
        except Exception as exc:
            logger.warning(
                "%s: could not load full JD for %s (%s) - keeping the teaser: %s",
                self.name, job.title, job.job_id, exc,
            )
            return job

        detail = ((data or {}).get("jobDetail") or {}).get("data") or {}
        record = detail.get("job") or {}
        full = _html_to_text(str(record.get("description") or ""))
        if len(full) < 200:
            logger.debug("%s: detail for %s looked empty", self.name, job.job_id)
            return job

        # Full JD first so its statements win when both mention years.
        teaser = job.description
        job.description = full if not teaser else f"{full}\n\n{teaser}"
        job.detail_fetched = True

        # The detail record carries better values for some fields.
        if record.get("ml_skills"):
            job.skills = [str(s) for s in record["ml_skills"] if s] or job.skills
        if not job.employment_type:
            job.employment_type = str(record.get("type") or "")
        return job

    def _detail_headers(self, job: Job) -> Dict[str, str]:
        base = self.config.base_url.rstrip("/")
        return {
            "Accept": "*/*",
            "Origin": base,
            "Referer": f"{base}/{self.config.locale_path}/job/{job.job_id}/",
        }

    def _description(self, item: Dict[str, Any]) -> str:
        """Every teaser Phenom gives us, concatenated.

        The ATS teaser often carries the experience line that the polished
        marketing teaser leaves out, so we search all of them.
        """
        parts = [str(item.get("descriptionTeaser") or "")]
        parser = item.get("ml_job_parser") or {}
        if isinstance(parser, dict):
            for key in (
                "descriptionTeaser",
                "descriptionTeaser_ats",
                "descriptionTeaser_keyword",
                "descriptionTeaser_first200",
            ):
                value = parser.get(key)
                if value:
                    parts.append(str(value))
        # de-duplicate while preserving order; the teasers overlap heavily
        out: List[str] = []
        for p in parts:
            p = p.strip()
            if p and p not in out:
                out.append(p)
        return "\n".join(out)

    def _apply_url(self, seq: str, job_id: str) -> str:
        """The Phenom-hosted apply flow.

        Deliberately NOT the `applyUrl` field from the API - that points at
        raw Workday, which loses the pre-filled candidate profile. The site's
        own UI ignores it too, because every job carries externalApply=false.
        """
        base = self.config.base_url.rstrip("/")
        if seq:
            query = urlencode({"jobSeqNo": seq})
            return f"{base}/{self.config.locale_path}/apply?{query}"
        return f"{base}/{self.config.locale_path}/job/{job_id}"

    def _job_url(self, seq: str, job_id: str, title: str) -> str:
        base = self.config.base_url.rstrip("/")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return f"{base}/{self.config.locale_path}/job/{job_id}/{slug}"


def _html_to_text(html: str) -> str:
    """Flatten a JD's HTML into readable text, keeping line structure."""
    if not html:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()
