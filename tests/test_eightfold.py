"""Eightfold adapter and the per-company role override. No network."""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

from src.matching.verdict import evaluate
from src.models import CompanyConfig, Job
from src.profile import load_profile
from src.sources.eightfold import EightfoldSource, _job_posting_ld, _to_text

PASSED, FAILED = [], []


def check(name, cond):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


FIXTURE = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "qualcomm_jobs.json")))


def config(**kw):
    base = dict(
        name="Qualcomm", adapter="eightfold",
        base_url="https://careers.qualcomm.com",
        filters={"location": "India"},
        extra={"domain": "qualcomm.com", "max_pages": 3},
    )
    base.update(kw)
    return CompanyConfig(**base)


def page(start, total=25):
    """A search page shaped like the real one, newest-first."""
    return {"data": {"count": total, "positions": [
        {"id": 900 + start + i, "displayJobId": f"30{start + i:03d}",
         "name": f"Engineer {start + i}", "locations": ["Bengaluru, KA, India"],
         "positionUrl": f"/careers/job/{900 + start + i}",
         "postedTs": 1792108800 - (start + i) * 86400,
         "workLocationOption": "onsite", "department": "Eng"}
        for i in range(10)]}}


def test_pagination_respects_max_pages():
    calls = []

    def fake_get_json(url, params, headers=None, **kw):
        calls.append(params["start"])
        return page(params["start"], total=500)

    with mock.patch("src.sources.eightfold.get_json", fake_get_json):
        jobs = EightfoldSource(config()).fetch()
    check("stops at max_pages", calls == [0, 10, 20])
    check("10 per page is the cap we send", len(jobs) == 30)
    check("newest first preserved", jobs[0].posted_date > jobs[-1].posted_date)


def test_stops_early_when_results_run_out():
    def fake_get_json(url, params, headers=None, **kw):
        return page(params["start"], total=12)

    with mock.patch("src.sources.eightfold.get_json", fake_get_json):
        jobs = EightfoldSource(config()).fetch()
    check("stops once count is exhausted", len(jobs) == 20)


def test_location_filter_is_sent():
    seen = {}

    def fake_get_json(url, params, headers=None, **kw):
        seen.update(params)
        return {"data": {"count": 0, "positions": []}}

    with mock.patch("src.sources.eightfold.get_json", fake_get_json):
        EightfoldSource(config()).fetch()
    check("location passed as a string", seen.get("location") == "India")
    check("sorted newest-first", seen.get("sort_by") == "timestamp")
    check("domain sent", seen.get("domain") == "qualcomm.com")


def test_jd_extracted_from_json_ld():
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"http://schema.org","@type":"WebSite","name":"ignore me"}'
        '</script><script type="application/ld+json">'
        '{"@context":"http://schema.org","@type":"JobPosting","employmentType":"FULL_TIME",'
        '"description":"Minimum Qualifications: Bachelor degree and 2+ years of relevant work '
        'experience. OR Master degree and 1+ year of relevant work experience. ' + ("padding. " * 30) + '"}'
        "</script></head><body></body></html>"
    )
    job = Job(company="Qualcomm", job_id="1", title="Engineer", apply_url="",
              job_url="https://careers.qualcomm.com/careers/job/1")
    with mock.patch("src.sources.eightfold.get_text", lambda *a, **k: html):
        EightfoldSource(config()).enrich(job)
    check("picks the JobPosting blob, not the first script", "Minimum Qualifications" in job.description)
    check("detail_fetched set", job.detail_fetched is True)
    check("employmentType carried over", job.employment_type == "FULL_TIME")


def test_enrich_survives_a_missing_blob():
    job = Job(company="Qualcomm", job_id="1", title="Engineer", apply_url="",
              job_url="https://careers.qualcomm.com/careers/job/1")
    with mock.patch("src.sources.eightfold.get_text", lambda *a, **k: "<html>nothing</html>"):
        EightfoldSource(config()).enrich(job)
    check("no blob -> no crash, no description", job.detail_fetched is False)

    def boom(*a, **k):
        raise OSError("network down")

    with mock.patch("src.sources.eightfold.get_text", boom):
        EightfoldSource(config()).enrich(job)
    check("fetch failure -> no crash", job.detail_fetched is False)


def test_lowest_minimum_wins_on_a_real_jd():
    """The real Qualcomm pattern: several degree-dependent floors in one JD.

    "Bachelor's and 2+ years OR Master's and 1+ year OR PhD" - the floor
    that actually gates you is the lowest, so that is what must come back.
    """
    real = [c for c in FIXTURE if c["title"] == "Memory CAD Software Engineer"]
    check("fixture has the multi-floor JD", len(real) == 1)
    if real:
        job = Job(company="Qualcomm", job_id="x", title=real[0]["title"],
                  apply_url="", description=real[0]["desc"])
        exp = evaluate(job, load_profile()).experience
        check("parses the lowest stated floor", exp.min_years == 1.0)


def test_an_explicit_range_outranks_the_degree_floors():
    """A JD that states both keeps the range - it is the more specific claim.

    The Senior Memory Characterization JD lists "1+/2+/3+ years" by degree
    AND "(5-12 years) experience in VLSI". The range is the real bar, and
    reading the floors instead would wrongly surface a senior role.
    """
    real = [c for c in FIXTURE if c["title"].startswith("Senior Memory")]
    if not real:
        check("fixture has the competing-range JD", False)
        return
    job = Job(company="Qualcomm", job_id="y", title=real[0]["title"],
              apply_url="", description=real[0]["desc"])
    m = evaluate(job, load_profile().for_company({"want": []}))
    check("range wins over the degree floors", m.experience.min_years == 5.0)
    check("and the senior role is skipped", m.verdict.value == "SKIP")


def test_want_list_override_is_what_lets_qualcomm_through():
    """The regression this whole config exists to prevent."""
    base = load_profile()
    assoc = [c for c in FIXTURE if c["title"] == "Associate Engineer"]
    check("fixture has the Associate Engineer case", len(assoc) == 1)
    if not assoc:
        return
    job = Job(company="Qualcomm", job_id="a", title="Associate Engineer",
              apply_url="", description=assoc[0]["desc"], location="Bengaluru, KA, India")

    with_list = evaluate(job, base)
    check("Cisco-tuned want-list wrongly drops it", with_list.verdict.value == "SKIP")

    overridden = evaluate(job, base.for_company({"want": []}))
    check("override lets it through", overridden.verdict.value != "SKIP")
    check("base profile not mutated", len(base.want_roles) > 0)


def test_override_does_not_leak_between_companies():
    base = load_profile()
    base.for_company({"want": []})
    check("Cisco keeps its want-list", len(base.for_company(None).want_roles) > 0)


for fn in [
    test_pagination_respects_max_pages,
    test_stops_early_when_results_run_out,
    test_location_filter_is_sent,
    test_jd_extracted_from_json_ld,
    test_enrich_survives_a_missing_blob,
    test_lowest_minimum_wins_on_a_real_jd,
    test_an_explicit_range_outranks_the_degree_floors,
    test_want_list_override_is_what_lets_qualcomm_through,
    test_override_does_not_leak_between_companies,
]:
    print(f"\n{fn.__name__}")
    fn()

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
