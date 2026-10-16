"""Measure what GitHub Actions ACTUALLY delivers vs the 5-minute schedule.

The whole point of trialling GitHub first is to find out whether its
scheduling is good enough for this. Run this after the workflow has been
live for a few hours and it will tell you with real numbers.

    python tools/check_drift.py <owner>/<repo>

Public repos need no authentication. For a private repo, set GITHUB_TOKEN.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

TARGET_MINUTES = 5.0


def fetch_runs(repo: str, limit: int = 100):
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page={limit}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "job-radar-drift-check",
    })
    token = os.getenv("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("workflow_runs", [])


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    repo = sys.argv[1].strip().rstrip("/").replace("https://github.com/", "")

    runs = [r for r in fetch_runs(repo) if r.get("event") == "schedule"]
    if len(runs) < 2:
        print(f"Only {len(runs)} scheduled run(s) so far. Let it run a few hours "
              f"and try again.\n(Manually-triggered runs are excluded - they say "
              f"nothing about scheduling.)")
        return 0

    times = sorted(
        datetime.strptime(r["run_started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        for r in runs
    )
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:])]

    span_hours = (times[-1] - times[0]).total_seconds() / 3600
    expected = int(span_hours * 60 / TARGET_MINUTES)

    print(f"Repo            : {repo}")
    print(f"Scheduled runs  : {len(runs)} over {span_hours:.1f} hours")
    print(f"Expected at 5min: ~{expected}")
    print(f"Actually ran    : {len(runs)}   ({100*len(runs)/max(expected,1):.0f}% of schedule)")
    print()
    print(f"Gap between runs (target {TARGET_MINUTES:.0f}.0 min):")
    print(f"  best    : {min(gaps):5.1f} min")
    print(f"  median  : {sorted(gaps)[len(gaps)//2]:5.1f} min")
    print(f"  average : {sum(gaps)/len(gaps):5.1f} min")
    print(f"  worst   : {max(gaps):5.1f} min")
    print()

    buckets = {"<=6 min": 0, "6-10 min": 0, "10-20 min": 0, ">20 min": 0}
    for g in gaps:
        if g <= 6: buckets["<=6 min"] += 1
        elif g <= 10: buckets["6-10 min"] += 1
        elif g <= 20: buckets["10-20 min"] += 1
        else: buckets[">20 min"] += 1
    for label, n in buckets.items():
        bar = "#" * int(40 * n / len(gaps))
        print(f"  {label:9} {n:4d}  {bar}")

    print()
    on_time = sum(1 for g in gaps if g <= 6) / len(gaps)
    if on_time >= 0.9:
        print("VERDICT: GitHub Actions is holding the schedule well. Stay here.")
    elif on_time >= 0.6:
        print("VERDICT: usable, but drifting. Fine for a job watcher; not for "
              "anything time-critical.")
    else:
        print("VERDICT: the schedule is not being honoured. An always-on box "
              "(Oracle) would be meaningfully better.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
