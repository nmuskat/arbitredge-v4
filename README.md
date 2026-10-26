# ArbitrEdge Needs — Research Prototype

Low-cost, evidence-driven data-collection system for inferring likely NFL/CFB
sportsbook "book need" sides. This is the **research prototype**: it
collects, preserves, and lets you inspect clean, auditable, point-in-time
data. It deliberately does **not** contain any scoring, weighting, or
betting-recommendation logic yet — that comes later, and only after the
collected data has been used to validate whether a signal exists at all.

## Project status

Completed (Steps 1–5 of the approved build order):

1. **Database schema + append-only protections** (`db/`)
2. **Automated backups** (`backup/`)
3. **Canonical team/game identity resolution** (`identity/`)
4. **Shared collector infrastructure** — HTTP retry/backoff, rate limiting,
   provenance, collection logging (`collectors/base_collector.py`)
5. **The Odds API current-odds collector** (spreads, NFL + CFB) (`collectors/odds_collector.py`)

Not yet built: context/schedule collector, Google Trends collector, free
betting-split collectors (VegasInsider/Action Network), the scheduler that
ties collectors together on a cadence, and the read-only Research/Inspection
Mode interface. These are next in the approved build order.

**No model, score, weight, or betting recommendation of any kind exists in
this codebase.** That is intentional — see the project's design philosophy
below.

## Requirements

- Python 3.10+ (developed/tested on 3.12)
- A free or paid API key from **the-odds-api.com** (note the hyphens —
  `theoddsapi.com`, no hyphens, is a confirmed unaffiliated impersonator;
  see `collectors/odds_collector.py` and the project's research history for
  details). The free Starter tier is enough to get started.

## Setup

```bash
# 1. Clone/unzip this project, then from the project root:
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API key (never commit this)
export THE_ODDS_API_KEY="your-real-key-here"   # Windows: set THE_ODDS_API_KEY=...

# 4. Initialize the database (creates db/arbitredge.db, applies schema +
#    append-only triggers, seeds canonical NFL/CFB team identity data)
python3 identity/seed_team_data.py

# 5. Run the full test suite (all mocked, no network required, no API key needed)
python3 tests/test_append_only.py
python3 tests/test_backup_db.py
python3 tests/test_identity_resolution.py
python3 tests/test_base_collector.py
python3 tests/test_odds_collector.py

# 6. Run a real dry-run discovery pass against the live API (requires your
#    API key from step 3; makes real requests but writes ZERO market data)
python3 collectors/run_odds_discovery.py
```

Step 6 is the one thing that requires actual network access and a real key
— everything else runs fully offline against mocked data.

## Project structure

```
arbitredge_prototype/
├── db/
│   ├── schema.sql          # full database schema
│   ├── triggers.sql        # append-only enforcement (raw/log tables can never be UPDATE'd or DELETE'd)
│   ├── migrate.py          # idempotent schema migration
│   └── arbitredge.db       # created on first run; gitignored
├── backup/
│   └── backup_db.py        # timestamped, integrity-checked, retention-bounded backups
├── identity/
│   ├── normalize.py        # conservative text normalization (formatting only, no fuzzy matching)
│   ├── team_resolver.py    # free-text -> canonical_team_id, never guesses
│   ├── game_resolver.py    # canonical game identity, kickoff-tolerance matching, reschedule handling
│   ├── seed_team_data.py   # loads seed_data/*.csv, checks for alias conflicts before accepting them
│   └── seed_data/          # NFL (32 teams) + CFB (62 teams) canonical identity + aliases
├── collectors/
│   ├── config.py           # env-based secrets, redaction helpers, shared tunables
│   ├── errors.py           # structured exception hierarchy
│   ├── base_collector.py   # shared retry/backoff/budget/logging/provenance infrastructure
│   ├── odds_collector.py   # the-odds-api.com current spreads odds (NFL + CFB)
│   └── run_odds_discovery.py  # manual local script: real dry-run + discovery pass
├── tests/                  # mocked, offline tests for every component above
├── requirements.txt
└── README.md                (this file)
```

## Design philosophy (why the code looks the way it does)

- **Raw data is append-only.** `market_snapshots`, `game_context`,
  `split_observations`, `collection_log`, `raw_responses`, and
  `game_reschedule_log` all reject `UPDATE`/`DELETE` at the database trigger
  level, not just by convention. Nothing downstream (research, modeling)
  can ever silently rewrite history.
- **Never guess.** Team/game identity resolution is exact-match-first, with
  narrow, conservative normalization for formatting only. Ambiguous or
  unknown inputs are logged for review and skipped — never silently
  resolved via a best guess.
- **Provenance over convenience.** Every parsed observation traces back to
  the exact raw HTTP response that produced it, the parser version that
  parsed it, and the timestamps involved (scheduled/executed/retrieved/
  source-reported), all kept distinct.
- **Low cost by design.** The odds collector defaults to spreads-only, one
  region, and includes a request-budget ceiling and an idempotency guard
  that blocks accidental reruns *before* they spend an API credit.
- **No premature modeling.** Every component through Step 5 is
  collection/identity/infrastructure only. No scoring, weighting, or
  recommendation logic exists anywhere in this codebase.

## A note on network access

This project was built and tested in a sandboxed environment with no
network access to the-odds-api.com. Every component is fully covered by
mocked tests that require no network and no API key. The one exception is
`collectors/run_odds_discovery.py`, which makes real requests and must be
run locally with your own key — see Setup step 6 above.
