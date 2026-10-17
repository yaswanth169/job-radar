"""The watchdog: staleness, delivery faults and the alert throttle.

No network. The point of these is the failure modes that look like
success - a recipient Telegram refuses forever, and a poller that has
stopped during a weekend when silence is expected anyway.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from src.health import (
    HEARTBEAT_MARKER, assess, check_delivery, check_freshness,
    should_alert, STALE_AFTER_HOURS, REALERT_AFTER_HOURS,
)

PASSED, FAILED = [], []
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def check(name, cond):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def run(company, minutes_ago, error=None, delivery=None):
    return {
        "company": company,
        "ran_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "error": error,
        "delivery": delivery,
    }


def test_a_live_radar_is_quiet():
    rows = [run("Cisco", 3), run("Qualcomm", 4)]
    h = check_freshness(rows, ["Cisco", "Qualcomm"], NOW)
    check("fresh passes raise nothing", h.ok)
    check("but are still reported as notes", len(h.notes) == 2)


def test_a_stopped_company_is_caught():
    rows = [run("Cisco", 3), run("Qualcomm", int(STALE_AFTER_HOURS * 60) + 30)]
    h = check_freshness(rows, ["Cisco", "Qualcomm"], NOW)
    check("stale company is a problem", not h.ok)
    check("names the stale one", any("Qualcomm" in p for p in h.problems))
    check("does not blame the healthy one", not any("Cisco" in p for p in h.problems))


def test_errors_do_not_count_as_alive():
    """Qualcomm 429ing every 5 minutes must not look like a working radar."""
    rows = [run("Qualcomm", m, error="blocked: HTTP 429") for m in range(0, 600, 5)]
    rows.append(run("Qualcomm", int(STALE_AFTER_HOURS * 60) + 60))
    h = check_freshness(rows, ["Qualcomm"], NOW)
    check("a storm of failed runs is still stale", not h.ok)


def test_a_company_that_never_ran():
    h = check_freshness([run("Cisco", 3)], ["Cisco", "PayPal"], NOW)
    check("never-run company is reported", any("PayPal" in p for p in h.problems))


def test_a_rejected_recipient_is_the_whole_point():
    """The failure that currently looks identical to success."""
    rows = [run("Cisco", 5, delivery={"primary": {"ok": 2},
                                      "recipient2": {"rejected": 2}})]
    h = check_delivery(rows, {})
    check("rejection is a problem", not h.ok)
    check("names the recipient", any("recipient2" in p for p in h.problems))
    check("does not accuse the working one", not any("primary" in p for p in h.problems))
    check("confirms the working one received", any("primary" in n for n in h.notes))


def test_transient_failure_alone_is_not_a_fault():
    rows = [run("Cisco", 5, delivery={"primary": {"ok": 3, "failed": 1}})]
    check("a retryable blip with successes is tolerated", check_delivery(rows, {}).ok)

    rows = [run("Cisco", 5, delivery={"primary": {"failed": 4}})]
    check("failures with no success at all is a fault", not check_delivery(rows, {}).ok)


def test_a_dead_token_is_caught_without_sending():
    h = check_delivery([], {"primary": "ok", "recipient2": "rejected"})
    check("dead token reported", any("recipient2" in p for p in h.problems))
    check("live token not reported", not any("primary" in p for p in h.problems))


def test_no_alerts_means_delivery_is_unverified_not_healthy():
    h = check_delivery([run("Cisco", 5)], {"primary": "ok"})
    check("silence is not proof of delivery", any("unverified" in n for n in h.notes))
    check("but silence alone is not a problem", h.ok)


def test_throttle_stops_hourly_nagging():
    recent = [{"company": HEARTBEAT_MARKER,
               "ran_at": (NOW - timedelta(hours=1)).isoformat(),
               "error": "Qualcomm stale"}]
    check("stays quiet right after alerting", not should_alert(recent, NOW))

    old = [{"company": HEARTBEAT_MARKER,
            "ran_at": (NOW - timedelta(hours=REALERT_AFTER_HOURS + 1)).isoformat(),
            "error": "Qualcomm stale"}]
    check("speaks up again once it has been long enough", should_alert(old, NOW))
    check("first ever alert is allowed", should_alert([], NOW))


def test_heartbeat_rows_cannot_fake_a_live_radar():
    """A heartbeat row is not a company pass - it must not mask staleness."""
    rows = [{"company": HEARTBEAT_MARKER, "ran_at": NOW.isoformat(), "error": None}]
    h = check_freshness(rows, ["Cisco"], NOW)
    check("heartbeat row does not stand in for Cisco", not h.ok)


def test_report_renders_both_ways():
    good = assess([run("Cisco", 2)], ["Cisco"], {"primary": "ok"}, now=NOW)
    check("healthy report says so", "all healthy" in good.report())

    bad = assess([run("Cisco", 10_000)], ["Cisco"], {"primary": "ok"}, now=NOW)
    check("unhealthy report lists the problem", "Cisco" in bad.report())
    check("unhealthy report is flagged", "needs attention" in bad.report())


def test_malformed_timestamps_do_not_crash():
    rows = [{"company": "Cisco", "ran_at": "not-a-date", "error": None},
            {"company": "Cisco", "ran_at": "", "error": None}]
    h = check_freshness(rows, ["Cisco"], NOW)
    check("garbage timestamps are ignored, not fatal", not h.ok)


for fn in [
    test_a_live_radar_is_quiet,
    test_a_stopped_company_is_caught,
    test_errors_do_not_count_as_alive,
    test_a_company_that_never_ran,
    test_a_rejected_recipient_is_the_whole_point,
    test_transient_failure_alone_is_not_a_fault,
    test_a_dead_token_is_caught_without_sending,
    test_no_alerts_means_delivery_is_unverified_not_healthy,
    test_throttle_stops_hourly_nagging,
    test_heartbeat_rows_cannot_fake_a_live_radar,
    test_report_renders_both_ways,
    test_malformed_timestamps_do_not_crash,
]:
    print(f"\n{fn.__name__}")
    fn()

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
