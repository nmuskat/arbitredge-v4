# Synthetic sportsbook audit and collection plan (2026-10-28)

Everything in this document about bettors, wagers, splits and Need is **MODELED**: simulated customers betting into real collected prices. None of it is observed betting data.

**Evidence base.** All figures come from the real ledger and collected odds for the week of Tue 2026-10-27 12:00 UTC:
- 4 odds shards, 2 ledger files per sport;
- `tools_v4/book_audit.py` and `tools_v4/collection_plan.py`, both re-runnable.

Engine versions:
- **m1** is the engine that produced the ledger up to today.
- **m4** is the corrected engine introduced by this audit. m1 runs stay in the ledger, unchanged and marked "superseded".

---

## 1. Bettor identities

| Check | NFL | CFB |
|---|---|---|
| Bettors | 10,000 | 10,000 (a separate population) |
| Unique, permanent IDs | 10,000 | 10,000 |
| Population hash re-verified from the stored ledger | yes | yes |

How identity works:
- Each bettor is generated from a seed of (sport, `PARAMS_VERSION`, index). Bettor *k* is the same person in every build and every week.
- Each has an archetype, a bankroll, and traits: unit size, arrivals per week, favourite lean, home lean, over lean, popularity, recency, and a loyal team where relevant.
- `PARAMS_VERSION` was deliberately **not** changed by this audit's fixes, so all 20,000 identities persist. Book-policy changes are recorded in the parameters hash stamped on every run and wager instead.

Archetype mix (NFL; CFB is similar):

| Archetype | Share | Median bankroll | Arrivals/week | Timing profile |
|---|---|---|---|---|
| REC_CASUAL | 51% | $403 | 2.0 | weekend, late |
| TEAM_LOYAL | 12% | $411 | 1.5 | weekend, late |
| NARRATIVE_CHASER | 12% | $401 | 2.5 | weekend, late |
| SELECTIVE_PRICE_SENSITIVE | 10% | $3,098 | 1.2 | steady |
| UNDERDOG_CONTRARIAN | 8% | $587 | 1.5 | steady |
| HIGH_STAKES_REC | 4% | $43,574 | 1.5 | weekend, late |
| SHARP | 3% | $57,209 | 8.0 | early week |

**Timing.** About 20,500 arrivals per sport-week, weighted to Thu–Sun:
- by weekday (Central time), Mon 1,918 · Tue 1,493 · Wed 1,821 · Thu 3,274 · Fri 2,927 · Sat 4,463 · Sun 4,582;
- by time of day, 08:00–24:00 Central carries 92%.

Each arrival time is a deterministic function of (week, bettor), so it never changes once the week is drawn.

**Not yet implemented (limitations):**
- Bankrolls don't evolve with results: there's no settlement feedback and no loss-chasing.
- The weekly risk budget resets each week.

## 2. How a bettor decides (`sportsbook_v4/engine.py::_decide`)

For each arrival, in order:
1. **Is anything open?** Only games not yet started, with a real quote no older than 6 hours at the bettor's offer time (decision time minus a 10-minute re-quote lag). Otherwise the outcome is `no_open_market`. This is the most common outcome at the current collection rate: 2,365 of about 2,500 NFL arrivals so far this week.
2. **Which game?** For non-sharps, a weighted draw:
   - kickoff proximity, `exp(-hours_to_kickoff / horizon)` with the horizon set per archetype;
   - a primetime/standalone weight, now active (see below);
   - for loyal bettors, their own team's game.
3. **Which market?** A per-archetype weighted draw among the spread, total and moneyline that are open.
4. **Which side?** A logistic choice from the bettor's own traits:
   - favourite lean × (is this side the favourite at the offered line);
   - loyalty to their team;
   - home lean (population mean exactly 0);
   - popularity and recency, currently **inactive** (see below);
   - on totals, the bettor's over lean.
5. **Would they take this price?**
   - **Price limit:** a side priced above the archetype's maximum (e.g., about −130) is refused.
   - **Shopping:** price-sensitive bettors decline if another book is ≥ 0.5 point or ≥ 1 point of implied probability better.
   - **Conviction:** they also need conviction ≥ 0.58.
