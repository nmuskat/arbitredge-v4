# ArbitrEdge V4 — Blueprint and Milestone 1 Report

**Status:** development only. Nothing was deployed, and no production file, flag, database, or service was touched.

**Workspace:** `/home/user/arbitredge_v4_workspace`, a git repository.
- Commit 1 is the unmodified archive you supplied.
- Milestone 1 is purely additive: four new top-level directories (`sportsbook_v4/`, `tests_v4/`, `tools_v4/`, `docs_v4/`). Zero existing files are modified (verified with `git status`).
- An auditable patch is in `docs_v4/milestone1.patch`.

---

## Part 1 — Blueprint

### 1. Existing components: preserve, refactor, retire

| Component | Decision | Notes |
|---|---|---|
| `simulation/accounting.py` (American odds math) | **Preserve.** V4 has an equivalent pure function in `sportsbook_v4/market.py` so the core has no dependency on V3 packages | Both reconcile to the cent |
| `simulation/offered_line.py` lower-median method | **Preserve the method**, re-implemented time-aware in `sportsbook_v4/market.py:representative_offer` | Same tie convention; never fabricates a price |
| `features/market_state_as_of.py` effective-time rule | **Preserve.** `SnapshotMarket` applies the identical `book_last_update`-else-`observed_at` rule | Tested |
| V3 identity/profile tables and anti-circularity allowlist | **Preserve the concept**; V4 has its own population tables and its own allowlist (`sportsbook_v4/facts.py`) | V3 rows stay untouched as a rollback baseline |
| `simulation/bet_selection.py`, `bettor_profile.py`, `population.py` | **Retire after V4 promotion.** Leave untouched until then (rollback baseline) | Defects A, B, C |
| `simulation/ledger.py` | **Retire after V4 promotion** | Settles by side only; replaced by per-wager settlement |
| `book_need/run_slate_book_need.py:_promote_v3_live` | **Retire.** Replace with the V4 read API plus a separate decision layer (milestone 4) | Defect D |
| `book_need/synthetic_sportsbook.py`, `synthetic_bettors.py`, `simulate_shared_nfl_population` | **Retire** | Circular by construction |
| `book_need/inference.py` market-response pipeline | **Preserve** as an *independent evidence input* to Market Confirmation, never as a gate on the Need | |
| `operations/run_collection.py:update_experimental_mixed_signals` | **Correct later** (append-only). Not touched now: it is part of the official play process you told me not to change | Defect E |
| `book_need/prospective_record.py` | Superseded by `v4_prospective_decision`; leave as is | |
| Web dashboard | **Untouched.** Later it will read V4 through `ledger_read.py` only (read-only connection) | |

### 2. Module and database architecture

```
sportsbook_v4/
  __init__.py      ENGINE_VERSION
  util.py          sha256 seeds, canonical JSON hashing, UTC epoch <-> ISO (no wall-clock inputs)
  params.py        PARAMS_VERSION, archetypes, book policy, timing profiles -- all UNVALIDATED, hashed
  schema.py        V4 DDL, append-only triggers, refuses to run inside a market/production DB
  market.py        Quote, MarketSource, FixtureMarket, SnapshotMarket (mode=ro), representative_offer
  facts.py         allowlisted point-in-time game facts; forbidden model fields raise
  population.py    deterministic persistent bettors; population_hash integrity check
  engine.py        run identity, arrival clock, per-arrival decisions, transactional advance()
  settlement.py    per-wager settlement, exact P&L curve over final margins, exposure summary
  ledger_read.py   READ-ONLY book views (mode=ro) -- the only thing a dashboard would call
  decision.py      FinancialNeed / MarketConfirmation / OfficialPlay (three separate outputs)
  prospective.py   immutable pre-kickoff decision records
tests_v4/          40 tests + realistic multi-day fixtures
tools_v4/run_diagnostics.py
```

