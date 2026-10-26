# ArbitrEdge V4 — Milestone 2 Report

This milestone covers three pieces of work:

- **Historical grader** (Milestone 2).
- **Replay framework, prepared but not run** (Milestone 4 preparation).
- **Investigation of the underdog-Need result.**

**Status: development only.** Nothing touched production. That means no database access, no website or flag changes, no restarts, no deployment, and no Odds API calls. Team popularity stays neutral, and official plays remain PASS (the policy is unchanged).

---

## 1. Summary

| Item | Result |
|---|---|
| **Grader** (`sportsbook_v4/grading.py`) | Built. Grades ATS at each pick's own line, ROI at recorded odds, CLV against the observed close, W‑L‑P with Wilson CI and exact p‑value, calibration (only if forecasts exist), and grouped reports. It **refuses to invent** missing lines, prices, or timestamps. |
| **Historical pick reconstruction** | **Not yet possible to verify**: no pick data has been provided. From the code, `experimental_mixed_signals` stores **no recorded price** and is **mutable** (upsert/delete). Any ROI from it can only be "derived from quotes recorded with the pick" and is reported separately, never as "recorded odds". **37‑34‑5 remains unverified.** |
| **Replay framework** (`sportsbook_v4/replay.py`) | Built and tested on a **synthetic, production-shaped** copy. It refuses the production path, opens copies read-only, validates the schema, builds point-in-time week slates (including CFB Top-25 and redirects), replays through the engine, and evaluates each Need against the real result and close. **Not run on real data.** |
| **Underdog-Need investigation** | **Explained** (§4). On the fixture, ~72% of the favourite pressure comes from sharp reaction to the fixture's line moves; it **reverses when the moves are reversed** and **vanishes when lines are frozen**. The remainder comes from recreational and high-stakes favourite preference, an **assumption** that pushes toward the underdog in every game regardless of the market. With all behavioural preferences off and lines frozen, the result is **11 of 24 underdog (a coin flip)**: no hidden engine bias. |
| **Correction** | My Milestone 1 report said high-stakes handle concentration drove the result. **That was wrong.** Corrected in the M1 document. |
| **Tests** | **64 passed, 0 failed** (`tests_v4`). 4 new mutation checks were all caught (10 in total across M1 and M2). |

---

## 2. Files changed in this milestone

Commit `M2` on top of `bc8f936`. All V4-side; **no production or baseline V3 file was modified** (verified with `git diff --name-status 0cf5495 HEAD`: every entry is under `sportsbook_v4/`, `tests_v4/`, `tools_v4/`, `docs_v4/`, or `.gitignore`).

| File | Change |
|---|---|
| `sportsbook_v4/grading.py` | **new**: grader, importers (experimental_mixed_signals, CSV ledger, V4 decisions), reconstruction audit, Need-outcome evaluation |
| `sportsbook_v4/replay.py` | **new**: production guard, read-only copy access, schema validation, week slates, replay |
| `sportsbook_v4/params.py` | `variant()` context manager for diagnostics (hashed, validated, always restored, non-nesting) |
| `sportsbook_v4/engine.py` | `rng_key` (common random numbers): randomness identity excludes params and market source |
| `sportsbook_v4/schema.py` | `v4_run.rng_key` column; SCHEMA_VERSION 2 (nothing deployed, so no migration) |
| `tests_v4/fixtures.py` | `static_week()`, `reversed_week()`, configurable `source_id` |
| `tests_v4/test_grading.py` | **new**: 12 tests |
| `tests_v4/test_replay.py` | **new**: 7 tests (synthetic production-shaped copy) |
| `tests_v4/test_variants.py` | **new**: 5 tests |
| `tools_v4/sensitivity_study.py` | **new**: 35-variant sweep with exact archetype attribution |
| `tools_v4/analyze_sensitivity.py` | **new**: per-game Need vs observed movement |
| `docs_v4/sensitivity_m2.json`, `docs_v4/sensitivity_m2.md` | **new**: generated results |
| `docs_v4/diagnostics_m1.json` | regenerated under the new seeding |
| `docs_v4/V4_BLUEPRINT_AND_MILESTONE_1.md` | correction note added to risk #1 |
| `docs_v4/MILESTONE_2_REPORT.md` | this file |

---

## 3. Exact test results

Run with Python 3.13 and pytest in an isolated venv:

```
python -m pytest tests_v4  ->  64 passed in 21.86s
```

| Suite | Tests | Result |
|---|---|---|
| `test_behavior.py` | 8 | 8 passed |
| `test_engine.py` | 11 | 11 passed |
| `test_grading.py` | 12 | 12 passed |
| `test_replay.py` | 7 | 7 passed |
| `test_safety.py` | 14 | 14 passed |
| `test_settlement.py` | 7 | 7 passed |
| `test_variants.py` | 5 | 5 passed |

