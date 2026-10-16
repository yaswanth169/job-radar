"""Phenom adapter, against a captured real response - no network needed."""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

from src.models import CompanyConfig
from src.sources.phenom import PhenomSource

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = json.load(open(os.path.join(HERE, "fixtures", "cisco_live.json")))

CFG = CompanyConfig(
    name="Cisco", adapter="phenom", base_url="https://careers.cisco.com",
    ref_num="CISCISGLOBAL", locale_path="global/en", lang="en_global",
    filters={"country": ["India"],
             "raasJobRequisitionType": ["Entry Level Talent", "Intern", "Co-Op", "Apprentice"]},
    page_size=100,
)

def run():
    passed = failed = 0
    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1; print(f"  PASS  {name}" + (f"   {extra}" if extra else ""))
        else:
            failed += 1; print(f"  FAIL  {name}   {extra}")

    src = PhenomSource(CFG)
    with mock.patch("src.sources.phenom.post_json", return_value=FIXTURE):
        jobs = src.fetch()

    check("parses jobs from a real response", len(jobs) > 0, f"{len(jobs)} jobs")
    check("every job has a title", all(j.title for j in jobs))
    check("every job has a stable id", all(j.job_id for j in jobs))
    check("job ids are unique", len({j.job_id for j in jobs}) == len(jobs))
    check("dedup key is company-scoped", jobs[0].key.startswith("cisco:"), jobs[0].key)

    # The apply link must be the Phenom flow, never the raw Workday URL -
    # Workday loses the pre-filled candidate profile.
    check("apply url points at Phenom, not Workday",
          all("careers.cisco.com" in j.apply_url for j in jobs))
    check("apply url carries jobSeqNo",
          all("jobSeqNo=" in j.apply_url for j in jobs))
    check("no job links to myworkdayjobs",
          not any("myworkdayjobs" in j.apply_url for j in jobs))

    check("descriptions are populated", sum(1 for j in jobs if j.description) == len(jobs))
    check("skills come through from ml_skills", any(j.skills for j in jobs),
          f"{sum(1 for j in jobs if j.skills)}/{len(jobs)} have skills")
    check("requisition type is carried through",
          all(j.requisition_type for j in jobs), jobs[0].requisition_type)
    check("locations are populated", all(j.location for j in jobs))

    print(f"\n  {passed} passed, {failed} failed")
    return failed

if __name__ == "__main__":
    sys.exit(1 if run() else 0)