**Database: V4 uses its own SQLite file** (for example `/opt/arbitredge/db/arbitredge_v4.db`, created only after you approve). It reads market data from the production DB through a **read-only** connection. V4 tables:

| Table | Purpose | Key constraints |
|---|---|---|
| `v4_population` | One row per (sport, params version, size, team universe) | PK `population_id`; stores `params_hash`, `population_hash` |
| `v4_bettor` | Persistent bettors: archetype, starting bankroll, traits | PK (population_id, bettor_index); `bettor_uid` UNIQUE |
| `v4_run` | One logical book: population + slate (games, betting window) + engine/params version + market source | PK `run_id` = sha256 of all of those |
| `v4_interval` | One row per committed clock advance (the watermark) | PK (run_id, seq); UNIQUE (run_id, interval_start) |
| `v4_wager` | Accepted wagers | PK `wager_id` = sha256(run, bettor, arrival); UNIQUE (run_id, bettor_uid, arrival_seq); CHECKs on side, price legality, stake > 0, and line/side consistency |
| `v4_prospective_decision` | Pre-kickoff decisions | UNIQUE (game, policy_version, decision_point); CHECK decided_at < kickoff |

**All six tables have BEFORE UPDATE and BEFORE DELETE triggers that abort.** Indices: `(run_id, canonical_game_id, accepted_at)` and `(run_id, bettor_index)`.

**Production migration implications:** none in milestone 1. The V4 DDL only creates new `v4_*` tables in a separate file. `ensure_schema` refuses to run in any DB containing `market_snapshots`. This deliberately avoids an `ALTER` of the 8.6 GB production database. The V3 `synthetic_wager` table is not reused: its schema has no acceptance-time market reference, ties identity to V3 runs, and is V3's rollback baseline.

### 3. Event-sourced wager lifecycle

```
create_run(population, slate, market_source)          -> run_id (deterministic)
advance(run_id, until, market, facts, data_complete_through)
   BEGIN IMMEDIATE
     watermark := last committed interval_end (or window_start)
     until <= watermark                                -> no-op (idempotent)
     until  > data_complete_through                    -> refuse (ClockError)
     engine/params/market source != run's              -> refuse (RunMismatch)
     arrivals in (watermark, until]  -- from each bettor's precomputed, run-fixed arrival list
     for each arrival in time order:
        offered number := representative market as of (t - offer_lag), floored to the minute
        reference      := reference-book market as of t
        decide(game, side, price check, shopping, stake, budget, limit) or PASS(reason)
     INSERT wagers + INSERT interval row
   COMMIT        (any exception -> ROLLBACK: no wagers, watermark unchanged)
```

- **A wager is final at INSERT.** No code path updates, deletes, or recomputes it (triggers enforce this).
- **Line moves** only affect wagers accepted after the move. Old wagers keep their number and price, and the ledger then holds several accepted numbers per game.
- **Dashboard refresh** calls only `ledger_read.py` over a `mode=ro` connection and cannot write.
- **Model upgrades** produce a new `params_hash`, so the old run refuses to advance and a new run (new `run_id`) must be created. Old wagers are never rewritten.

### 4. Bettor behaviour and stake model (`params.py`, `v4_params_m1_unvalidated`)

Seven archetypes. **Every number is an UNVALIDATED assumption.**