**Mutation checks (M2).** Each defect was injected into a throwaway copy and reverted:

| Injected defect | Caught by |
|---|---|
| Grader fills a missing price with −110 | `test_missing_data_is_never_invented` |
| Production-path guard disabled | `test_refuses_production_paths`, `test_default_guard_covers_the_real_production_path` |
| Closing line peeks 1 h past kickoff | `test_closing_line_never_uses_post_kickoff_quotes` |
| Result selection ignores the grading cutoff | `test_result_selection_guards_lookahead_and_revisions` |

**Defects the tests found in my own new code (fixed):**

1. **ROI was rounded to 4 decimals before aggregation.** This was a precision loss, caught by the exact-units tests.
2. **Redirect lookup compared INTEGER ids with text ids.** A redirected CFB game was wrongly excluded.
3. **Replay graded results against the *market-data* horizon.** Every final score (recorded later) was being ignored. The grading cutoff is now the latest result retrieval in the copy (or an explicit cutoff).

V3 baseline tests were not re-run, because no baseline file changed. In M1, running them rewrote a tracked V3 test database, which I then restored.

---

## 4. Investigation: why was the Need almost always the underdog?

**Method.** 35 variants × 4 seeds × 10,000 bettors on the fixture week (`tools_v4/sensitivity_study.py`, ~8 min). Full output is in `docs_v4/sensitivity_m2.md` / `.json`.

- **Common random numbers:** variants share arrival and decision draws, so differences between variants are caused by the variant.
- **Favourite pressure (FP)** = (P&L if the underdog covers every accepted number − P&L if the favourite covers every accepted number) / handle. **FP > 0 means the book needs the underdog.** Because P&L is linear in wagers, FP splits **exactly** into one term per archetype.
- **"Dog x/24"** counts the 6 non-pick'em games × 4 seeds.

### 4.1 The decisive experiments

| Variant | Behaviour | Market path | Underdog Needs | Mean FP |
|---|---|---|---|---|
| baseline | as configured | fixture moves | 20/24 | +0.588 |
| neutral_behaviour | **no** directional preferences | fixture moves | 20/24 | +0.391 |
| mkt_static | as configured | **frozen at open** | 24/24 | +0.342 |
| neutral_mkt_static (null control) | none | frozen | **11/24** | **−0.028** |
| mkt_reversed | as configured | **every move mirrored** | 14/24 | +0.212 |
| neutral_mkt_reversed | none | mirrored | **8/24** | −0.052 |
| neutral_mkt_symmetric | none | mirrored-venue slate | 12/24 | +0.054 |

**Exact attribution of baseline FP (+0.588):**

| Archetype | FP contribution | Share of FP |
|---|---|---|
| SHARP | **+0.422** | ~72% |
| HIGH_STAKES_REC | +0.119 | ~20% |
| REC_CASUAL | +0.049 | ~8% |
| all others | about −0.002 net | ~0% |

The mean handle share of HIGH_STAKES_REC is 46.6%, yet it contributes only about a fifth of the pressure.

### 4.2 Per game: the sharp component tracks the observed market move

"Fav move" is the movement from open to close relative to the closing favourite; + means the favourite got more expensive. Generated by `tools_v4/analyze_sensitivity.py`.

| Game | Fav move (actual fixture) | Sharp FP | Need (baseline) | Fav move (reversed) | Sharp FP (reversed) | Need (reversed) |
|---|---|---|---|---|---|---|
| g_tnf | −1.0 | −0.45 | **favourite** 4/4 | +1.0 | +0.45 | underdog 4/4 |
| g_sun1 | +1.0 | +0.86 | underdog 4/4 | −1.0 | −0.86 | **favourite** 4/4 |
| g_sun2 | +3.5 | +1.09 | underdog 4/4 | +3.5* | +1.09 | underdog 4/4 |
| g_sun4 | +1.0 | +0.41 | underdog 4/4 | −1.0 | −0.41 | favourite 2/4 |
| g_snf | +0.5 | +0.36 | underdog 4/4 | −0.5 | −0.37 | favourite 3/4 |
| g_mnf | +1.0 | +0.26 | underdog 4/4 | −1.0 | −0.26 | underdog 3/4 (preference outweighs market) |

\*Mirroring g_sun2's favourite flip produces a game that again moved toward its (new) favourite.

The sharp component has **the same sign as the observed line move in all 12 game × market-path cells**. It is exactly **zero** when the lines are frozen, because sharps only bet off-market numbers and with frozen lines there are none.

### 4.3 What each factor did (one at a time, market held fixed)

