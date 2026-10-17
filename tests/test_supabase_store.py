"""SupabaseStore against a fake PostgREST. No network, no credentials."""

import sys, os, json
from urllib.parse import unquote
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")

from src.models import ExperienceRequirement, ExperienceState, Job, MatchResult, Seniority, Verdict
from src.store_supabase import SupabaseStore


class FakeRest:
    """Minimal in-memory stand-in for the subset of PostgREST we use."""

    def __init__(self):
        self.jobs = {}
        self.runs = []
        self.calls = []

    def __call__(self, method, path, **kw):
        self.calls.append(f"{method} {path.split('?')[0]}")
        # PostgREST decodes percent-encoded filter values; so must the fake,
        # or "cisco%3A2027309" never matches the stored "cisco:2027309".
        path = unquote(path)
        table = path.split("?")[0]
        body = json.loads(kw["data"]) if kw.get("data") else None

        if method == "GET" and table == "jobs":
            rows = list(self.jobs.values())
            if "notified=is.false" in path:
                rows = [r for r in rows if not r["notified"]]
            if "verdict=in.(" in path:
                allowed = path.split("verdict=in.(")[1].split(")")[0].split(",")
                rows = [r for r in rows if r["verdict"] in allowed]
            if "job_key=eq." in path:
                k = path.split("job_key=eq.")[1].split("&")[0]
                rows = [r for r in rows if r["job_key"] == k]
            if "job_id=eq." in path:
                k = path.split("job_id=eq.")[1].split("&")[0]
                rows = [r for r in rows if r["job_id"] == k]
            return rows
        if method == "GET" and table == "runs":
            return list(reversed(self.runs))
        if method == "POST" and table == "jobs":
            self.jobs[body["job_key"]] = body          # upsert
            return None
        if method == "POST" and table == "runs":
            self.runs.append(body)
            return None
        if method == "PATCH" and table == "jobs":
            k = path.split("job_key=eq.")[1].split("&")[0]  # already unquoted
            if k in self.jobs:
                self.jobs[k].update(body)
            return None
        if method == "DELETE":
            return None
        return []


def make_job(i="1", title="Software Engineer"):
    return Job(company="Cisco", job_id=i, title=title,
               apply_url=f"https://x/{i}", location="Bangalore, India")


def make_match(v=Verdict.APPLY):
    return MatchResult(verdict=v, reason="needs 1-3 years, you have 1.5",
                       experience=ExperienceRequirement(
                           state=ExperienceState.STATED, min_years=1.0, max_years=3.0,
                           evidence="1-3 years of experience"),
                       seniority=Seniority.EARLY, matched_skills=["Python"], total_skills=10)


def run():
    passed = failed = 0
    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  PASS  {name}" + (f"   {extra}" if extra else ""))
        else:
            failed += 1; print(f"  FAIL  {name}   {extra}")

    fake = FakeRest()
    store = SupabaseStore()
    with mock.patch.object(SupabaseStore, "_request", side_effect=fake, autospec=False):
        # record + read back
        job, match = make_job("2027309"), make_match()
        store.record(job, match)
        check("record writes a row", len(fake.jobs) == 1)
        check("key is company-scoped", "cisco:2027309" in fake.jobs)
        check("payload stored as an object, not a string",
              isinstance(fake.jobs["cisco:2027309"]["payload"], dict))
        check("new rows start undelivered", fake.jobs["cisco:2027309"]["notified"] is False)

        # batched lookup
        fake.calls.clear()
        kv = store.known_verdicts("Cisco")
        check("known_verdicts returns key->verdict", kv == {"cisco:2027309": "APPLY"}, str(kv))
        check("and costs exactly ONE request", len(fake.calls) == 1, str(fake.calls))

        # pending / mark_notified
        check("undelivered job is pending", len(store.pending()) == 1)
        store.mark_notified("cisco:2027309")
        check("marking delivered clears it", len(store.pending()) == 0)
        check("notified_at is stamped", fake.jobs["cisco:2027309"]["notified_at"] is not None)

        # SKIP must never be queued for sending
        store.record(make_job("999", "Senior Staff Engineer"), make_match(Verdict.SKIP))
        check("SKIP is recorded", len(fake.jobs) == 2)
        check("SKIP is never pending", len(store.pending()) == 0)

        # upsert, not duplicate
        store.record(job, make_match(Verdict.CHECK))
        check("re-recording upserts rather than duplicating", len(fake.jobs) == 2)

        # counts / find / runs
        c = store.counts()
        check("counts aggregate by verdict", c["total"] == 2, str(c))
        check("find by job_id works", (store.find("2027309") or {}).get("job_id") == "2027309")
        check("find returns None when absent", store.find("nope") is None)
        store.log_run("Cisco", fetched=15, new_jobs=1, alerted=1, enriched=1)
        check("log_run records a pass", len(fake.runs) == 1)
        check("enriched is carried", fake.runs[0]["enriched"] == 1)

    # 5xx must retry, then give up rather than hang
    import requests
    with mock.patch.object(store._session, "request",
                           side_effect=requests.ConnectionError("boom")) as m:
        try:
            store.known_verdicts()
            check("network failure raises", False)
        except Exception:
            check("network failure raises after retries", m.call_count == 3, f"{m.call_count} attempts")

    print(f"\n  {passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