| Archetype | Share | Starting bankroll (median) | Stake model | Arrivals per week | Side behaviour | Timing |
|---|---|---|---|---|---|---|
| REC_CASUAL | 51% | $400 | bankroll × ~5% × lognormal | ~2.0 | favourite lean (+0.5 logit), popularity, recency | weekend/late |
| TEAM_LOYAL | 12% | $400 | ~5% | ~1.5 | +2.0 logit to own team; 8× game-selection weight for own team | weekend/late |
| UNDERDOG_CONTRARIAN | 8% | $600 | ~4% | ~1.5 | underdog lean (−0.6), anti-popularity | steady |
| NARRATIVE_CHASER | 12% | $400 | ~6% | ~2.5 | strong recency (1.5), primetime 1.5 | weekend/late |
| HIGH_STAKES_REC | 4% | $40,000 | ~3% | ~1.5 (not more frequent) | favourite lean (+0.4), primetime | weekend/late |
| SELECTIVE_PRICE_SENSITIVE | 10% | $3,000 | ~2% | ~1.2 | passes unless P(side) ≥ 0.58; lays at most ~−113; **shops**: passes if any book is ≥ 0.5 pt or ≥ 1 implied-prob point better | steady |
| SHARP | 3% | $60,000 | ~2% × edge multiplier, book limit $3,000 | ~8 board checks | **bets only when the book's number is worse than the reference market at that moment** (≥ 0.5 pt, or ≥ 1.5 implied-prob points at the same line); otherwise passes | early week |

**Structural rules:**
- **Side choice is a logit of antisymmetric terms:**
  - favourite preference × favourite sign
  - popularity log-ratio
  - recency difference
  - loyalty
  - plus `home_pref`, whose **population mean is 0.0 (validated at import)**

  A home/away swap exactly mirrors P(home); `test_side_probability_is_exactly_antisymmetric…` verifies this for 2,000 bettors.
- **Stake** = bankroll × unit fraction × lognormal noise, capped by the book limit and the bettor's remaining weekly budget (bankroll × weekly risk fraction minus stakes already accepted this run). High stakes is a property of the bettor's bankroll and unit, not of a tier-threshold coefficient. That removes the class of bug behind Defect B.
- **No archetype is assumed profitable.** SHARP describes a behaviour (reacting to off-market numbers using only information at time *t*), not skill. Profitability and CLV are to be measured from settled results (milestone 3).
- **Team popularity** is implemented as a hook (`TEAM_POPULARITY_PRIOR`) but is **neutral (all 1.0) in the active params.** I did not invent team-popularity numbers. Supplying a sourced table is a decision for you (see §10).
- **Bankroll evolution, loss chasing, and overconfidence** need settled results feeding back into later weeks: milestone 3, event-sourced, never rewriting `v4_bettor`.

### 5. Deterministic clock and replay

- **Arrival times:** each bettor's arrivals over the whole window are a non-homogeneous Poisson process (thinning against the archetype's Central-time weekday/hour profile). Seed = sha256(run_id, bettor_uid). They do not depend on how the window is cut into intervals.
- **Each decision** uses seed sha256(run_id, bettor_uid, arrival_seq) and the market as of its own time.
- **Proven by tests:**
  - advancing `start→end` in one step equals four steps, byte for byte
  - two fresh databases produce identical wagers
  - an interrupted transaction leaves nothing behind, and the retry equals a clean run
  - injecting a future quote after time T changes nothing accepted at or before T
- **`data_complete_through`:** the clock may never pass the last time for which market data is complete. In production that is the latest successful odds poll. This stops V4 from freezing decisions made on not-yet-collected data.

### 6. Financial accounting and settlement (`settlement.py`)

- Each wager settles at **its own** accepted number and price. Result = sign(margin_for_side + accepted_line): win, push, or loss.
- Every spread wager on a game pushes at final home margin `−accepted_home_spread`. Book P&L as a function of the final margin is therefore a step function whose only breakpoints are the distinct accepted spreads. Evaluating every integer margin across them is **exact**, with **no margin distribution assumed**.
- **Per game it reports:**
  - tickets, handle, ticket and handle % by side
  - P&L if home covers every accepted number, and if away covers every accepted number (the "clean" outcomes)
  - swing
  - hold % per outcome
  - worst/best case and the margins where they occur
  - push margins
  - **middle risk margins** (worse for the book than either clean outcome) and **middle benefit margins**
  - narrow-cover scenarios either side of a reference line
  - accepted-number and price distribution
  - per-archetype tickets, handle, stake stats, and P&L
  - concentration (top-1% ticket handle share, top-10 wagers, bettor handle HHI)
  - exposure over time (`exposure_timeline`)
