"""The candidate profile - the single source of truth for every decision."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Profile:
    years: float = 0.0
    tolerance: float = 0.0
    countries: List[str] = field(default_factory=list)
    preferred_locations: List[str] = field(default_factory=list)
    allow_any_location: bool = False
    want_roles: List[str] = field(default_factory=list)
    avoid_roles: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)

    @property
    def max_years(self) -> float:
        """The highest stated minimum we will still call APPLY."""
        return self.years + self.tolerance

    def wants_title(self, title: str) -> bool:
        if not self.want_roles:
            return True
        t = (title or "").lower()
        return any(w.lower() in t for w in self.want_roles)

    def avoids_title(self, title: str) -> str:
        """Return the matching avoid-term, or '' if the title is fine."""
        t = (title or "").lower()
        for a in self.avoid_roles:
            a_low = a.lower()
            if re.search(rf"\b{re.escape(a_low)}\b", t):
                return a
        return ""

    def location_ok(self, job_location: str, job_country: str) -> bool:
        if self.allow_any_location:
            return True
        blob = f"{job_location} {job_country}".lower()
        if self.countries and any(c.lower() in blob for c in self.countries):
            return True
        if self.preferred_locations and any(p.lower() in blob for p in self.preferred_locations):
            return True
        return not (self.countries or self.preferred_locations)


def load_profile(path: Path | str | None = None) -> Profile:
    p = Path(path) if path else ROOT / "config" / "profile.yaml"
    if not p.exists():
        raise FileNotFoundError(
            f"No profile at {p}. Copy config/profile.yaml from the repo and edit it."
        )
    data: Dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    exp = data.get("experience") or {}
    loc = data.get("locations") or {}
    roles = data.get("roles") or {}
    return Profile(
        years=float(exp.get("years", 0) or 0),
        tolerance=float(exp.get("tolerance", 0) or 0),
        countries=list(loc.get("countries") or []),
        preferred_locations=list(loc.get("preferred") or []),
        allow_any_location=bool(loc.get("allow_any", False)),
        want_roles=list(roles.get("want") or []),
        avoid_roles=list(roles.get("avoid") or []),
        skills=list(data.get("skills") or []),
    )
