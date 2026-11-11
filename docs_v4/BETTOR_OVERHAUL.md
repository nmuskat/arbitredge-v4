# Synthetic bettor behaviour overhaul (research branch `research/bettor-overhaul`)

Research only. Nothing in production (`v4-dev`, engine sportsbook_v5.r2) or in any ledger, settlement, frozen NEED
record, collection schedule, SIMS, or the Thursday pilot was changed. No API credits were used.

## 1. Root cause: why every group wins about 50%

**Short answer:** the groups differ in *what they like*, not in *how they decide relative to the price*. Every
recreational customer picks a side with the same formula and then pays the same price. Against an efficient line,
that gives about 50% wins before juice and the same expected loss for everybody. This is mostly how efficient
markets work, plus three modelling gaps.

| # | Question | Finding (code path) |
|---|---|---|
| 1 | Same side-selection mechanism? | **Yes.** Every non-sharp group uses `engine._recreational_side`: one logistic function of favourite sign, team popularity, recency, loyalty and home preference. Totals use one fixed `over_pref` per customer (`_recreational_pick`). Groups differ only in the weights (`params.ARCHETYPES`). |
| 2 | Independent of odds, fair probability, price changes? | **Yes.** The utility uses only the *sign* of the spread, never its size, the price or the fair probability. The only price check, `max_implied_prob`, blocks expensive juice (about −130 and worse). It never compares the price with fair value. |
| 3 | Do sharps have information? | **No.** The production rule (`engine._sharp_pick`) compares raw points without juice or key numbers, so sharps bought −EV prices (EV at decision **−2.5%**; realism audit). |
| 4 | Do team-loyal fans accept worse prices? | **No.** Everyone gets the same book offer (the retail median) and nobody checks value. Loyal fans' EV at decision is −4.3%, like everyone else. |
| 5 | Do narrative chasers react to narratives? | **No. The production book passes `NoFacts()`** (`live_book.py` lines 376 and 401), so `recency_signal` is always 0. Recency weights multiply zero for every group. Their only distinctive features are more primetime and more overs. |
| 6 | High-stakes vs casual beyond size? | **Barely.** Same function, slightly different weights. Favourite share 58% vs 61%, overs 55% vs 57%. |
| 7 | Individual skill, price sensitivity, discipline, persistence? | **Partly.** `customers_v5` gives individual engagement, market mix, timing, price-cap jitter and risk fraction. There are no individual discipline or price-sensitivity traits, and no skill. |
| 8 | Reacting to real pre-kickoff information? | **No** (see 5). |
| 9 | Stakes, frequency and exposure realistic? | **Plausible but unvalidated.** About 2.2 bets per active customer-week. High-stakes customers: median stake $1,153 (2.8% of bankroll), 99th percentile $9,300, $10,000 limit. |
| 10 | Forced toward 50/50? | **No mechanical forcing.** I re-graded all 124,082 spread/total bets from real scores: 124,082 match. Results are real outcomes against real lines. Favourites covered 48–49% this season and CFB overs 55.5%. |

**The decisive statistic (production, 3 seeds):** EV at decision, at the market makers' no-vig fair, is **−4.3% for
every recreational group, with an SD of only ~1 point**. Every customer is buying essentially the same price. With no
information about outcomes, every group's win rate is ~50% and its expected ROI ≈ −vig. Realised differences
between groups are luck.

**Modelling defects vs market efficiency**
- **Market efficiency (not a defect):** preferences for favourites, overs or popular teams carry almost no
  information against a closing-quality line. Even well-modelled human biases should win ~48–51% before juice.
- **Defects:**
  1. no pre-kickoff facts (`NoFacts`);
  2. no price awareness, so nobody's price differs from anyone else's;
  3. mis-specified sharps.

## 2. What was changed (research configuration V4b2; `lab_v4/behavior_v2.py`, hooks in `lab_v4/runner.py`)
All parameters are a-priori behavioural assumptions, never fitted to results or NEED. No customer ever sees the
result of the game it bets on.

1. **Real pre-kickoff facts** (`RealFacts`): each team's last-3 wins, margin, streak and scoring, from finals of
   games that kicked off ≥ 4 h earlier. This switches on the recency and narrative weights production already has.
2. **Persistent individual decision traits:** discipline, price tolerance and sensitivity, impulse-bet probability,
   loyalty strength, and narrative over-reaction, drawn from each customer's id.
3. **Price awareness:** each recreational bet is checked against the market makers' no-vig fair price at the same
   minute (it is on any screen).
   - Acceptance falls off below an individual tolerance.
   - **Selective** customers use a hard EV floor (−1% to −3%).
   - **Team-loyal** fans never price-check their own team.