- **ARBITREDGE FINANCIAL NEED** = the clean outcome with the higher book P&L. With one accepted line it reduces exactly to V3's two-outcome definition (tested). Middles are reported alongside it, not hidden inside it.
- **MARKET CONFIRMATION** (`decision.py`): consensus movement from open to `as_of` toward or against the Need side (≥ 0.5 pt, or ≥ 2 implied-prob points at an unchanged line), plus an `external_evidence` dict where the legacy regime can be attached. It never alters the Need.
- **OFFICIAL PLAY** (`decision.py`): versioned policy. The **active policy always returns PASS** (`POLICY_NOT_VALIDATED`) and records whether a candidate rule *would* have played, so that rule can be graded prospectively before anyone considers activating it.

### 7. Migrations and isolation safeguards

1. V4 never shares a database file with production writers.
   - `ensure_schema` refuses a DB containing `market_snapshots`.
   - `SnapshotMarket` opens the market DB with `mode=ro`.
2. The V4 core imports nothing from `book_need`, `behavioral`, `web`, `operations`, `collectors`, or `simulation`. An AST test enforces this.
3. Nothing in production imports V4. Milestone 1 adds no hooks, flags, cron entries, or web changes.
4. The future production migration is `CREATE` statements only, on a new file. It needs no change to `arbitredge.db`. Rollback = stop calling V4 and delete the new file; V3 is untouched.
5. **Disk, measured on the fixture week:** a 10,000-bettor population is ~4 MB. The wager table plus indices takes ~1.16 KB per wager, so ~18k wagers is **~21 MB per sport-week** (`VACUUM`ed). To be re-measured on real slates.

### 8. Test plan and acceptance criteria

All 16 required properties have automated tests. Results are in Part 2.

| # | Requirement | Test(s) |
|---|---|---|
| 1 | No unintended home/away preference | `test_side_probability_is_exactly_antisymmetric…`, `test_no_systematic_home_lean_on_symmetric_slate` (±1.5 pt over ~15k tickets; mirror pairs within 6 pt), `test_population_home_pref_centred`, `test_active_params_assume_no_home_preference` |
| 2 | Stake ordering | `test_high_stakes_bettors_have_larger_stakes_not_more_bets` (HS median unit ≥ 10× every recreational archetype; HS frequency ≤ REC_CASUAL), `test_accepted_stake_ordering_in_run` |
| 3 | Ticket ≠ handle when justified | `test_ticket_and_handle_shares_can_diverge`, `test_sharps_only_bet_when_book_number_is_off_market` |
| 4 | Immutable accepted numbers | `test_append_only_triggers`, `test_line_moves_create_multiple_accepted_numbers_and_wagers_keep_theirs`, `test_advancing_does_not_change_earlier_wagers` |
| 5 | Idempotent reruns | `test_idempotent_reruns` |
| 6 | Time-forward accumulation | `test_time_forward_accumulation`, `test_book_as_of_only_counts_wagers_accepted_by_then` |
| 7 | Settlement at multiple numbers | `test_multiple_accepted_lines_settle_individually` |
| 8 | Pushes, middles, opposing liabilities | `test_wager_result_signs_and_pushes`, `test_middle_and_opposing_liabilities`, `test_book_middle_benefit_and_whole_number_push`, `test_reference_narrow_scenarios_and_push` |
| 9 | Deterministic replay | `test_deterministic_replay_and_split_invariance` |
| 10 | Rollback / interruption recovery | `test_rollback_on_interruption_then_clean_recovery` |
| 11 | Sport/population isolation | `test_sport_and_population_isolation`, `test_population_is_idempotent_and_verified`, `test_v4_package_imports_nothing…`, `test_refuses_to_write_into_a_market_database` |
| 12 | No future leakage | `test_future_market_data_cannot_change_past_wagers`, `test_market_lookup_never_returns_future_quotes`, `test_forbidden_model_fields_rejected`, `test_snapshot_adapter_is_read_only_and_point_in_time`, `test_clock_cannot_outrun_market_data` |
| 13 | P&L reconciliation | `test_full_week_pnl_reconciles_exactly` (exact rational arithmetic over every margin, every game, plus archetype sums) |
| 14 | Need / confirmation / play separation | `test_financial_need_survives_contradicting_market`, `test_official_play_can_pass_while_need_exists`, `test_market_confirmation_direction` |
| 15 | Prospective immutability | `test_prospective_records_are_immutable` |
| 16 | Dashboard reads have no side effects | `test_dashboard_reads_have_no_side_effects` (file hash unchanged after repeated reads; ro connection rejects writes) |
| — | Version safety | `test_model_upgrade_cannot_continue_old_run`, `test_market_source_must_match_run` |

