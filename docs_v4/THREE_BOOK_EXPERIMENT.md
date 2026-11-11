# Three-sportsbook "bet with the book" experiment (research branch `research/three-book`; EXPLORATORY)

- **Thresholds and categories:** fixed in `lab_v4/three_book.py`, committed (c1d2cef, 2026-11-11 20:49 UTC)
  before any result was computed.
- **Populations:** `lab_v4/behavior_v2.PROFILES`.
- **Full numbers:** `docs_v4/three_book_results.json.gz`.
- **Scope:** production, ledgers, SIMS, collection and frozen records were untouched; 0 API credits.

## Setup
- **Universe:** every completed NFL game (136) and CFB AP Top-25 game (199; 194 graded) in the replay universe,
  through 2026-11-10.
- **Markets:** spreads and totals, with real collected prices.
- **Seeds:** 3 per configuration.
- **Bettors:** 20,000 customers in each book (10,000 per sport).
- **Decision:** the book's NEED from wagers accepted by kickoff − 1 h, bet at the best **real** executable price
  at that time.
- **Independence:** one bet per game-market per configuration (side = majority of the 3 seeds). CIs bootstrap
  whole games.
- **Multiple testing:** 47 strategy tests; Holm and BH-10% applied.
- **Control:** the production engine, unchanged. A/B/C = V4b2 plus fixed profile multipliers.

## Results
See the session report; every figure is in the JSON. **No strategy passed Holm or BH.** Every strategy's CLV is
−1.5% to −2.4%, the same as betting the market (−1.85%).
