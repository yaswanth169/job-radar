"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from src.matching.verdict import evaluate
from src.models import Verdict
from src.notify.telegram import Telegram, format_job, _strip_html
from src.profile import load_profile
from src.sources.registry import build_source, load_company_configs
from src.store import Store, _job_dict
from src.watcher import Watcher, run_pass


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _companies(only: str | None):
    configs = [c for c in load_company_configs() if c.enabled]
    if only:
        wanted = {o.strip().lower() for o in only.split(",")}
        configs = [c for c in configs if c.name.lower() in wanted]
        if not configs:
            print(f"No enabled company matched '{only}'.", file=sys.stderr)
            sys.exit(2)
    return configs


def cmd_check(args) -> int:
    """One pass, printed. Sends nothing unless --send is given."""
    profile = load_profile()
    configs = _companies(args.company)
    print(
        f"Profile: {profile.years}y experience, tolerance {profile.tolerance} "
        f"-> APPLY when a role asks for <= {profile.max_years}y\n"
    )
    total = {"APPLY": 0, "CHECK": 0, "SKIP": 0}
    for config in configs:
        source = build_source(config)
        jobs = source.fetch()
        detail = config.fetch_details and not args.no_detail
        print(f"=== {config.name}: {len(jobs)} jobs matching server-side filters ===")
        if detail:
            print(f"    loading full job descriptions ({len(jobs)} requests)...\n")
        else:
            print("    teaser only (--no-detail)\n")
        rows = []
        for job in jobs:
            if detail:
                source.enrich(job)
            match = evaluate(job, profile)
            total[match.verdict.value] = total.get(match.verdict.value, 0) + 1
            rows.append((job, match))
        order = {Verdict.APPLY: 0, Verdict.CHECK: 1, Verdict.SKIP: 2}
        rows.sort(key=lambda r: (order[r[1].verdict], r[0].title.lower()))
        for job, match in rows:
            if args.verdict and match.verdict.value != args.verdict.upper():
                continue
            mark = {"APPLY": "[+]", "CHECK": "[?]", "SKIP": "[-]"}[match.verdict.value]
            print(f"{mark} {match.verdict.value:6} {job.title[:50]:52} {match.experience.describe():22} {match.reason}")
            if args.show_messages and match.is_alertable:
                print(_strip_html(format_job(_job_dict(job, match))))
                print()
        print()
    print(f"Totals: {total}")

    if args.send:
        store = Store()
        telegram = Telegram(dry_run=False)
        if not telegram.configured:
            print("Telegram is not configured - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
            return 1
        for config in configs:
            res = run_pass(config, profile, store, telegram)
            print(f"{res.company}: alerted {res.alerted}, new {res.new_jobs}")
    return 0


def cmd_poll(args) -> int:
    """Exactly one pass per company, then exit. The CI / cron primitive.

    `watch` loops forever, `check` is a read-only preview. This is what a
    scheduled runner invokes: fetch once, alert, persist, exit.
    """
    profile = load_profile()
    store = Store()
    telegram = Telegram(dry_run=args.dry_run)
    if not args.dry_run and not telegram.configured:
        print("Telegram is not configured - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return 1
    worst = 0
    for config in _companies(args.company):
        res = run_pass(config, profile, store, telegram)
        print(
            f"{res.company}: fetched={res.fetched} new={res.new_jobs} "
            f"full-jd={res.enriched} alerted={res.alerted} {res.by_verdict or {}}"
            + (f" ERROR: {res.error}" if res.error else "")
        )
        if res.error:
            worst = 1
    return worst


def cmd_seed(args) -> int:
    """Record everything currently posted as seen, without alerting."""
    profile = load_profile()
    store = Store()
    telegram = Telegram(dry_run=True)
    for config in _companies(args.company):
        res = run_pass(config, profile, store, telegram, seed_only=True)
        print(f"{res.company}: {res.fetched} jobs marked as already-seen ({res.new_jobs} new to the db)")
    print("\nSeeded. From now on only genuinely new postings will alert you.")
    return 0


def cmd_watch(args) -> int:
    profile = load_profile()
    store = Store()
    telegram = Telegram(dry_run=args.dry_run)
    if not args.dry_run and not telegram.configured:
        print("Telegram is not configured - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 1
    Watcher(_companies(args.company), profile, store, telegram).run()
    return 0


def cmd_explain(args) -> int:
    """Why did this job get the verdict it got?"""
    store = Store()
    record = store.find(args.job_id)
    if not record:
        print(f"No job with id '{args.job_id}' in the database yet. Run 'check' or 'watch' first.")
        return 1
    print(f"\n{record['title']}  ({record['company']}, req {record['job_id']})")
    print(f"  verdict     : {record['verdict']}")
    print(f"  reason      : {record['reason']}")
    print(f"  experience  : {record['experience']}")
    if record.get("experience_evidence"):
        print(f"  evidence    : \"{record['experience_evidence']}\"")
    print(f"  requisition : {record.get('requisition_type') or '-'}")
    print(f"  location    : {record.get('location') or '-'}")
    print(f"  skills      : {len(record.get('matched_skills') or [])}/{record.get('total_skills', 0)} matched")
    if record.get("matched_skills"):
        print(f"                {', '.join(record['matched_skills'])}")
    print(f"  apply       : {record.get('apply_url')}\n")
    return 0


def cmd_status(args) -> int:
    store = Store()
    print("Database:", store.db_path)
    print("Jobs    :", store.counts())
    print("\nRecent passes:")
    for row in store.last_runs(12):
        err = f"  ERROR {row['error']}" if row["error"] else ""
        print(f"  {row['ran_at'][:19]}  {row['company']:10} "
              f"fetched={row['fetched']} new={row['new_jobs']} alerted={row['alerted']}{err}")
    return 0


def cmd_test_telegram(args) -> int:
    t = Telegram(dry_run=args.dry_run)
    if not args.dry_run and not t.configured:
        print("Telegram is not configured - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 1
    return 0 if t.send_test() else 1


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="job-radar", description="Experience-aware job watcher")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="one pass, printed; sends nothing by default")
    p.add_argument("--company")
    p.add_argument("--verdict", choices=["apply", "check", "skip"], help="show only this verdict")
    p.add_argument("--show-messages", action="store_true", help="render the Telegram messages")
    p.add_argument("--no-detail", action="store_true",
                   help="skip the full-JD fetch (faster, far less accurate)")
    p.add_argument("--send", action="store_true", help="actually send and persist")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("poll", help="one pass per company, then exit (for cron / CI)")
    p.add_argument("--company")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("seed", help="mark everything currently posted as seen")
    p.add_argument("--company")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("watch", help="poll forever on each company's interval")
    p.add_argument("--company")
    p.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("explain", help="why a job got its verdict")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("status", help="database and recent run summary")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("test-telegram", help="send a test message")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_test_telegram)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    sys.exit(args.func(args))