**Fixtures** (`tests_v4/fixtures.py`) are synthetic. They are a full NFL week from Sunday-night open to MNF:
- a TNF move through 3
- a favourite flip
- a juice-only move at 3
- a pick'em
- reference books leading retail books by 3 hours

### 9. Staged implementation order

| Milestone | Scope | Acceptance | Production impact |
|---|---|---|---|
| **M1 (done, below)** | Engine, ledger, settlement, decision separation, prospective table, diagnostics, tests | 16 properties tested; mutation-checked | None |
| **M2** | Grader: ATS at recorded number and price, ROI, CLV vs observed close, calibration by tier, Need-accuracy reporting; reconstruction audit of historical picks from read-only data | Grades reproducible from DB alone; no manual inputs; look-ahead test | None (reads a DB copy) |
| **M3** | Settlement events → bankroll evolution across weeks; loss chasing and overconfidence as UNVALIDATED traits; weekly population budget across days | Bankroll ledger reconciles; replay across weeks deterministic | None |
| **M4** | Real-data shadow replay: `SnapshotMarket` against a **copy** of production market data for completed 2026 weeks; real slates (NFL, CFB Top-25 eligibility reproduced point-in-time) | Runs end to end on real snapshots; diagnostics on real slates | Needs the data copy (§10) |
| **M5** | Shadow operation on the server: separate DB file, separate scheduled job, read-only on market data; nothing displayed | 2+ weeks of clean shadow runs | **Needs your approval** (new file + cron entry) |
| **M6** | Dashboard read path (V4 panel alongside V3), still no official-play change | Read-only test in production context | **Needs your approval** |
| **M7** | Official-play policy decision, only after prospective grading | Pre-registered sample size reached | **Needs your approval** |

### 10. Risks, unknowns, and information I need

