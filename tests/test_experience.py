"""The experience parser is the brain of this project. Test it hard."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matching.experience import parse_experience
from src.models import ExperienceState as S

CASES = [
    # (text, expected_state, expected_min, expected_max)
    # --- the 13 phrasings the OLD project missed ---
    ("Requires 1+ years of experience",                      S.STATED, 1.0,  None),
    ("1.5 years of experience in backend",                   S.STATED, 1.5,  None),
    ("2+ years of experience with Java",                     S.STATED, 2.0,  None),
    ("3+ years of professional experience",                  S.STATED, 3.0,  None),
    ("minimum 2 years of experience required",               S.STATED, 2.0,  None),
    ("at least 1 year of experience",                        S.STATED, 1.0,  None),
    ("1-2 years of experience preferred",                    S.STATED, 1.0,  2.0),
    ("2-4 years of relevant experience",                     S.STATED, 2.0,  4.0),
    ("1 to 4 years of experience",                           S.STATED, 1.0,  4.0),
    ("4-7 years of experience in distributed systems",       S.STATED, 4.0,  7.0),
    ("Experience: 6 months to 2 years",                      S.STATED, 0.5,  2.0),
    ("No prior experience required",                         S.EARLY_CAREER, 0.0, None),
    ("This is an early-career opportunity",                  S.EARLY_CAREER, 0.0, None),
    # --- the 7 the old project did get ---
    ("0-1 years of experience",                              S.STATED, 0.0,  1.0),
    ("1-3 years of experience",                              S.STATED, 1.0,  3.0),
    ("2-5 years of experience",                              S.STATED, 2.0,  5.0),
    ("Fresher welcome to apply",                             S.EARLY_CAREER, 0.0, None),
    ("Entry level role",                                     S.EARLY_CAREER, 0.0, None),
    # --- senior detection ---
    ("8+ years of experience leading teams",                 S.SENIOR, 8.0,  None),
    ("10+ years of industry experience",                     S.SENIOR, 10.0, None),
    # --- unknown must stay unknown ---
    ("We build software that powers the internet.",          S.UNKNOWN, None, None),
    ("Join our team and grow with us!",                      S.UNKNOWN, None, None),
    ("",                                                     S.UNKNOWN, None, None),
    # --- must NOT false-positive on non-requirement numbers ---
    ("Cisco has shipped this product for 20 years.",         S.UNKNOWN, None, None),
    ("Our team has grown 3 years running.",                  S.UNKNOWN, None, None),
    # --- the real Cisco teaser from the live API ---
    ("Seeking a Software Engineer to design high-performance system software "
     "for Cisco Meraki. Ideal for candidates with 1-4 years' experience and "
     "strong C/C++/Python skills.",                          S.STATED, 1.0,  4.0),
    # --- messy real-world forms ---
    ("Candidates should bring 2 years experience in Python", S.STATED, 2.0,  None),
    ("Looking for someone with over 5 years of experience",  S.STATED, 5.0,  None),
    ("6 months of relevant experience is a plus",            S.STATED, 0.5,  None),
]

def run():
    passed = failed = 0
    for text, want_state, want_min, want_max in CASES:
        got = parse_experience(text)
        ok = got.state == want_state
        if want_min is not None:
            ok = ok and got.min_years == want_min
        if want_max is not None:
            ok = ok and got.max_years == want_max
        elif got.state == S.STATED and want_max is None:
            ok = ok and got.max_years is None
        if ok:
            passed += 1
            print(f"  PASS  {got.describe():22} <- {text[:58]!r}")
        else:
            failed += 1
            print(f"  FAIL  got state={got.state.value} min={got.min_years} max={got.max_years}")
            print(f"        want state={want_state.value} min={want_min} max={want_max}")
            print(f"        text: {text[:70]!r}")
    print(f"\n  {passed} passed, {failed} failed  ({len(CASES)} cases)")
    return failed

if __name__ == "__main__":
    sys.exit(1 if run() else 0)
