"""Shared data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ExperienceState(str, Enum):
    """What we know about a job's experience requirement."""

    STATED = "stated"            # a concrete number of years was found
    EARLY_CAREER = "early"       # explicitly fresher / entry level / intern
    SENIOR = "senior"            # explicitly senior-level years (8+, etc.)
    UNKNOWN = "unknown"          # nothing said - worth a human look


class Seniority(str, Enum):
    EARLY = "early"
    MID = "mid"
    SENIOR = "senior"
    UNKNOWN = "unknown"


class Verdict(str, Enum):
    APPLY = "APPLY"
    CHECK = "CHECK"
    SKIP = "SKIP"


@dataclass
class ExperienceRequirement:
    state: ExperienceState = ExperienceState.UNKNOWN
    min_years: Optional[float] = None
    max_years: Optional[float] = None
    evidence: str = ""
    source_field: str = ""

    def describe(self) -> str:
        if self.state is ExperienceState.EARLY_CAREER:
            return "Entry level / no experience required"
        if self.state is ExperienceState.UNKNOWN:
            return "Not stated"
        if self.min_years is not None and self.max_years is not None:
            return f"{_num(self.min_years)}-{_num(self.max_years)} years"
        if self.min_years is not None:
            return f"{_num(self.min_years)}+ years"
        if self.state is ExperienceState.SENIOR:
            return "Senior level"
        return "Not stated"


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


@dataclass
class Job:
    company: str
    job_id: str
    title: str
    apply_url: str
    job_url: str = ""
    location: str = ""
    city: str = ""
    country: str = ""
    remote_type: str = ""
    employment_type: str = ""
    category: str = ""
    department: str = ""
    requisition_type: str = ""      # Cisco: Entry Level Talent / Professional
    posted_date: str = ""
    created_date: str = ""
    description: str = ""
    skills: List[str] = field(default_factory=list)
    source: str = ""
    # The ATS's own handle for fetching this job's full detail record.
    external_ref: str = ""
    detail_fetched: bool = False

    @property
    def key(self) -> str:
        return f"{self.company.lower()}:{self.job_id}"

    def match_text(self) -> str:
        """Text searched for experience requirements - never the title."""
        return self.description


@dataclass
class MatchResult:
    verdict: Verdict
    reason: str
    experience: ExperienceRequirement
    seniority: Seniority
    matched_skills: List[str] = field(default_factory=list)
    total_skills: int = 0

    @property
    def is_alertable(self) -> bool:
        return self.verdict in (Verdict.APPLY, Verdict.CHECK)


@dataclass
class CompanyConfig:
    name: str
    adapter: str
    base_url: str
    enabled: bool = True
    ref_num: str = ""
    locale_path: str = "global/en"
    lang: str = "en_global"
    poll_seconds: int = 300
    filters: Dict[str, List[str]] = field(default_factory=dict)
    keywords: str = ""
    page_size: int = 100
    # Fetch each new job's full description before deciding. The search API
    # only returns a ~300 char marketing teaser, which almost never states
    # the experience requirement; the full JD usually does.
    fetch_details: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)
