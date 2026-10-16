"""The verdict engine must mirror how the user actually decides."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matching.verdict import evaluate
from src.models import Job, Verdict
from src.profile import Profile

P = Profile(
    years=1.5, tolerance=0.5,
    countries=["India"], preferred_locations=["Bangalore", "Remote"],
    want_roles=["software engineer", "backend", "developer", "data engineer"],
    avoid_roles=["sales", "customer success", "finance"],
    skills=["Python", "Java", "AWS"],
)

def job(title, desc="", loc="Bangalore, India", country="India", req="", skills=None):
    return Job(company="Cisco", job_id="1", title=title, apply_url="u",
               description=desc, location=loc, country=country,
               requisition_type=req, skills=skills or [])

CASES = [
    # --- APPLY: stated and within reach ---
    (job("Software Engineer", "1-4 years' experience required"),      Verdict.APPLY),
    (job("Software Engineer", "1+ years of experience"),              Verdict.APPLY),
    (job("Software Engineer", "2+ years of experience"),              Verdict.APPLY),  # 1.5+0.5 tolerance
    (job("Backend Developer", "0-2 years of experience"),             Verdict.APPLY),
    (job("Software Engineer", "Freshers welcome"),                    Verdict.APPLY),
    (job("Software Engineer", "No prior experience required"),        Verdict.APPLY),

    # --- SKIP: asks for more than the user has ---
    (job("Software Engineer", "3+ years of experience"),              Verdict.SKIP),
    (job("Software Engineer", "5-8 years of experience"),             Verdict.SKIP),
    (job("Software Engineer", "minimum 4 years of experience"),       Verdict.SKIP),
    (job("Software Engineer", "8+ years of experience"),              Verdict.SKIP),

    # --- SKIP: senior by title, whatever the description says ---
    (job("Senior Software Engineer", "1+ years of experience"),       Verdict.SKIP),
    (job("Staff Software Engineer", "Freshers welcome"),              Verdict.SKIP),
    (job("Principal Software Engineer", ""),                          Verdict.SKIP),
    (job("Software Engineering Technical Leader", ""),                Verdict.SKIP),

    # --- SKIP: wrong role or wrong place ---
    (job("Sales Engineer", "1+ years of experience"),                 Verdict.SKIP),
    (job("Customer Success Engineer", "fresher"),                     Verdict.SKIP),
    (job("Marketing Analyst", ""),                                    Verdict.SKIP),
    (job("Software Engineer", "1+ years", loc="Austin, Texas", country="United States of America"), Verdict.SKIP),

    # --- CHECK: nothing stated, needs human eyes ---
    (job("Software Engineer", "Join our team and build great things"), Verdict.CHECK),
    (job("Software Engineer", "", req="Entry Level Talent"),           Verdict.CHECK),
    (job("Data Engineer", "We work on large scale pipelines"),         Verdict.CHECK),

    # --- the description must NOT make a junior role look senior ---
    (job("Software Engineer", "You will collaborate with senior engineers on the team"), Verdict.CHECK),
    (job("Software Engineer", "Work with our principal architect and 1+ years experience"), Verdict.APPLY),
]

def run():
    passed = failed = 0
    for j, want in CASES:
        got = evaluate(j, P)
        if got.verdict == want:
            passed += 1
            print(f"  PASS  {want.value:6} {j.title[:40]:42} {got.reason[:46]}")
        else:
            failed += 1
            print(f"  FAIL  want {want.value} got {got.verdict.value}  {j.title!r}")
            print(f"        desc: {j.description[:60]!r}")
            print(f"        reason: {got.reason}")
    print(f"\n  {passed} passed, {failed} failed  ({len(CASES)} cases)")
    return failed

if __name__ == "__main__":
    sys.exit(1 if run() else 0)
