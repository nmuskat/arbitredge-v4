# Development plan: result settlement and persistent bankrolls (MODELED sportsbook)

**Goal.** Every synthetic bettor has:
- a complete wager history;
- each wager settled as win, loss, push or void from **real final scores**;
- a bankroll that evolves week to week from those settlements.

**Rules that hold throughout:**
- Accepted wagers are never altered. Settlement is a separate, append-only record that refers to a wager by ID.
- No result is ever invented. A missing, unmatched or disputed score leaves the wager **OPEN**, never guessed.
- Everything stays labelled MODELED. Settled simulated profit and loss is a property of the simulated customers, not a betting claim. Nothing here creates a recommendation or an official play.

## 1. Results ingestion (no Odds API credits)

| Sport | Source | Cost | Notes |
|---|---|---|---|
| NFL | nflverse `games.csv` (already used for history) | free, no key | final scores usually published within hours |
| CFB | CollegeFootballData `/games?year=&week=&seasonType=` | free tier, existing key | about 2 calls a week. Rule 2b allows the key only in `cfb-rankings.yml`, so this extends **that** workflow (needs the owner's OK) |

**How results are stored and matched:**
- **Storage:** each fetch becomes an immutable file in a new release `v4-results`, with source, URL, fetch time and SHA-256.
- **Matching:** each Odds API event is mapped to its source game by (sport, normalised home and away teams via `data_v4.rankings.TeamMatcher` and an NFL code table, kickoff date ± 1 day). An ambiguous or unmatched game stays UNMATCHED and is reported.
- **Finality:** a result is used only once the source marks it final, the score is non-negative, and two consecutive fetches agree. A later different score is recorded as a **correction event**, never an overwrite.

## 2. Settlement ledger (append-only, separate from wagers)

**New table `v4_settlement`** (rows only appended):
- `settlement_id`, `wager_id`, `run_id`, `event`, `result`, `payout`;
- `home_score`, `away_score`, `result_ref` (the source file and row hash);
- `rules_version`, `settled_at`, `supersedes`.

**Grading uses each wager's own `accepted_line` and `accepted_price`** (the existing `settlement.wager_outcome`):

| Market | Rule |
|---|---|
| Spread | `margin + line` > 0 win, = 0 push, < 0 loss |
| Total | `total − line` for overs (mirrored for unders) |
| Moneyline | a tie is a push |

Overtime counts; the game final is used.

**Exceptions:**
- **Void:** the game is cancelled or postponed more than 48 h past its kickoff. The stake is returned, and the reason is recorded.
- **Corrections:** a new row with `supersedes = <old settlement_id>`. The newest non-superseded row per wager is authoritative, and history is never lost.

**Storage and checks:**
- Exported like `v4-book`: one immutable `settle_<sport>_<week>_<seq>.json.gz` per settlement pass.
- Every build checks that every settled wager exists, that each settlement re-grades identically from its stored score, and that no wager is settled twice without `supersedes`.

## 3. Bankroll ledger

**Opening bankroll.** The population's generated bankroll (already persistent per bettor).

**Event stream per bettor**, derived and never stored as a mutable balance:
- `stake_committed` at acceptance (from `v4_wager`);
- `payout` at settlement (from `v4_settlement`);
- `void_refund`;
- optional `reload`, an explicit and parameterised assumption, for example 30% of recreational bettors who go broke re-deposit their opening bankroll after a 2-week gap. It's labelled and versioned.

**Balances:**
- *Available* bankroll = opening + Σ settled P&L + reloads − open (unsettled) stakes.
- *Equity* also counts open stakes at cost.

**Point-in-time and replay-safe.** Weekly run W freezes a **bankroll snapshot** when the week opens (Tuesday 12:00 UTC):
- each bettor's available bankroll from every settlement *recorded* by then;
- stored with the run, and its hash becomes part of the run identity.
- A replay of week W therefore reproduces exactly; a late settlement only affects the *next* week's snapshot.
- Wagers from week W−1 still unsettled at open stay committed, so their stakes stay unavailable.

## 4. Engine changes (engine m5, new runs only; m4 runs stay as they are)

- **Stakes and budgets** come from the week's snapshot instead of the static opening bankroll: unit size × available bankroll, weekly budget a fraction of available bankroll.
- **Busted bettors.** A bettor whose bankroll is below the minimum stake doesn't bet until they reload.
- **Result-dependent behaviour** (all unvalidated, each behind its own parameter, defaulting to **off** until calibrated):
  - loss-chasing: a stake multiplier after a losing week;
  - winners raising unit size;
  - recency of the bettor's *own* results.
- **Sharps' bankroll** evolves the same way, so a sharp's simulated edge shows in their bankroll. It's still MODELED and doesn't validate anything.

## 5. Dashboard (all MODELED)

- **Per-bettor wager history** in a game or bettor detail view: bettor ID, archetype, wagers, settled results, bankroll path.
- **Per week:** realised book P&L and hold by market; distribution of bettor results (share up or down, by archetype); open versus settled counts; unmatched results listed as UNAVAILABLE.
- **Need versus outcome**, shown as a liability fact (*"the book needed X; the result was Y"*), with the reminder that Need is not betting value.

## 6. Validation and tests

**Settlement correctness:**
- table tests for every market: half and whole lines, pushes, ties, overtime;
- moneyline ties, voids and corrections.
**Immutability:**
- UPDATE and DELETE triggers on `v4_settlement`;
- a test that settlement never touches `v4_wager`;
- tamper tests on settlement files (hash, gap, duplicate).
**Determinism:**
- replaying week W with its stored bankroll snapshot gives the identical ledger;
- a late result changes only W+1.
**Reconciliation:**
- Σ bettor P&L = −(book P&L) per game and week, to the cent;
- bankroll = opening + Σ events for every bettor.
**Results ingestion:**
- duplicate fetches are idempotent;
- disagreeing scores are held as OPEN;
- an unmatched game stays OPEN and is reported.
**Sanity checks** (expected outcomes, not targets):
- realised hold over many weeks is near the theoretical hold of the book's offered markets;
- Need side ATS ≈ 50% (Need is not an edge).
Any systematic deviation is investigated, never tuned away silently.

## 7. Phases

| Phase | Deliverable | Depends on |
|---|---|---|
| 1 | NFL results ingestion (nflverse) + matching + `v4-results` | none |
| 2 | Settlement ledger + grading + immutability tests + dashboard settled view | 1 |
| 3 | CFB results via CFBD inside `cfb-rankings.yml` | **owner OK to extend that workflow's key use** |
| 4 | Bankroll event stream + weekly snapshot + reconciliation | 2 |
| 5 | Engine m5: bankroll-driven stakes, busts and reloads (result-dependent traits off) | 4 |
| 6 | Calibration study: which result-dependent behaviours, if any, are justified | 5 + several weeks of data |

**Not in scope:**
- profitability claims, official plays and recommendations;
- any Odds API usage (scores come from free sources).