**Modelling risks (from the diagnostics — see Part 2):**
1. > **CORRECTION (Milestone 2):** the explanation below is wrong about the cause. The sensitivity study (`docs_v4/MILESTONE_2_REPORT.md` §4) shows that sharp reaction to the fixture's line moves supplies ~72% of the favourite pressure; recreational and high-stakes favourite preference supplies the rest. High-stakes concentration is not the main driver. The 69/72 figure is from the pre-M2 seeding; it is 65/72 after the M2 seeding change.

   **On the realistic fixture, the Need is almost always the underdog (69 of 72 non-pick'em game-seeds).** This is the V4 counterpart of V3's "always away", except it now follows from explicit, inspectable assumptions:
   - favourite-leaning recreational and high-stakes bettors
   - high-stakes bettors (4% of bettors) carrying **~48% of handle**
   
   Setting recreational favourite preference to 0 leaves it at 22 of 24. The handle concentration is what drives it.
   - Whether real books look like this is **unknown**. Until it is calibrated, the Need is largely an expression of these priors.
   - It must not be read as market insight, and the official-play policy stays PASS.
2. The fixture's line paths themselves mostly move toward favourites, which pushes sharp money onto favourites. Real-snapshot replay (M4) is required before any frequency claim.
3. The offer-lag assumption matters. The home-Need share moves from 21% to 43% between 10 min and 1 h of lag, because sharps exploit the lag. The lag must be set from observed polling and update cadence, not guessed.
4. Team popularity is neutral. A sourced prior (if you have one) would change the Need on marquee teams.

**Information I need** (read-only commands only):
1. `sqlite3 -readonly db/arbitredge.db ".schema market_snapshots"` and `".schema teams_canonical"`, plus `SELECT name, sql FROM sqlite_master WHERE type='trigger';`
2. `SELECT sport, COUNT(*), COUNT(DISTINCT canonical_game_id), COUNT(DISTINCT book), MIN(observed_at), MAX(observed_at) FROM market_snapshots GROUP BY sport;`. This tells me the time resolution available for offers.
3. **Defect E:**
   - `".schema experimental_mixed_signals"`
   - `SELECT sport, signal_version, COUNT(*), MIN(written_at), MAX(written_at) FROM experimental_mixed_signals GROUP BY 1,2;`
   - `SELECT COUNT(*) FROM experimental_mixed_signals s JOIN games_canonical g USING(canonical_game_id) WHERE s.written_at >= g.commence_time;`
   - whatever spreadsheet or log produced 37-34-5

   Until I see these, I treat historical picks as **not reconstructable**.
4. `SELECT canonical_team_id FROM teams_canonical WHERE sport IN ('nfl','cfb');`. This is the team universe for loyalty draws.
5. The server's Python version (V4 needs 3.10+ for `X | None` annotations; developed on 3.13).
6. **For M4:** permission to make a **copy** of the market tables (or the whole DB) on the dev side. I would use `sqlite3 -readonly db/arbitredge.db ".backup /tmp/market_copy.db"` or an export of `market_snapshots`, `games_canonical`, `teams_canonical`, `game_context`, and `game_results` for 2025–2026.
7. **Decision:** do you have a sourced team-popularity or public-handle reference you want encoded as a labelled prior, or should it stay neutral?

---

## Part 2 — Milestone 1 report

### What changed

Purely additive; no existing file is modified (`git diff 0cf5495 --stat` shows only new files).

- `sportsbook_v4/`: 12 modules (Part 1, §2)
- `tests_v4/`: `conftest.py`, `fixtures.py`, `test_settlement.py`, `test_engine.py`, `test_behavior.py`, `test_safety.py`
- `tools_v4/run_diagnostics.py`
- `docs_v4/`: this document, `diagnostics_m1.json`, `milestone1.patch`

### Tests actually run

Environment: Python 3.13, pytest in an isolated venv, temp/in-memory SQLite.

| Suite | Result |
|---|---|
| `tests_v4` (40 tests) | **40 passed** (~22 s) |
| Mutation check: injected home bias (+0.2 logit) | caught by both symmetry tests |
| Mutation check: HS stake collapsed to the V3-like small-stake profile | caught by the stake-ordering test |
| Mutation check: book offers a price from 24 h in the future | caught by the leakage and accepted-number tests |
| Mutation check: push settled as win | caught by 3 settlement tests |
| Mutation check: commit instead of rollback on failure | caught by the rollback test |
| Mutation check: Need nulled when market contradicts | caught by the separation test |
| Baseline V3 runnable tests (unchanged files) | 120 passed, 4 failed. Identical to the Phase 1 baseline; the 4 failures are the missing `collectors/config.py` |

Not run: anything against real market data or the production DB (not available, and not authorised).

### Diagnostics

`tools_v4/run_diagnostics.py`, 10,000 bettors, 12 seeds; full output in `docs_v4/diagnostics_m1.json`. **Synthetic fixtures only; these show model behaviour, not market behaviour.**

- **One week, seed 0:** 23,483 arrivals and 17,888 accepted wagers. Passes:

  | Reason | Count |
  |---|---|
  | `no_edge` (sharps) | 2,362 |
  | `budget_or_minimum` | 2,057 |
  | `low_conviction` | 956 |
  | `price_too_expensive` | 165 |
  | `no_open_market` | 46 |
  | `shopped_elsewhere` | 9 |

- **Archetype mix** (ticket share / handle share, median stake):

  | Archetype | Ticket share | Handle share | Median stake |
  |---|---|---|---|
  | REC_CASUAL | 57.4% | 14.2% | $21 |
  | NARRATIVE_CHASER | 17.2% | 4.9% | $24 |
  | TEAM_LOYAL | 10.5% | 2.5% | $21 |
  | UNDERDOG_CONTRARIAN | 7.2% | 2.3% | $26 |
  | HIGH_STAKES_REC | 4.0% | **48.1%** | **$1,079** |
  | SHARP | 1.9% | 27.0% | $2,046 |
  | SELECTIVE_PRICE_SENSITIVE | 1.8% | 1.0% | $58 |

- **Ticket vs handle diverge materially**, for example:
  - g_sun2: home tickets 47.6% vs home handle 79.2%
  - g_tnf: 69.3% vs 55.3%
- **Games carry 2–6 distinct accepted numbers each.**
- **Need frequencies:**
  - Symmetric fixture: home 47 / away 49 (no home/away bias).
  - Realistic fixture: home 21 / away 63, and underdog 69 of 72 non-pick'em game-seeds (risk #1 above).
- **Sensitivity** (home-Need share and favourite-Need count, 28 game-seeds each):

  | Variant | Home-Need share | Favourite Needs |
  |---|---|---|
  | Baseline | 21% | 1/24 |
  | Offer lag 0 s | 32% | 1/24 |
  | Offer lag 1 h | 43% | 4/24 |
  | Rec favourite preference 0 | 25% | 2/24 |
  | Rec favourite preference 1 | 21% | 0/24 |
  | HS weekly risk halved | 32% | 3/24 |

  The Need direction is robust to recreational favourite preference and sensitive to high-stakes handle and book lag.

### Assumptions and remaining defects (honest list)

1. All behavioural parameters are UNVALIDATED (see risk #1).
2. **There is no bankroll feedback across weeks yet.** The weekly budget comes from the *starting* bankroll (M3).
3. **The arrival window is per run (slate).** A bettor's NFL and CFB activity are independent populations by design. Within a sport, the run window should be the betting week (Sunday-night open to MNF), not a calendar day.
4. **Game facts:** V4 consumes them through `FactsSource`, but no production facts adapter exists yet. V3 Defect C‑H1 (`engineered_features_v2` often empty for upcoming games) must be resolved before recency terms carry real information; until then use `NoFacts`.
5. **Slate definition is fixed at run creation.** Adding a game mid-week means a new run. CFB Top-25 eligibility must therefore be decided point-in-time at window open (M4).
6. Totals, parlays, and teasers are not implemented (totals deliberately stay off).
7. **`exposure()` treats each game independently.** Cross-game or parlay liability needs a joint ledger later.
8. **Performance:** a full NFL week with 10,000 bettors advances in ~4 s; the whole test suite takes ~22 s. Not yet measured on real snapshot volumes.

### How this integrates later (not done; each step needs approval)

- **M4:** V4 is pointed at a read-only copy of production market data via `SnapshotMarket`.
- **M5:** a separate scheduled job runs `advance(run, until=min(now, last successful odds poll))` on a separate `arbitredge_v4.db`. It cannot affect V3, the website, or official plays.
- **M6:** `web/app.py` gains a read-only V4 panel built from `ledger_read.game_book` plus `decision.assess_game`. V3 stays live until you decide otherwise.
- **Rollback at every stage:** disable the job or panel; V3 and its database were never modified.

**Decision points for you:**
1. the read-only information in §10
2. whether to proceed to M2 (grader) and M4 (copy of market data)
3. the team-popularity prior question