4. **Group mechanisms:**
   - **High-stakes:** favourite +0.3 and over +0.2 logit, twice the national-TV pull, and occasional oversized
     bets of 2–4× (still within limit and budget).
   - **Narrative chasers:** 1.5× (lognormal) recency over-reaction, plus a totals lean that follows the two teams'
     recent scoring.
   - **Underdog contrarians:** participation rises with the size of the underdog.
   - **Team-loyal:** individual loyalty strength and a persistent home preference.
   - **Sharps:** V4r1 EV rule (+EV vs market-maker fair at the same minute).
5. **Max-win limit** (V4r1, standard risk practice).

**Stake concentration (Part 3), deliberately NOT capped:**
- High-stakes customers (716 who bet) produce 56.5% of handle with a median stake of $1,153, only 2.8% of
  bankroll (99th percentile $9,300; limit $10,000).
- The concentration comes from bankroll size, not reckless staking.
- It is plausible (VIP-heavy handle is how real books describe themselves) but cannot be calibrated without real
  handle data.
- A cap chosen to stabilise NEED would be the arbitrary tuning the brief forbids. The max-win limit is the only
  exposure rule added.
- **Liability effects:**
  - top-1 wager median share of game-market handle: 22% (A) → 25% (20k) → 32% (10k) → 36% (5k);
  - NEED survives removing the top-5 wagers in 63% (A) / 72% (20k) / 68% (10k) / 65% (5k) of game-markets.

## 3. Before vs after (3 seeds each; spreads and totals; same 335-game universe: NFL wk 1–9, CFB Top-25 wk 0–10)
| Group | A: win% / ROI with vig / EV at decision | V4b2 20k: win% / ROI / EV at decision | What actually differs now |
|---|---|---|---|
| High-stakes rec | 49.6 / −6.4% / −4.37% | 49.5 / −6.5% / −4.44% | favourites 58→68%, overs 55→60% |
| Casual rec | 50.0 / −4.2% / −4.37% | 50.3 / −3.7% / −4.38% | uses recent form |
| Narrative chaser | 50.1 / −4.0% / −4.36% | 50.3 / −3.8% / −4.47% | favourites 57→66% (recent winners are usually favourites) |
| Team-loyal | 49.7 / −5.5% / −4.34% | 49.4 / −5.3% / −4.44% | own team at any price |
| Underdog contrarian | 49.9 / −3.6% / −4.32% | 50.1 / −3.8% / −4.29% | fewer small-dog bets (16.2k vs 19.4k) |
| Selective price-sensitive | 52.0 / −3.5% / −3.85% (876 bets) | 53.5 / +4.7% / **−1.44%** (157 bets) | genuinely selective: pays ~1.4% instead of ~4.4% |
| Sharp | 51.3 / −1.9% / −2.51% (7,277) | 59.4 / +24% / **+4.10%** (441) | genuinely +EV, rare |

**Interpretation**
- Behaviour is now visibly different by group: favourite and over shares, selectivity, price paid, timing on
  form. But **recreational win rates stay ~49–51%**. That is the honest result: biases about favourites, overs,
  form and loyalty carry no information against an efficient line.
- The **price paid** is what separates groups (selective −1.4%, sharp +4.1%, everyone else ~−4.4%). For
  recreational groups the spread of prices is narrow (SD 1.4 pts) because the synthetic book quotes one
  retail-median price to everyone.
- The sharps' +24% ROI is +4.1% EV plus luck on 441 bets. It comes from catching the book's 10-minute-stale
  re-quote, not from football information.

**Other measures**
| Measure | A | 20k | 10k | 5k |
|---|---|---|---|---|
| Median bettor ROI (≥ 10 bets); p10 / p90 | −6.6%; −44% / +32% | −6.4%; −47% / +34% | −5.8% | −6.3% |
| Top 1% of bettors' handle share | 34.4% | 39.2% | 39.4% | 39.8% |
| Bets per active customer-week | 2.20 | 2.12 | 2.12 | 2.10 |
| Natural handle (spreads + totals, per seed) | $13.6–14.1M | $12.5–13.0M | $6.1–6.3M | $3.1–3.3M |
| Build time per seed (4 parallel workers) | ~5–7 min (earlier) | ~2.5 min | ~1.5 min | ~1 min |

