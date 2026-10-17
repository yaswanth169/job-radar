"""Fan-out to several recipients, each with their own bot. No network."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest import mock

import requests

# Aliased: this module already uses FAILED for its own results list, and
# letting the status constant shadow it made a comparison silently wrong.
from src.notify.telegram import (FAILED as S_FAILED, OK as S_OK,
                                 REJECTED as S_REJECTED, Recipient, Telegram,
                                 load_recipients, resolved)

PASSED = []
FAILED = []


def check(name, cond):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class FakeResponse:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeApi:
    """Records every POST and replies per-token from a script."""

    def __init__(self, by_token=None):
        self.by_token = by_token or {}
        self.sent = []

    def __call__(self, url, json=None, timeout=None):
        token = url.split("/bot")[1].split("/")[0]
        self.sent.append((token, json["chat_id"], json["text"]))
        return self.by_token.get(token, FakeResponse())


R1 = Recipient("primary", "111:aaa", "1001")
R2 = Recipient("recipient2", "222:bbb", "2002")


def test_both_recipients_get_the_same_text():
    api = FakeApi()
    tg = Telegram(recipients=[R1, R2])
    with mock.patch("requests.post", api), mock.patch("time.sleep"):
        out = tg.send("<b>hello</b>")
    check("every recipient reported ok", out == {"primary": S_OK, "recipient2": S_OK})
    check("all resolved", resolved(out) is True)
    check("one post per recipient", len(api.sent) == 2)
    check("each went through its own bot", {s[0] for s in api.sent} == {"111:aaa", "222:bbb"})
    check("each went to its own chat", {s[1] for s in api.sent} == {"1001", "2002"})
    check("identical text to both", api.sent[0][2] == api.sent[1][2])


def test_transient_failure_leaves_job_queued():
    api = FakeApi({"222:bbb": FakeResponse(500, "server error")})
    tg = Telegram(recipients=[R1, R2])
    with mock.patch("requests.post", api), mock.patch("time.sleep"):
        out = tg.send("hi")
    check("transient failure -> not resolved (stays queued)", resolved(out) is False)
    check("the failure is attributed to the right person",
          out == {"primary": S_OK, "recipient2": S_FAILED})
    check("the healthy recipient still got it", ("111:aaa", "1001", "hi") in api.sent)
    check("transient failure was retried 3x", sum(1 for s in api.sent if s[0] == "222:bbb") == 3)


def test_permanent_rejection_does_not_jam_the_queue():
    """A blocked bot must not re-send to everyone else forever."""
    api = FakeApi({"222:bbb": FakeResponse(403, "bot was blocked by the user")})
    tg = Telegram(recipients=[R1, R2])
    with mock.patch("requests.post", api), mock.patch("time.sleep"):
        out = tg.send("hi")
    check("permanent rejection -> resolved (no infinite duplicates)", resolved(out) is True)
    check("rejection is reported, not silently counted as success",
          out == {"primary": S_OK, "recipient2": S_REJECTED})
    check("permanent rejection not retried", sum(1 for s in api.sent if s[0] == "222:bbb") == 1)
    check("healthy recipient delivered once", sum(1 for s in api.sent if s[0] == "111:aaa") == 1)


def test_send_jobs_marks_only_fully_resolved():
    api = FakeApi({"222:bbb": FakeResponse(503, "unavailable")})
    tg = Telegram(recipients=[R1, R2])
    jobs = [{"company": "Cisco", "job_id": "1", "title": "SWE", "verdict": "APPLY"}]
    with mock.patch("requests.post", api), mock.patch("time.sleep"):
        report = tg.send_jobs(jobs)
    check("job not marked delivered while a recipient is retryable", report.delivered == [])
    check("the outcome is still recorded per recipient",
          report.outcomes == {"primary": {S_OK: 1}, "recipient2": {S_FAILED: 1}})
    check("the broken recipient is flagged", report.unhealthy == ["recipient2"])


def test_single_recipient_behaves_exactly_as_before():
    api = FakeApi()
    tg = Telegram(recipients=[R1])
    with mock.patch("requests.post", api), mock.patch("time.sleep"):
        out = tg.send("hi")
    check("single recipient still delivers", resolved(out) and len(api.sent) == 1)


def test_env_loading():
    env = {
        "TELEGRAM_BOT_TOKEN": "111:aaa", "TELEGRAM_CHAT_ID": "1001",
        "TELEGRAM_BOT_TOKEN_2": "222:bbb", "TELEGRAM_CHAT_ID_2": "2002",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        rs = load_recipients()
    check("two pairs load from env", [r.chat_id for r in rs] == ["1001", "2002"])

    with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "1:a", "TELEGRAM_CHAT_ID": "9"}, clear=True):
        rs = load_recipients()
    check("legacy single-pair config still loads", len(rs) == 1)

    # A half-filled pair is a config mistake, not a recipient.
    partial = {
        "TELEGRAM_BOT_TOKEN": "111:aaa", "TELEGRAM_CHAT_ID": "1001",
        "TELEGRAM_BOT_TOKEN_2": "222:bbb",
    }
    with mock.patch.dict(os.environ, partial, clear=True):
        rs = load_recipients()
    check("incomplete second pair is ignored", len(rs) == 1)

    with mock.patch.dict(os.environ, {}, clear=True):
        check("no config -> not configured", Telegram(recipients=load_recipients()).configured is False)


for fn in [
    test_both_recipients_get_the_same_text,
    test_transient_failure_leaves_job_queued,
    test_permanent_rejection_does_not_jam_the_queue,
    test_send_jobs_marks_only_fully_resolved,
    test_single_recipient_behaves_exactly_as_before,
    test_env_loading,
]:
    print(f"\n{fn.__name__}")
    fn()

print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
