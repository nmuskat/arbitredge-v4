# Book Need Tracker (research / paper only)

**Question under test:** when broad recreational betting pressure leaves the synthetic sportsbook with a large financial Need, does betting the side the book needs have a measurable advantage?

- **Status:** WATCH / paper. Owner-approved design, 2026-10-29.
- The thresholds below are pre-registered experimental choices, **not validated profitability rules**.
- Nothing here is a recommendation or an official play. Rebuild plan §G is unchanged.

## 1. Readiness audit (2026-10-29)

| Foundation | State before this work | Action |
|---|---|---|
| Immutable wager ledger | ready: append-only table triggers, hash-verified `v4-book` files, replay verification each build | unchanged; the tracker only reads it |
| Need from actual P&L | ready: `settlement.exposure` settles every accepted spread wager at its own line and price over every final margin | reused as-is |
| Public-pressure metrics | partial: splits by side; no recreational / sharp separation, no per-bettor concentration, no weekly rank | built (`need_tracker.pressure`) |
| Pre-kickoff records | not usable: `prospective.py` writes a SQLite table, which does not persist between GitHub Actions runs | built: immutable release files |
| Final scores for live games | missing (only historical nflverse research data) | built: `data_v4/results.py` |
| Grading and intervals | ready: `grading.ats`, Wilson interval, binomial test | reused; ROI interval added |
| Settlement of the simulated bettors and bankrolls (`SETTLEMENT_BANKROLL_PLAN.md`) | not built | **not required** for this tracker, which grades the Need side as a 1-unit paper bet. The final scores ingested here are the input phase 2 of that plan needs. |
| National-TV / primetime flags | NFL primetime via "standalone" worked; national TV never set; CFB night games unclassified | fixed for weeks opening from 2026-11-03 (§6) |

## 2. Definitions

**Need** (`need_tracker.need_of`):
- The spread side whose cover leaves the synthetic book better off, comparing the book's P&L if home covers every accepted number with the P&L if away does.
- **Liability:** the book's P&L if the side it does not need covers. Negative means a loss.
- **Swing:** the difference between the two outcomes.

**Public pressure** (`need_tracker.pressure`), over the game's spread wagers:
- **Recreational:** every archetype except SHARP. Ticket share and handle share per side.
- **Sharp handle share.**
- **Largest bettor:** stakes summed per simulated bettor, as a share of the handle.
- Counts of wagers and distinct bettors.

**Weekly handle rank:** the game's spread handle against every game in that sport's weekly run, at the same moment (point-in-time; see §7).

**Pre-registered filter** (`QUALIFY`), all five required:

| Rule | Threshold |
|---|---|
| weekly spread-handle rank | top 25% (rank ≤ ceil(0.25 × games)) |
| spread wagers | ≥ 100 |
| largest single bettor | ≤ 25% of spread handle |
| recreational handle on one side | ≥ 70% |
| recreational tickets | majority (> 50%) on that same side |

**Alignment:**
- **ALIGNED:** fading the recreational side is the side the book needs. Only an aligned, qualified game becomes a **research pick**.
- **DISAGREE:** the book's Need sits on the public side, for example because of sharp money or line positions taken at different numbers. It is flagged, never forced into a pick.

## 3. Records and immutability

| Record | When | Where |
|---|---|---|
| snapshot | every dashboard build, for each NFL / AP Top-25 game kicking off within 24 h; taken strictly before kickoff by data time **and** build wall-clock time | release `v4-need-YYYY-Www`, `need_<sport>_<built>.json.gz` |
| decision | first build after kickoff: the last pre-kickoff snapshot, sealed once (`OK`, `EARLY` if more than 3 h before kickoff, or `NO_PRE_KICKOFF_SNAPSHOT`) | release `v4-need-decisions`, `decisions_<built>.json.gz` |
| final scores | NFL daily in `odds-analyze` (nflverse, no key); CFB in `cfb-rankings` (CFBD, 1 call; 2 in bowl season) | release `v4-results`, `results_<sport>_<fetched>.json.gz`, saved only when scores changed |

**Safeguards:**
- Every record and file carries a SHA-256 hash, verified on load. A tampered record or file stops the tracker with INTEGRITY_FAILED.
- Uploads never use `--clobber`. Two different decisions for one game are an integrity error.
- Each snapshot stores the hash of the wager IDs it summarised. The tracker never writes to the wager ledger. Verified: identical ledger hash and splits with and without the tracker.

## 4. Grading

**Each decision is graded at its own recorded line and price:** the real market price the synthetic book showed its customers at the snapshot, with the book that quoted it.

**Possible statuses:**
- GRADED: win, loss or push, plus units at the recorded price.
- AWAITING_RESULT: no final score from the source yet. Nothing is inferred.
- NO_PRICE, NO_NEED and NO_DECISION: excluded and counted.

**Results matching:**
- NFL: team names and the kickoff's Eastern date.
- CFB: the CFBD school matcher, kickoff within 36 h.
- A later, different score is used and flagged as a correction; both versions stay on record.

**Reported on the Need Tracker tab, separately:**
- all Need sides;
- the research subset;
- qualified disagreements;
- by largest-bettor share, by weekly handle percentile, and by sport.

Each line shows the W-L-P record, win % with a 95% Wilson interval, ROI with a 95% interval and the break-even rate. Any line with fewer than 300 decided games is marked INSUFFICIENT SAMPLE.

## 5. What is operational vs experimental

**Operational and tested:**
- snapshots, sealing, immutability checks;
- results ingestion and matching (NFL tested on real nflverse data; CFB on the documented API shape — the first live CFB scores fetch is Sunday's `cfb-rankings` run);
- grading and the dashboard tab.

**Experimental:**
- the thresholds;
- the simulated public's behaviour;
- whether any of it predicts covers.

## 6. Game-window classification v2 (weekly runs opening from 2026-11-03)

- **NFL:** `national_tv` = standalone. That covers TNF, SNF, MNF, international games and any lone window.
- **CFB:** primetime = standalone or kickoff 7 pm – midnight ET. National-TV assignments aren't in the data, so they aren't guessed.

Runs already open keep their flags, so no accepted wager or replay changes.

## 7. Known limitations

- **Bettors are simulated.**
  - Their tendencies (favourites, home teams, own-team loyalty, narratives) are unvalidated parameters.
  - Team popularity is neutral: no "popular team" table is used.
- **Handle rank is point-in-time.** Earlier kickoffs are compared with later games that are still filling, so early-week standalone games rank high more easily. A post-hoc weekly rank would be cleaner but could not be known at decision time, so it isn't used for qualification.
- **Snapshot timing.** Snapshots exist only when a dashboard build runs. In the final 6 h builds are about every 45 minutes, so a decision is usually 15–60 min before kickoff.
- **Sample size.** One NFL season gives about 270 games and only a handful of qualified ones (§8). Proof takes several seasons.

## 8. Exploratory historical sensitivity test

See `docs_v4/NEED_TRACKER_HISTORY.md`. It is a synthetic backtest, not evidence about real public bettors.
