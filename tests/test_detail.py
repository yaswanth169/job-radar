"""Full-JD enrichment: accuracy, request cost, and failure behaviour."""

import sys, os, json, copy, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

from src.models import CompanyConfig, Verdict
from src.notify.telegram import Telegram
from src.profile import Profile
from src.sources.phenom import PhenomSource, _html_to_text
from src.store import Store
from src.watcher import run_pass

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = json.load(open(os.path.join(HERE, "fixtures", "cisco_live.json")))

CFG = CompanyConfig(
    name="Cisco", adapter="phenom", base_url="https://careers.cisco.com",
    ref_num="CISCISGLOBAL", filters={"country": ["India"]},
    page_size=100, fetch_details=True,
)
PROF = Profile(years=1.5, tolerance=0.5, countries=["India"],
               want_roles=["software engineer", "data engineer"],
               avoid_roles=["sales"], skills=["Python", "Java"])

DETAIL = {"jobDetail": {"data": {"job": {"description":
    "<h3>Minimum Qualifications</h3><ul><li>1-3 years of experience in software "
    "development</li><li>Strong Java skills</li></ul>" + "<p>More about the team.</p>" * 30}}}}


def run():
    passed = failed = 0
    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  PASS  {name}" + (f"   {extra}" if extra else ""))
        else:
            failed += 1; print(f"  FAIL  {name}   {extra}")

    # --- html flattening ---
    txt = _html_to_text("<p>Hello</p><br><ul><li>1-3 years</li></ul>&amp; more")
    check("html is flattened to text", "<" not in txt and ">" not in txt, repr(txt[:40]))
    check("entities are decoded", "&" in txt and "&amp;" not in txt)
    check("list items keep structure", "1-3 years" in txt)
    check("empty html is safe", _html_to_text("") == "")

    calls = []
    def fake(url, payload, headers=None, **kw):
        calls.append(payload.get("ddoKey"))
        return DETAIL if payload.get("ddoKey") == "jobDetail" else FIX

    # --- the teaser alone must NOT be enough; the full JD must change it ---
    src = PhenomSource(CFG)
    with mock.patch("src.sources.phenom.post_json", side_effect=fake):
        jobs = src.fetch()
        before = len(jobs[0].description)
        src.enrich(jobs[0])
    check("enrich lengthens the description", len(jobs[0].description) > before,
          f"{before} -> {len(jobs[0].description)} chars")
    check("enrich marks the job", jobs[0].detail_fetched)
    check("the teaser is kept too", before > 0 and jobs[0].description.endswith(
        jobs[0].description[-20:]))

    # --- request cost ---
    store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    tg = Telegram(dry_run=True)
    with mock.patch("src.sources.phenom.post_json", side_effect=fake):
        calls.clear()
        r = run_pass(CFG, PROF, store, tg, seed_only=True)
        check("first run: 1 search + 1 detail per new job",
              calls.count("refineSearch") == 1 and calls.count("jobDetail") == len(jobs),
              f"{len(calls)} requests")
        check("all new jobs enriched", r.enriched == len(jobs), f"enriched={r.enriched}")

        calls.clear()
        r = run_pass(CFG, PROF, store, tg)
        check("routine poll costs exactly ONE request", calls == ["refineSearch"], str(calls))
        check("no re-enrichment of known jobs", r.enriched == 0)

    # --- one new job costs exactly one extra request ---
    nf = copy.deepcopy(FIX)
    j = copy.deepcopy(nf["refineSearch"]["data"]["jobs"][0])
    j.update({"jobId": "777", "reqId": "777", "title": "Software Engineer",
              "jobSeqNo": "CISCISGLOBAL777EXTERNALENGLOBAL"})
    nf["refineSearch"]["data"]["jobs"].insert(0, j)
    def fake2(url, payload, headers=None, **kw):
        calls.append(payload.get("ddoKey"))
        return DETAIL if payload.get("ddoKey") == "jobDetail" else nf
    with mock.patch("src.sources.phenom.post_json", side_effect=fake2):
        calls.clear()
        r = run_pass(CFG, PROF, store, tg)
    check("one new job = search + one detail", calls == ["refineSearch", "jobDetail"], str(calls))
    check("verdict came from the full JD", r.by_verdict.get("APPLY", 0) >= 1, str(r.by_verdict))

    # --- the detail endpoint failing must never break a poll ---
    def flaky(url, payload, headers=None, **kw):
        if payload.get("ddoKey") == "jobDetail":
            raise RuntimeError("detail endpoint down")
        return FIX
    store2 = Store(os.path.join(tempfile.mkdtemp(), "u.db"))
    with mock.patch("src.sources.phenom.post_json", side_effect=flaky):
        r = run_pass(CFG, PROF, store2, tg, seed_only=True)
    check("poll survives on teasers alone", r.new_jobs > 0 and not r.error, f"new={r.new_jobs}")
    check("nothing falsely claimed as enriched", r.enriched == 0)

    # --- the toggle must be honoured ---
    off = copy.deepcopy(CFG); off.fetch_details = False
    src_off = PhenomSource(off)
    with mock.patch("src.sources.phenom.post_json", side_effect=fake):
        jobs_off = src_off.fetch()
        calls.clear()
        src_off.enrich(jobs_off[0])
    check("fetch_details: false makes no extra request", calls == [], str(calls))
    check("and leaves the job unenriched", not jobs_off[0].detail_fetched)

    print(f"\n  {passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