6. **How much?** Bankroll × unit fraction × log-normal noise (× an edge multiplier for sharps):
   - capped by the book's limit ($10,000; $3,000 for sharps);
   - capped by the bettor's remaining weekly budget;
   - and at least $5.

**Sharps** skip steps 2–4. They bet only when the book's number is worse than the market-maker reference (Pinnacle, Circa, BetOnline, LowVig, BookMaker) by ≥ 0.5 point, or ≥ 1.5 points of implied probability at the same line.

**No step looks at the book's liability, Need, or the other customers' bets.** Need is purely the result of these individual decisions. The book reacts to *sharp action* (section 2a), never to its own Need.

The side choice is calibrated: over 200,000 simulated draws, realised over rate 44.84% versus intended 44.75% (contrarians).

### Inactive behavioural dimensions on live data

| Dimension | Status |
|---|---|
| Team popularity | no sourced popularity table, so every team weighs 1.0 by design |
| Recency ("hot team") | no live results feed yet |
| National-TV weight | network assignments are not in the odds data |
| Primetime / standalone | **fixed:** games alone in their kickoff window are now flagged from the schedule. This week, NFL TNF (Oct 30 00:15 UTC), SNF and MNF; no CFB game. |

### 2a. Confirmed bugs fixed in engine m4 (no API spend)

1. **The offered line leaned to one side whenever books disagreed.** The book took the *lower* middle value: fewer points for the home side, a lower total for overs.
   - On real data, 23 of 341 spread offers and 47 of 341 total offers were shifted, always the same way.
   - Fix: true median line; the line offered by more books; then the one nearer the mean; an exact tie is a hash coin-flip. Mirroring a market mirrors the offer (tested).
2. **The two prices weren't a real market.** Each side's price was chosen separately, so 123 of 341 offered pairs mixed two sportsbooks.
   - Fix: line and both prices come from one real book, the median no-vig book; with two middle books, the higher-margin one.
3. **The sharps' "true price" came from one operator.** LowVig and BetOnline only. It now uses the market-maker set above.
4. **The book never moved its number after sharp action.** All ~300 simulated sharps hit the same off-market line.
   - Result in m1: 88.8% (NFL) and 95.0% (CFB) of handle was sharp, 100% on underdogs. Need was therefore "whatever retail books shade".
   - Fix: after a respected (sharp) wager the book **copies the market makers' quote** for that market until the retail market moves. Retail books do this in practice; the parameters remain unvalidated.

**Results on the same real week:**

| | m1 sharp bets | m1 sharp handle | m4 sharp bets | m4 sharp handle | m4 sharp bets on favourites |
|---|---|---|---|---|---|
| NFL | 95 | 88.8% | 9 | 47.0% | 25% |
| CFB Top-25 | 355 | 95.0% | 40 | 46.7% | 40% |

Sharps are still about half of a mid-week handle, because recreational volume concentrates on Thursday to Sunday. Whether that is realistic is an open calibration question (section 9).

## 3. Immutability

Wagers are protected at four levels:
- **Database:** append-only tables (UPDATE and DELETE triggers raise; tested).
- **Ledger files:** every advance is exported once to release `v4-book` and never overwritten. The upload step has no `--clobber`.
- **Load checks:** every build reloads the files and refuses on any hash mismatch, gap, reordering or altered row.
- **Replay:** every build re-simulates the week from scratch and requires the identical wager set.

What each wager keeps:
- its own accepted line, price, stake, market, side, time, and offer time;
- the books behind the price, plus the decision inputs (`decision_json`, including `book_mode`).

**No repricing anywhere.**
- Settlement, payouts, liability and Need all use each wager's accepted line and price.
- The consensus "reference" line is used only to label scenarios.
- **Audit:** for every m4 wager, the stored line and price equal the real representative quote at its offer time: 1,080 of 1,080, 0 mismatches. Maximum quote age at acceptance is 6.2 hours, which is the 6-hour rule plus the 10-minute lag.

