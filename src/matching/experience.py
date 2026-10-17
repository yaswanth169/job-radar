"""Parse an experience requirement out of free job text.

This is the component the previous project never had. It missed 13 of 20
real-world phrasings - including every "N+ years" form, which is the most
common way a JD states its requirement.

Design notes:
  * Years are read from the DESCRIPTION only, never the title. Titles say
    "Senior", descriptions say "3+ years"; mixing them produced the old
    project's "Clinical Research Co-ordinator -> Senior / Lead" nonsense.
  * UNKNOWN is a real, first-class state. "Nothing stated" is a different
    decision from "stated and too high", and the user treats them
    differently, so the type system has to as well.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from src.models import ExperienceRequirement, ExperienceState

# A number match is only trusted as a requirement if the word "experience"
# (or "exp") appears within this many characters of it.
PROXIMITY = 90

NUM = r"(\d+(?:\.\d+)?)"
YEARS = r"(?:years?|yrs?)"

# Ordered most-specific first. Each returns (min, max).
_RANGE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # "6 months to 2 years"
    (re.compile(rf"{NUM}\s*months?\s*(?:-|to|through)\s*{NUM}\s*{YEARS}"), "months_to_years"),
    # "1-4 years", "2 to 5 years", "1 - 3 yrs"
    (re.compile(rf"{NUM}\s*\+?\s*(?:-|to|through)\s*{NUM}\s*\+?\s*{YEARS}"), "range"),
]

# (pattern, label, needs_proximity). "plain" and "months" spell out
# "experien" inside the pattern, so they are self-evidencing; the rest are
# bare numbers that only count as a requirement if experience is mentioned
# nearby.
_MIN_PATTERNS: List[Tuple[re.Pattern, str, bool]] = [
    # "minimum 2 years", "at least 1 year", "no less than 3 years"
    (re.compile(
        rf"(?:minimum|min\.?|at\s*least|atleast|no\s*less\s*than|more\s*than|over|upwards\s*of)"
        rf"\s*(?:of\s*)?{NUM}\s*\+?\s*{YEARS}"), "minimum", True),
    # "3+ years", "2 + yrs"
    (re.compile(rf"{NUM}\s*\+\s*{YEARS}"), "plus", True),
    # "3 years of experience", "2 yrs relevant experience"
    (re.compile(
        rf"{NUM}\s*{YEARS}\s*(?:of\s+)?"
        rf"(?:relevant\s+|professional\s+|industry\s+|work\s+|hands[\s-]on\s+)?experien"), "plain", True),
    # "6 months of experience"
    (re.compile(rf"{NUM}\s*months?\s*(?:of\s+)?(?:relevant\s+)?experien"), "months", True),
]

# Last resort: a bare "4 years" with no qualifying word at all. Consulted
# only when every pattern above came up empty, because it re-matches every
# number they do - including the ones they deliberately threw away.
_BARE_PATTERN: Tuple[re.Pattern, str, bool] = (
    re.compile(rf"{NUM}\s*{YEARS}"), "bare", True,
)

# Phrases that mean the employer is describing its own age, not asking for
# years from you: "PayPal has been revolutionizing commerce for more than
# 25 years". The surrounding marketing copy usually mentions "experiences"
# (as in customer experiences), which is enough to satisfy the proximity
# check, so proximity alone cannot tell these apart from a real floor.
_COMPANY_HISTORY = re.compile(
    r"(?:has|have|had)\s+been\b"
    r"|\bfounded\b|\bestablished\s+in\b|\bsince\s+(?:19|20)\d{2}\b"
    r"|\b(?:years?|yrs?)\s+of\s+(?:innovation|history|excellence|leadership"
    r"|operation|growth|heritage|service)\b"
    r"|\bour\s+(?:history|journey|legacy|story)\b"
    r"|\b(?:celebrating|marking)\s+\d+\s*(?:years?|yrs?)\b"
)
HISTORY_WINDOW = 120

# A match containing one of these is unambiguously a requirement - nobody
# writes "the company has been around for a minimum of 25 years" - so it
# is exempt from the company-history check above.
_HARD_REQUIREMENT = re.compile(
    r"\b(?:minimum|min\.?|at\s*least|atleast|no\s*less\s*than)\b"
)

_EARLY_MARKERS = re.compile(
    r"\b("
    r"fresher|freshers|entry[\s-]level|entry level|new\s+grad(?:uate)?|recent\s+grad(?:uate)?|"
    r"no\s+(?:prior\s+|previous\s+)?experience\s+(?:is\s+)?(?:required|necessary|needed)|"
    r"without\s+(?:prior\s+)?experience|campus\s+hire|early[\s-]career|"
    r"graduate\s+program(?:me)?|university\s+grad(?:uate)?|"
    r"0\s*(?:-|to)\s*1\s*(?:years?|yrs?)|zero\s+experience"
    r")\b",
    re.IGNORECASE,
)

_SENIOR_YEARS = re.compile(rf"(?:{NUM})\s*\+?\s*{YEARS}", re.IGNORECASE)
_SENIOR_THRESHOLD = 7.0

_MONTHS_PER_YEAR = 12.0


def _flatten(text: str) -> str:
    """Normalise dashes and whitespace, preserving the original casing.

    Applied identically by `_normalise`, so an offset found in the lowered
    text points at the same character here - which lets the evidence quote
    come back in the job's real casing.
    """
    text = text or ""
    # unify every dash variant so ranges parse uniformly
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text)


def _normalise(text: str) -> str:
    return _flatten(text).lower()


def _near_experience(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - PROXIMITY): end + PROXIMITY]
    return "experien" in window or re.search(r"\bexp\b", window) is not None


def _is_company_history(text: str, start: int, end: int) -> bool:
    """True when this number is the employer's age, not a requirement.

    Scoped to the sentence the number sits in, not a fixed window. The
    giveaway sits on one side or the other - "founded ... 30 years ago"
    before, "40 years of innovation" after - but always in the same
    sentence, so a real floor in the next one is never dragged down
    with it.
    """
    before = text[max(0, start - HISTORY_WINDOW):start]
    cut = max(before.rfind(c) for c in ".!?;:")
    if cut != -1:
        before = before[cut + 1:]

    after = text[end:end + HISTORY_WINDOW]
    stop = min((i for i in (after.find(c) for c in ".!?;:") if i != -1), default=-1)
    if stop != -1:
        after = after[:stop]

    return _COMPANY_HISTORY.search(before + text[start:end] + after) is not None


def _evidence(original: str, norm: str, start: int, end: int) -> str:
    """A readable snippet around the match, in the job's original casing.

    `_flatten` applies the same transformations as `_normalise` without
    lowercasing, so offsets found in the lowered text line up exactly.
    """
    display = _flatten(original)
    if len(display) != len(norm):
        display = norm
    lo = max(0, start - 70)
    hi = min(len(display), end + 70)
    snippet = display[lo:hi].strip()
    if lo > 0:
        snippet = "..." + snippet
    if hi < len(display):
        snippet = snippet + "..."
    return snippet


def _lowest_match(
    text: str,
    norm: str,
    entry: Tuple[re.Pattern, str, bool],
    source_field: str,
    best: Optional[ExperienceRequirement],
) -> Optional[ExperienceRequirement]:
    """Fold one pattern class into the running lowest minimum.

    Keeping the lowest is deliberate: JDs list a hard floor in the
    qualifications and nice-to-haves further down, and the floor is the
    only number that actually gates you.
    """
    pattern, label, needs_proximity = entry
    for m in pattern.finditer(norm):
        if needs_proximity and not _near_experience(norm, m.start(), m.end()):
            # "3+ years" with no mention of experience anywhere near is
            # usually describing a product, not a requirement.
            continue
        if (not _HARD_REQUIREMENT.search(m.group(0))
                and _is_company_history(norm, m.start(), m.end())):
            continue
        value = float(m.group(1))
        if label == "months":
            value = value / _MONTHS_PER_YEAR
        candidate = ExperienceRequirement(
            state=ExperienceState.STATED,
            min_years=round(value, 2),
            evidence=_evidence(text, norm, m.start(), m.end()),
            source_field=source_field,
        )
        if best is None or (candidate.min_years or 0) < (best.min_years or 0):
            best = candidate
    return best


def parse_experience(text: str, source_field: str = "description") -> ExperienceRequirement:
    """Extract the experience requirement from a block of job text."""
    if not text or not text.strip():
        return ExperienceRequirement(state=ExperienceState.UNKNOWN)

    norm = _normalise(text)

    # 1. Ranges win - they are the most informative form.
    for pattern, label in _RANGE_PATTERNS:
        for m in pattern.finditer(norm):
            if not _near_experience(norm, m.start(), m.end()):
                continue
            lo = float(m.group(1))
            hi = float(m.group(2))
            if label == "months_to_years":
                lo = lo / _MONTHS_PER_YEAR
            if hi < lo:
                lo, hi = hi, lo
            return ExperienceRequirement(
                state=ExperienceState.STATED,
                min_years=round(lo, 2),
                max_years=round(hi, 2),
                evidence=_evidence(text, norm, m.start(), m.end()),
                source_field=source_field,
            )

    # 2. Explicit entry-level language, before single numbers.
    early = _EARLY_MARKERS.search(norm)

    # 3. Single-sided minimums. Every pattern class is weighed, not just
    #    the first one that happens to hit: a JD saying "minimum 5 years"
    #    in one bullet and "1+ years" in another has a floor of 1, and
    #    stopping at the first class would report 5 and skip the job.
    best: Optional[ExperienceRequirement] = None
    for entry in _MIN_PATTERNS:
        best = _lowest_match(text, norm, entry, source_field, best)
    if best is None:
        best = _lowest_match(text, norm, _BARE_PATTERN, source_field, best)

    if best is not None:
        if best.min_years is not None and best.min_years >= _SENIOR_THRESHOLD:
            best.state = ExperienceState.SENIOR
        return best

    if early:
        return ExperienceRequirement(
            state=ExperienceState.EARLY_CAREER,
            min_years=0.0,
            evidence=_evidence(text, norm, early.start(), early.end()),
            source_field=source_field,
        )

    return ExperienceRequirement(state=ExperienceState.UNKNOWN, source_field=source_field)
