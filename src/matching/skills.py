"""Overlap between the candidate's skills and the job's.

Purely informational - it is shown in the alert, never used to filter.
The point is to help you judge fit at a glance, not to gate anything.
"""

from __future__ import annotations

import re
from typing import List, Tuple


def _canon(skill: str) -> str:
    s = (skill or "").strip().lower()
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s)
    # common aliases so "Node.js" and "nodejs" count as one
    aliases = {
        "golang": "go",
        "nodejs": "node js",
        "node.js": "node js",
        "c sharp": "c#",
        "postgresql": "postgres",
        "k8s": "kubernetes",
        "ci cd": "ci/cd",
        "cicd": "ci/cd",
        "gen ai": "generative ai",
        "genai": "generative ai",
        "llm": "llms",
        "ml": "machine learning",
    }
    return aliases.get(s, s)


def overlap(mine: List[str], theirs: List[str]) -> Tuple[List[str], int]:
    """Return (skills I have that they want, total skills they listed)."""
    if not theirs:
        return [], 0
    # map canonical form -> the candidate's own spelling, so the alert
    # reads "Python, Git" rather than the ATS's lowercase "python, git"
    mine_canon = {}
    for s in mine:
        if s:
            mine_canon.setdefault(_canon(s), s.strip())
    matched: List[str] = []
    seen = set()
    for raw in theirs:
        c = _canon(raw)
        if not c or c in seen:
            continue
        if c in mine_canon:
            seen.add(c)
            matched.append(mine_canon[c])
    return matched, len({_canon(s) for s in theirs if s})
