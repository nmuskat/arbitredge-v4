# One-time historical backfill (owner-approved 2026-11-10)

Purpose: replay the synthetic book on the whole 2026 season so far (NFL weeks 1-7, CFB weeks 0-8: every
betting week before live collection began on 2026-10-27), grade it against real final scores, and find
where the book makes and loses money. Rules for official plays are written from the DISCOVERY weeks and
checked on sealed TEST weeks before they count (see "Analysis" below).

## Budget
* Separate one-time allowance: at most `BACKFILL_HARD_MAX` = **12,000 credits**, usable only until
  **2026-12-01 00:00 UTC** (`collect_v4/backfill.py`). The owner chooses the usable amount with repository
  variable `V4_BACKFILL_BUDGET` (clamped to 12,000; unset or 0 = nothing is spent).
* Live collection is unchanged: its monthly cap (variable `V4_MONTHLY_CAP`, code ceiling 6,000) and 24 h cap
  apply to live spending only; backfill credits are added back (`actions_run.exclude_backfill`), so the
  backfill can never stop live collection.
* Every paid request still passes the provider-balance preflight (fails closed), the V3 floor (2,000), the
  per-request ceiling (30 = one historical snapshot: 10 x 3 markets x 1 region) and the breaker; every
  request is written to the ledger.
* Estimate (2026-11-10, from the real CFB schedule + AP polls and the standard NFL kickoff pattern):
  CFB ~235 snapshots (~7,050 credits), NFL ~152 snapshots (~4,560), **total ~11,600**. Pilot (NFL week 1)
  ~20 snapshots (~600). Account balance 2026-11-10: 19,064; expected left on 2026-11-30 after the backfill
  and normal live collection: ~5,500.

## Snapshot plan (per sport and Tuesday-to-Tuesday week, like the live book)
Wednesday 16:00 UTC anchor (lists the week's games) -> 16:00 UTC daily -> for each kickoff cluster:
kickoff - 3 h, - 1 h (where a final pick is made) and - 10 min (the observed close). CFB: AP Top-25
games only, by the poll that applied that week. Pick and close snapshots are always kept.
Snapshots are stored at the provider's snapshot time in release **odds-hist** (never in odds-data-*: the
live site and live book never read them). Progress: `backfill_state.json` in release v4-state.

## Owner steps
1. GitHub -> repository -> Settings -> Secrets and variables -> Actions -> Variables -> New repository
   variable: name `V4_BACKFILL_BUDGET`, value `12000`.
2. Actions -> odds-collect -> Run workflow -> branch `v4-dev`, mode **backfill-pilot** -> Run workflow.
   (about 1-2 minutes, about 600 credits). Claude checks the cost and data.
3. Then Run workflow again with mode **backfill-all**. A run stops itself after 35 minutes; if the summary
   says "press Run again", run it again (nothing is repeated).
4. When finished, delete the variable or set it to 0 (the allowance also expires on 2026-12-01).
Claude never starts these runs.

## Analysis
Replay and grading run offline (no Actions minutes): `tools_v4/backfill_replay.py` (to come).
Discovery weeks: NFL weeks 1-4, CFB weeks 0-4. Test weeks (sealed until the rules are committed):
NFL weeks 5-7, CFB weeks 5-8, plus the live weeks.
