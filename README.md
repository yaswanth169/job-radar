# job-radar

An experience-aware job watcher. It answers the question you actually ask —
**"can I apply to this, with the experience I have?"** — instead of
"does this title contain engineering words?".

Polls a company's careers API on an interval, and sends a Telegram alert only
for roles that fit your profile.

## Why this exists

A keyword scorer ranks *Principal Engineer* and *Engineer I* identically, and
cannot read `1+ years` out of a job description. This one models the human
workflow instead:

1. Filter to early-career roles **server-side** — the same thing you do when
   you click "Early Careers" on a careers site.
2. Read the experience requirement out of each description.
3. Compare it to your own experience.
4. Only tell you about the ones you could actually get.

## Verdicts

| | Meaning | Sent? |
|---|---|---|
| 🟢 **APPLY** | Stated minimum is within `years + tolerance` | yes |
| 🟡 **CHECK** | No experience stated, but it reads early-career | yes, tagged |
| 🔴 **SKIP**  | Asks for more than you have, or is senior | no, logged only |

`CHECK` exists because "nothing stated" is a genuinely different decision from
"stated and too high" — it is the bucket you open and read yourself.

## Setup

```bash
cd job-radar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add your Telegram bot token + chat id
$EDITOR config/profile.yaml   # set your years, locations, roles
```

## Usage

```bash
# What would it say right now? Sends nothing.
python -m src check
python -m src check --verdict apply
python -m src check --show-messages      # render the Telegram cards

# Mark everything currently posted as already-seen, so the first
# real run does not fire dozens of alerts for old jobs.
python -m src seed

# Poll forever on each company's interval.
python -m src watch
python -m src watch --dry-run            # print instead of sending

# Why did this job get that verdict?
python -m src explain 2027309

python -m src status                     # db + recent passes
python -m src test-telegram
```

## Configuration

**`config/profile.yaml`** — who you are. Gitignored; it is yours.

```yaml
experience:
  years: 1.5
  tolerance: 0.5     # 1.5 + 0.5 means "2+ years" roles still qualify
locations:
  countries: [India]
roles:
  want:  [software engineer, backend, ...]
  avoid: [sales, customer success, ...]
skills: [Java, Python, AWS, ...]
```

**`config/companies/*.yaml`** — one file per company.

```yaml
name: Cisco
adapter: phenom
base_url: https://careers.cisco.com
ref_num: CISCISGLOBAL
poll_seconds: 300
filters:
  country: [India]
  raasJobRequisitionType: [Entry Level Talent, Intern, Co-Op, Apprentice]
```

Adding another Phenom-powered company is **one more YAML file, no code**.

## Full job descriptions

The search API returns only a ~300 character marketing teaser, which almost
never states an experience requirement. The same `/widgets` endpoint with
`ddoKey: "jobDetail"` returns the real 4-9k character JD, which usually does.

Measured on Cisco's 15 current early-career India roles:

| | teaser only | with full JDs |
|---|---|---|
| APPLY | 1 | **11** |
| CHECK | 14 | **2** |
| SKIP | 0 | **2** |

Only jobs we have never seen before are enriched, so the cost is:

```
routine poll, nothing new   ->  1 request
poll with one new job       ->  2 requests
very first run (15 jobs)    ->  16 requests
```

If the detail endpoint is unavailable the poll still succeeds on teasers
alone - it degrades, it does not fail. Turn it off per company with
`fetch_details: false`, or per invocation with `check --no-detail`.

## How the Cisco source works

`careers.cisco.com` is a JavaScript app — its HTML contains the string
"Software Engineer" **zero times**, so scraping it returns nothing useful.
The jobs come from a separate API call the page makes after loading.

That API, `POST /widgets`, answers with **no cookies, no JWT and no CSRF
token**. The tenant is identified by `refNum` inside the POST body, which is
why probing the URL with query parameters only ever returns
`"Tenant not identified"`.

Two details that matter:

- **`raasJobRequisitionType`** is Cisco's own early-career classification,
  exposed as a filter parameter. That is the "Early Careers" button, as an API.
- **The apply link is built from `jobSeqNo`, not the `applyUrl` field.**
  `applyUrl` points at raw Workday; every job carries `externalApply: false`,
  meaning the site deliberately uses its own apply flow — which is pre-filled
  from your stored candidate profile. Sending you to Workday would lose that.

## Tests

```bash
python tests/run_all.py
```

- `test_experience.py` — 29 real-world phrasings, including every `N+ years`
  form, plus negative cases that must *not* parse as requirements.
- `test_verdict.py` — 23 APPLY/CHECK/SKIP decisions.
- `test_phenom.py` — the adapter against a captured live response. No network.
- `test_detail.py` — full-JD enrichment: accuracy, request cost, the
  `fetch_details` toggle, and graceful degradation when the endpoint fails.

## Design notes

- **Experience is read from the description, seniority from the title.**
  Mixing them makes any JD that says "work with senior engineers" look senior.
- **`UNKNOWN` is a first-class state**, not the string "Not specified".
- **Jobs are marked delivered only after Telegram confirms.** A failed send
  queues the job for the next cycle instead of silencing it forever.
- **State is a local SQLite file**, not a CI cache that can be evicted.

## Layout

```
config/profile.yaml        you
config/companies/*.yaml    what to watch
src/sources/phenom.py      Cisco + any Phenom site (search + full JD)
src/matching/experience.py parse "1+ years" out of prose
src/matching/seniority.py  title + requisition type
src/matching/verdict.py    APPLY / CHECK / SKIP
src/store.py               SQLite, dedup, delivery queue
src/notify/telegram.py     alerts
src/watcher.py             the loop
```
