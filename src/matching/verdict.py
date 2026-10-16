"""Turn a job plus a profile into APPLY / CHECK / SKIP.

Mirrors the user's own words:

  "if any job is not asking for experience, or if it is asking for 1+ or 1.5,
   I will check and apply; if it is asking more than mine I don't bother"

  APPLY  stated minimum is within reach           -> alert, act on it
  CHECK  nothing stated but it looks early-career -> alert, needs your eyes
  SKIP   asks for more than you have, or senior   -> logged, never sent
"""

from __future__ import annotations

from src.matching.experience import parse_experience
from src.matching.seniority import classify
from src.matching.skills import overlap
from src.models import (
    ExperienceState,
    Job,
    MatchResult,
    Seniority,
    Verdict,
)
from src.profile import Profile


def evaluate(job: Job, profile: Profile) -> MatchResult:
    experience = parse_experience(job.match_text())
    seniority = classify(job.title, job.requisition_type)
    matched_skills, total_skills = overlap(profile.skills, job.skills)

    def result(verdict: Verdict, reason: str) -> MatchResult:
        return MatchResult(
            verdict=verdict,
            reason=reason,
            experience=experience,
            seniority=seniority,
            matched_skills=matched_skills,
            total_skills=total_skills,
        )

    # 1. Hard title exclusions first - cheapest and most certain.
    avoided = profile.avoids_title(job.title)
    if avoided:
        return result(Verdict.SKIP, f"title contains avoided term '{avoided}'")

    if not profile.wants_title(job.title):
        return result(Verdict.SKIP, "title does not match any wanted role")

    # 2. Location.
    if not profile.location_ok(job.location, job.country):
        return result(Verdict.SKIP, f"location '{job.location}' outside your preferences")

    # 3. Seniority from the title / requisition type.
    if seniority is Seniority.SENIOR:
        return result(Verdict.SKIP, "senior-level role (title or requisition type)")

    # 4. The experience gate - the heart of it.
    if experience.state is ExperienceState.SENIOR:
        return result(
            Verdict.SKIP,
            f"needs {experience.describe()}, you have {_n(profile.years)}",
        )

    if experience.state is ExperienceState.STATED:
        required = experience.min_years if experience.min_years is not None else 0.0
        if required <= profile.max_years:
            return result(
                Verdict.APPLY,
                f"needs {experience.describe()}, you have {_n(profile.years)}",
            )
        return result(
            Verdict.SKIP,
            f"needs {experience.describe()}, you have {_n(profile.years)}",
        )

    if experience.state is ExperienceState.EARLY_CAREER:
        return result(Verdict.APPLY, "explicitly entry level / no experience required")

    # 5. Nothing stated. This is the bucket the user said they would open
    #    and read themselves, so surface it - clearly marked as unverified.
    if seniority is Seniority.EARLY:
        return result(Verdict.CHECK, "no experience stated, but classified early-career")
    if seniority is Seniority.MID:
        return result(Verdict.CHECK, "no experience stated, mid-level requisition")
    return result(Verdict.CHECK, "no experience stated anywhere - worth a look")


def _n(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
