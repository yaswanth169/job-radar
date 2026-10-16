"""Judge seniority from the TITLE and the ATS requisition type only.

Never from the description. A junior JD that says "work alongside senior
engineers" is not a senior role - that exact mistake is why the old project
labelled a Clinical Research Co-ordinator as "Senior / Lead".
"""

from __future__ import annotations

import re

from src.models import Seniority

_SENIOR_TITLE = re.compile(
    r"\b("
    r"senior|sr\.?|staff|principal|distinguished|fellow|"
    r"architect|director|head|chief|vp|vice\s+president|"
    r"manager|mgr|lead|leader|"
    r"iii|iv|v"
    r")\b",
    re.IGNORECASE,
)

_EARLY_TITLE = re.compile(
    r"\b("
    r"intern|internship|trainee|apprentice|co[\s-]?op|"
    r"graduate|grad|fresher|campus|entry[\s-]level|junior|jr\.?"
    r")\b",
    re.IGNORECASE,
)

# What an ATS calls each bucket. Cisco uses raasJobRequisitionType.
_EARLY_REQ = {
    "co-op", "early career", "entry level talent", "entry level", "intern", "internship",
    "co-op", "co op", "coop", "apprentice", "apprenticeship",
    "graduate", "new grad", "campus",
}
_SENIOR_REQ = {"executive", "leadership", "director"}
_MID_REQ = {"professional", "experienced", "regular"}


def _req_bucket(requisition_type: str) -> str:
    """Classify an ATS requisition label, which may be a '/'-joined set.

    A set only counts as early-career when EVERY member is early-career.
    """
    parts = [p.strip().lower() for p in (requisition_type or "").split("/") if p.strip()]
    if not parts:
        return ""
    if all(p in _EARLY_REQ for p in parts):
        return "early"
    if any(p in _SENIOR_REQ for p in parts):
        return "senior"
    if all(p in _MID_REQ for p in parts):
        return "mid"
    return ""


def classify(title: str, requisition_type: str = "") -> Seniority:
    bucket = _req_bucket(requisition_type)
    title = title or ""

    # A senior word in the title always wins. The ATS bucket can be wrong
    # or coarse; the title is what the hiring manager actually wrote.
    if _SENIOR_TITLE.search(title):
        return Seniority.SENIOR

    if bucket == "senior":
        return Seniority.SENIOR
    if bucket == "early":
        return Seniority.EARLY

    if _EARLY_TITLE.search(title):
        return Seniority.EARLY

    if bucket == "mid":
        return Seniority.MID

    return Seniority.UNKNOWN