## 4. 20k vs 10k vs 5k (same revised behaviour; fixed subsets of the same customer ids; handle normalised ×2 / ×4)
| | 20k | 10k | 5k | A (production, 20k) |
|---|---|---|---|---|
| NEED same side in all 3 seeds | **43.9%** | 38.9% | 34.9% | 37.1% |
| NEED survives removing the largest wager | **72.7%** | 68.6% | 66.5% | 69.2% |
| NEED survives removing the 5 largest | **72.5%** | 67.6% | 64.5% | 63.3% |
| Largest wager, median share of game-market handle | 25% | 32% | 36% | 22% |

**20k is best.** Smaller populations make every game-market more dependent on a handful of bets and make NEED
less stable. They save only ~1–1.5 minutes of compute per season replay. There is no reason to reduce.

## 5. Financial NEED: more stable, not predictive (EXPLORATORY: behaviour designed after these games were studied)
NEED side from wagers placed by kickoff − 1 h, graded at the best **real** executable price at that time (each
game-market once per seed).

| Config | Seed | NFL spread | NFL total | CFB spread | CFB total | All: ROI [95% CI]; CLV vs consensus close |
|---|---|---|---|---|---|---|
| A | 0/1/2 | 72-59-5, 71-60-5, 68-63-5 | 61-73-2, 76-60, 70-66 | 84-88-2, 87-85-2, 84-86-4 | 102-91-1, 94-99-1, 96-96-2 | −1.2% [−9, +7], +1.1%, −1.4%; CLV −1.85 to −1.89% |
| 20k | 0/1/2 | 69-60-7, 66-65-5, 67-63-6 | 65-70-1, 73-62-1, 66-70 | 84-88-2, 87-84-3, 92-79-3 | 105-88-1, 92-101-1, 91-102-1 | +0.2% [−7, +8], −1.5%, −2.2%; CLV −1.80 to −1.86% |
| 10k | 0/1/2 | 67-63-6, 66-65-5, 70-60-6 | 69-66-1, 70-65-1, 63-73 | 83-89-2, 87-85-2, 83-87-4 | 108-86, 88-105-1, 101-92-1 | +1.1%, −3.8%, −1.7%; CLV −1.84 to −1.97% |
| 5k | 0/1/2 | 72-60-4, 62-67-7, 67-63-6 | 71-64-1, 63-72-1, 60-75-1 | 76-95-3, 84-87-3, 86-85-3 | 102-90-2, 101-91-2, 105-88-1 | −0.6%, −3.6%, −1.4%; CLV −1.83 to −1.95% |

- **Independent games:** NFL 136, CFB ~170–194 graded (Top-25 universe).
- **CLV vs the market makers' close:** −1.6% to −1.9% in every configuration and seed, i.e. the vig. Records
  change sign between seeds of the same model.
- **The "robust to top-5" subset** is no better (20k: 0.0%, −4.2%, −4.1%).

**Financial NEED carries no information beyond the market under either bettor model.** The revised bettors are more
differentiated and NEED is somewhat more stable at 20k, but that stability is not predictive.

## 6. Verdict
**ABANDON SYNTHETIC-BETTOR NEED AS A PREDICTIVE SIGNAL.** For sportsbook simulation only: **REPLACE BEHAVIOURAL
RULES AFTER VALIDATION** with V4b2 at 20,000 customers.
- **Do NOT reduce the population.**
- **Keep Financial NEED on the dashboard as a liability display only, never as a pick.**
- V4b2 fixes real defects: no facts, no price awareness, −EV sharps, unlimited long-shot wins. It does not and
  cannot create predictive NEED: the synthetic customers have no information the market lacks.
- No further bettor-model revisions are recommended without a new information source, such as real
  ticket/handle splits.

## 7. V4 integration
- V4b2 is a set of hooks around the existing engine functions (`_decide`, `_recreational_pick`) plus one facts
  source. A production port would be a new engine release (sportsbook_v5.r3):
  1. replace `NoFacts()` with `RealFacts` built from the `v4-results` finals the site build already downloads;
  2. add the acceptance gate and individual traits in `_decide`;
  3. adopt the EV sharp rule and max-win limit.
- The website, ledgers and settlement stay as they are; a new weekly run starts under the new engine version
  (older runs immutable).
- Needs your approval; not done.

## 8. Files
- `lab_v4/behavior_v2.py`, `lab_v4/overhaul_runs.py`, `lab_v4/overhaul_report.py`.
- Edits (research hooks, production path unchanged): `lab_v4/runner.py`, `season.py`, `ledger.py`, `config.py`.
- `tests_lab_v4/test_behavior_v2.py`.
- `docs_v4/bettor_overhaul_report.json.gz` (all numbers).