## 4. Accounting (each figure per game, market and side, from the ledger only)

| Figure | Definition |
|---|---|
| Tickets | count of wagers |
| Handle | sum of stakes |
| Ticket % | side tickets ÷ market tickets |
| Handle % | side handle ÷ market handle |
| Potential payout (new) | Σ (stake + winnings at the wager's own price) |
| Book P&L if this side wins (new) | market handle − that side's potential payout |
| Need | the side whose result gives the book the higher P&L when every accepted number on it wins. Spreads and totals are evaluated over every final margin or total, exactly; push and middle numbers are listed separately. |

**Reconciliation, done independently from raw rows:** 186 side rows (m4) and 184 side rows (m1); 0 mismatches in tickets, handle, both percentages, payouts, book P&L, or Need side.

Moneyline ties settle as no action (push).

## 5. Do sharp, profitable, recreational and losing bettors behave differently?

**What differs:**
- **Recreational types differ by assumption:** lean to favourites and overs, team loyalty, weekend timing, small units.
- **Sharps differ structurally:** they only bet numbers that beat the market makers, bet early in the week, and stake more.

**What doesn't exist yet:**
- **No "profitable" or "losing" behaviour.** No bettor's behaviour depends on their results, because wins and losses don't feed back.
- **No empirical basis.** None of the parameters are fitted to customer data; ArbitrEdge has none.

The directions are the commonly reported patterns: recreational bettors favour favourites and overs, and sharp money moves early. The magnitudes are assumptions, labelled as such in `sportsbook_v4/params.py`.

## 6. Proposed collection schedule (planning only; nothing changed)

**Cost facts:**
- One fetch costs 3 credits (spreads + totals + moneyline, one region) and returns **every** game of that sport.
- So prioritising NFL and Top-25 games means choosing *when* to fetch. The only per-game cost is the close: a fetch at most 45 minutes before each distinct kickoff time.

**Design** (rules are "next priority kickoff within H hours and last fetch at least G hours old"):

| Tier | Rules | Final-72-hour coverage |
|---|---|---|
| test (current) | 24 h / 12 h, 72 h / 24 h | ~7–8 snapshots before each kickoff |
| **balanced** | 6 h / 2 h, 24 h / 4 h, 72 h / 8 h, 168 h / 24 h | ~15–16 |
| aggressive | 3 h / 45 min, 12 h / 1.5 h, 24 h / 3 h, 72 h / 6 h, 168 h / 12 h | ~18–20 |

All tiers include:
- **Closes:** a fetch at most 45 minutes before each kickoff time.
- **Opening lines:** the 168-hour rule catches Sunday-night and Monday openers.
- **Movement windows:** the early-week sharp window and the injury and news days (Wednesday to Saturday) are covered by the 72-hour and 24-hour rules.
- **CFB targeting:** college close fetches target AP Top-25 kickoff times only.

Recommended additions to the cron grid, at no extra cost unless they are used:
- a Sunday 13:00–15:00 UTC window, for NFL international games (Cincinnati @ Atlanta, Nov 8 14:30 UTC, currently has **no** close attempt);
- Tuesday to Friday 23:00–03:00 UTC windows, if non-Top-25 weekday CFB closes are wanted.

## 7. Monthly credit estimates

Simulated on this week's real kickoff times, the real cron grid, 25–50-minute GitHub delays and 5% dropped runs:

| Tier | CFB close target | NFL fetches/week | CFB fetches/week | **Credits/month** | Closes observed |
|---|---|---|---|---|---|
| test (current) | all FBS | 13 | 26 | **≈ 509** | NFL 6/6, CFB 20/27 |
| test | Top-25 | 13 | 13 | **≈ 340** | NFL 6/6, Top-25 9/9 |
| balanced | Top-25 | 33 | 25 | **≈ 757** | NFL 6/6, Top-25 9/9 |
| aggressive | Top-25 | 43 | 32 | **≈ 979** | NFL 6/6, Top-25 9/9 |
| balanced / aggressive | all FBS | — | — | ≈ 1,018 / 1,305 | CFB 20/27 |

**Findings:**
- **The current test tier will probably hit its 500-credit cap near month-end.** The gate refuses safely; it cannot overspend. Targeting CFB closes at Top-25 kickoffs alone fixes this, at about 340/month. That narrows the line-shopping dataset to NFL and Top-25 CFB closes, so **it is an owner decision.**
- **Balanced and aggressive both fit far inside the approved 6,000/month V4 cap,** leaving at least 5,000 credits of V4 headroom and the gateway's 2,000-credit V3 floor untouched.
- **V3 usage looks unexpectedly low.** The provider counter shows 27 non-V4 credits used in the current cycle as of Oct 28 15:58 UTC, about 11 a day. The cycle may simply have reset around Oct 26. ArbitrEdge V4 cannot inspect V3, so this is reported, not interpreted.

**GitHub Actions minutes:**
- **Collection:** about 705 a month, unchanged, since the cron grid doesn't change.
- **Dashboard rebuilds:** about 1 minute each, which adds roughly 250 a month (balanced) or 330 (aggressive).
- **Total:** within the free 2,000.

**Nothing is activated.** The tier is the owner's `V4_TIER` variable; the default remains `test`.

## 8. Limits on credible sharp-money detection

1. **No observed betting splits.** Reverse line movement and ticket-versus-money divergence need real public splits. ArbitrEdge has only *modeled* splits, which describe its simulated customers, not the market. They are never used as evidence of real sharp action.
2. **Odds alone can't identify who bet.** A market-maker move is consistent with sharp action but also with news, injuries, weather or risk balancing. Without bet-level data, "sharp" stays a hypothesis.
3. **Timing resolution.** At the test tier, snapshots are 12–24 hours apart, so steam (several books moving within minutes) and "which book moved first" are unobservable. The analysis reports "timing unresolved" instead of guessing. The balanced tier gives 2–4-hour resolution in the final day, which is still too coarse for true steam.
4. **Coverage.** 10 books, one region. No offshore or Asian market makers beyond Pinnacle, and no line history before ArbitrEdge's first fetch.
5. **No ground truth yet.** Signals can only be validated against closing-line movement and results, which needs weeks of closes (section 9).

## 9. Validation plan

Every claim is checked out of sample, on games after the code version that makes it was frozen.

| What | Test | Pass criterion |
|---|---|---|
| Line-shopping EV | closing EV: the price taken, evaluated at the observed closing consensus; game-clustered bootstrap CI | CI lower bound > 0 over ≥ 300 independent games; positive in ≥ 2 of 3 walk-forward folds |
| Sharp-money signals | does the suspected side's price improve to the close (CLV)? Hit rate by score bin; Brier score of a calibrated probability | CLV hit rate above 50% with a CI excluding 50%, within the score bins used; calibration slope within 0.8–1.2 |
| Simulated splits | not checkable against reality without a licensed splits feed. Interim check: compare modeled ticket % with published *public* consensus percentages where legally available, as a sanity range, never as truth | documented divergence, labelled |
| Simulated Need | does Need predict the result? Expected **no** edge: Need is a liability fact. Test Need side ATS and against the close | if Need beats the close out of sample (CI > 0, ≥ 300 games), investigate; otherwise report "no edge", which is the expected outcome |
| Behaviour assumptions | sensitivity: re-run each week under the parameter variants (already shown as ranges); flag any split or Need that flips sign across variants as assumption-driven | the share of games whose Need is assumption-driven is reported weekly |
| Multiple testing | every strategy and signal registered before evaluation (`edge_v4.ledger`); Holm or Benjamini–Hochberg across simultaneously tested ideas | adjusted p < 0.05 and the CI rule above |
| Calibration of model probabilities | reliability curves and Brier/log-loss against results; Engine C (NFL) already ties the close | no strategy is promoted on a model that is worse than the closing line |

Status rules:
- Every idea starts as **WATCH/PAPER**.
- Nothing becomes an official play until it passes the evidence gate in `V4_REBUILD_PLAN.md` §G.
- The default decision is **NO BET**.