| Factor | Variants | Underdog Needs | Mean FP | Reading |
|---|---|---|---|---|
| Favourite preference | ×0 / ×0.5 / ×1.5 / ×−1 (all archetypes) | 20 / 20 / 22 / **16** of 24 | 0.38 / 0.49 / 0.67 / 0.22 | Moves the magnitude. Even fully **reversed**, the sharp/market component keeps most Needs on the underdog. |
| High-stakes share | 0% / 8% | 20 / 22 | **0.79** / 0.52 | Removing high-stakes bettors **raises** FP per dollar: their handle leaves the denominator while sharp handle stays. Not the driver. |
| High-stakes bankroll | $10k / $100k | 20 / 23 | 0.71 / 0.49 | Same normalisation effect |
| High-stakes favourite preference | 0 / −0.4 | 20 / 19 | 0.44 / 0.30 | Their preference is ~20% of FP |
| Sharp share | 0% / 6% | 20 / 20 | **0.19** / 0.75 | The largest single lever on magnitude |
| Underdog contrarians 16%; no team-loyal | — | 20 / 20 | 0.56 / 0.61 | Small |
| Timing (all steady; sharps late; recs early) | — | 20 / 20 / 20 | 0.75 / 0.67 / 0.68 | Changes magnitude, not direction |
| Price tolerance: all bettors max −110 | — | **14** | 0.23 | Fixture favourites priced −120 become unbettable for everyone, including sharps |
| Price tolerance: max −150; selective no shopping | — | 20 / 20 | 0.62 / 0.59 | Small |
| Sharp reference = all books (not lowvig/betonline) | — | 19 | **0.23** | Fewer apparent edges, so less sharp money |
| Book offer lag 0 / 30m / 1h / 2h | — | 20 each | 0.58 → 0.67 | Edges come mostly from **retail books lagging the reference books**, not from the book's own lag |
| Retail books lag 0 h / 6 h (market structure) | — | 20 / 20 | **0.23** / 0.70 | Strong effect on magnitude |

### 4.4 Conclusions

1. **The engine has no built-in direction.** With no behavioural preferences and no line movement, Needs split 11/24 (and 12/24 on the mirrored slate), with mean FP ≈ 0.
2. **Most of the fixture's underdog skew is a response to market conditions, but those conditions are synthetic.** The sharp component follows observed line movement game by game and reverses with it. In the fixture I wrote, 5 of the 6 non-pick'em games move toward the favourite, so this component mostly points at underdogs. Whether real NFL/CFB lines move toward favourites more often than not is an **empirical question only real snapshots can answer**. The fixture says nothing about it.
3. **The rest is an explicit behavioural assumption.** Recreational and high-stakes favourite preference adds underdog pressure in **every** game regardless of market (FP +0.34 with frozen lines; 24/24). Its existence is literature-motivated (favourite-longshot bias); its **size is UNVALIDATED**.
4. **The magnitude of the market-driven part rests on further assumptions:**
   - the sharp share (3%) and stake/limit settings
   - which books count as the "reference"
   - how long retail books lag

   Real snapshots will show the lag structure (§6, item 2). The sharp parameters remain assumptions.
5. **The Need direction is robust to timing, archetype mix, and book lag.** It is sensitive to the price tolerance of bettors when favourites are priced beyond −110.
6. **Per your instruction, nothing was tuned to balance favourite and underdog Needs.** Recommendation for M3 (not implemented): report each game's Need **with its decomposition**, separating the market-reactive component (sharp archetypes) from the preference component (recreational archetypes). The exact per-archetype terms already exist in `exposure()["by_archetype"]`. A reader would then see when a Need is "the sharps hit the number" versus "the public likes the favourite".

---

## 5. Milestone 2 — grader details

**Per pick:**
- **ATS** at the pick's own line.
- **Units** at the pick's price (1 unit risked; push = 0).
- **CLV in points** against the consensus close strictly before kickoff.
- **Price CLV and no-vig CLV**, computed only when the pick line equals the closing line.

**Never invented:**
- No side, line, timestamp, or known game → `UNGRADABLE`.
- Decided at or after kickoff → `NOT_PROSPECTIVE`.
- No price → graded ATS but **excluded from ROI**; no default −110.
- Results are used only if final, retrieved after kickoff, and retrieved at or before the grading cutoff. Score revisions are flagged.

**Provenance separation:**
- ROI is reported separately as `roi_recorded_odds` (`RECORDED`) and `roi_derived_odds` (`DERIVED_FROM_RECORDED_QUOTES`), never combined.
- Every pick from a mutable source carries `SOURCE_MUTABLE`.

**Statistics:**
- W‑L‑P, win % excluding pushes, Wilson 95% CI, and exact two-sided binomial p.
- Break-even win % at the mean price, and CLV summaries.
- Calibration (Brier and reliability bins) **only if a forecast probability was recorded**; otherwise it says so.
- A sample warning below 300 decided picks.
- Grouped by sport, tier, market condition, signal agreement, price provenance, and source.

