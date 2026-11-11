# Edge-discovery study: fixed research plan (written before evaluation)

Branch `research/edge-discovery`. The plan constants are in `edge_research/plan.py` (`PLAN_HASH`). This file and
`plan.py` are committed **before** any new hypothesis is run against closes, movement or outcomes. The commit
time is the timestamp.

## Honesty statement
- The 2026 quote store and finals were already examined by Tier A (`research/tier-a`), V7 (`dev/v7-market-intel`)
  and DESK-v1. Those studies' headline outcomes are known to the author.
- So every backtest here is **EXPLORATORY**. Freezing code now does not make it independent. Only the
  prospective test in the final report can validate anything.

## Decision time and data
- **Decision fetch T_dec:** the latest fetch ≤ kickoff − 60 min, and no older than 3 h before that time.
- **Reference fetch** (for movement signals): within [T_dec − 12 h, T_dec − 90 min].
- **Close:** the last fetch before kickoff, at most 90 min before kickoff and at least 15 min after T_dec.
- **Fresh quotes only:** a book counts only if it was quoted in that fetch. Nothing is carried forward. Quotes
  fetched after kickoff are never used.
- **Fair lines:**
  - every two-way quote → no-vig, push-aware fair mean (NFL key numbers; CFB plain), via `need_v6.normalize`;
  - professional = Pinnacle, else the BetOnline/LowVig operator;
  - retail = median of ≥ 3 fresh retail books;
  - consensus = median of all operators.
- **Two provenance eras, reported separately:**
  - archive: provider historical snapshots, retrieved 2026-11-10, CFB AP Top-25 only;
  - live: collected live from 2026-10-26.

## Primary hypotheses (each × NFL/CFB × spread/total = 20 cells)
| ID | Family | Qualifies at T_dec when | Bet |
|---|---|---|---|
| H1 | A, pro disagreement | \|pro − retail\| ≥ 0.5 pt | the side the pro fair rates higher |
| H2 | A, pro resistance | ref → T_dec: retail moved ≥ 0.5 pt while pro moved ≤ 0.25 pt the same way (or the other way) | against the retail move |
| H3 | B, price pressure | pro point unchanged ref → T_dec, but the no-vig probability of one side rose ≥ 0.03 | that side |
| H5a | line shop at T_dec | some book's price has EV ≥ 0 vs the pro fair | that side, at that price (highest EV) |
| H5b | early line shop at T − 24 h | as H5a, at the latest fetch ≤ kickoff − 24 h | as H5a |
| H4 | C, leadership (movement only, no bets) | consecutive fetches with pro + ≥ 3 retail | — |

- **H4 regressions:** Δretail(next interval) and Δpro(next interval) on the gap (pro − retail), by sport and market;
  game-clustered CIs.
- **Bet price:** the best fresh price for the bet side in the decision fetch. One bet per game-market.

## Evaluation
- **Primary metric:** mean CLV = EV of the taken price evaluated at the all-operator consensus close fair mean.
  - One-sided test H0: mean CLV ≤ 0, game-clustered bootstrap (4,000 resamples).
  - Holm correction across the 20 cells, family α = 0.05.
  - A cell with fewer than 30 bets is INSUFFICIENT.
- **Secondary metrics:**
  - consensus move toward the bet side;
  - directional CLV (bet side − opposite side);
  - outcome excess vs closing-fair expectation;
  - ROI at the actual price;
  - archive vs live era.
- **Interactions** run only if a primary cell passes Holm:
  - H1 × NFL key number;
  - H5 × horizon.

## Declared NOT TESTABLE
- **D, public splits:** no real ticket/handle data exists in the project.
- **Circa and BetCRIS:** not in our feed.
- **SIMS:** the verified export is not in this session.
- **Financial NEED:** already graded on genuine records (no edge). It is reported, not re-mined.