**Importers:**
- `picks_from_experimental_mixed_signals`. Column names are taken from the INSERT in `operations/run_collection.py` and still have to be checked against the real `.schema`.
- `picks_from_csv` (a hand ledger; blanks stay blank).
- `picks_from_v4_decisions` (PLAY rows only; currently none).
- `reconstruction_audit` produces a verdict: RECONSTRUCTABLE / PARTIALLY / NOT / NO_PICKS_FOUND.

**Need accuracy** (`need_outcome`): did the Need side cover the **closing** number, and what was the synthetic book's **realised** P&L at the real final margin, settling every wager at its own number? This measures exposure accuracy, not betting profit.

**What can be said about historical picks today:**
- Nothing verified. No pick rows or ledger have been supplied.
- From code alone:
  - `experimental_mixed_signals` holds at most one row per game and signal version: the last state written in the T‑15 min window.
  - Signals that qualified earlier and were later deleted are unrecoverable.
  - No single bet price was ever stored.

---

## 6. Milestone 4 — replay framework (prepared, not run)

**Safety:**
- `open_historical_copy` refuses `/opt/arbitredge/db/arbitredge.db`, any file in that directory, and any path that resolves (including through symlinks) or hard-links to it.
- Copies are opened with `mode=ro` + `query_only`.
- The replay test verifies the copy's SHA‑256 is unchanged after a full replay.

**Schema:**
- `validate_copy` checks the inferred schema and reports missing tables and columns rather than assuming.
- Inferred columns: `market_snapshots`, `games_canonical`, `teams_canonical`, `game_results`, `game_context`, and optionally `game_canonical_redirects`.

**Slates** (`build_week_slates`):
- **NFL** weeks run from Thursday 00:00 UTC (same rule as V3).
- **CFB** weeks run from Monday 00:00 UTC (assumption).
- **Window:** from max(first quote, first kickoff − 10 days) to the last kickoff.
- **CFB Top-25 eligibility** is decided **at window start** from the latest context row observed by then, including redirects.
- **Standalone** = no same-sport game within ±2 h.
- **National TV** uses V3's network list.

**Completeness:** a week replays only if the copy's market data extends to its last kickoff (`IncompleteCopy` otherwise). Grading uses the copy's result horizon.

**Not yet decided or verified:**
1. The real schema.
2. Snapshot cadence (it determines the time resolution of offers and the realism of "retail lag").
3. Whether 10 days is the right open window.
4. The CFB week boundary.

---

## 7. Unresolved assumptions (complete list)

1. **All behavioural parameters are UNVALIDATED:** archetype shares, bankrolls, unit sizes, frequencies, preferences, timing profiles, price tolerances, sharp edge thresholds, book limits, and offer lag.
2. Favourite preference exists among recreational bettors (literature-motivated); its **size** is unknown.
3. Sharp bettors exist at 3% with $60k median bankrolls and a $3k limit, and react to gaps between the book and reference books. **This is a behaviour, not demonstrated skill.**
4. The reference books are `lowvig` and `betonlineag`.
5. Team popularity is neutral (per your instruction).
6. Bankroll is static within a run; there is no cross-week feedback yet (M3).
7. The synthetic book offers the consensus lower-median with a 10-minute lag. It does not set its own lines.
8. The replay schema is inferred, and the week and window rules are stated in §6.
9. The fixture market paths are synthetic. No conclusion about real favourite/underdog Need frequencies can be drawn from them.

---

## 8. What I need from you (read-only, from the server)

1. **Schemas:** `.schema` for `market_snapshots`, `games_canonical`, `teams_canonical`, `game_results`, `game_context`, `game_canonical_redirects`, `experimental_mixed_signals`; the trigger list.
2. **Snapshot cadence:**
   ```sql
   SELECT sport, COUNT(*), COUNT(DISTINCT canonical_game_id), COUNT(DISTINCT book), MIN(observed_at), MAX(observed_at)
   FROM market_snapshots ms JOIN games_canonical USING(canonical_game_id) GROUP BY sport;
   ```
   Also a per-game median count of snapshots per book.
3. **Pick data:** counts and date range of `experimental_mixed_signals`; rows written at or after kickoff; and **whatever file produced 37‑34‑5** (as a CSV export, if it exists).
4. **For real replay:** the isolated read-only copy you plan to create, plus its path, so the guard and `validate_copy` can be run against it first.

**Decision points:**
- (a) authorise the real-data replay once the copy exists;
- (b) whether to implement the Need decomposition in M3;
- (c) M3 scope (bankroll feedback from settled results).
