# Engine C (NFL): walk-forward validation report

Model `sims_v4.nfl.0.1`. Data: nflverse 2006–2026 (see DATA_SOURCES.md). Each season S is predicted by a model fit only on seasons before S, using features built only from earlier dates. Full numbers are in `engine_c_eval.json`.

## Design

- **Features (point in time):** margin-aware Elo; EWMA of offensive and defensive EPA/play, success rate and points; rest difference; home field; starting-QB change.
  - The QB flag uses the actual starter, which assumes the starter was known before kickoff.
  - Weather is excluded (observed values would be look-ahead).
- **Mean:** ridge regression for margin and total.
  - *Independent* variant: no market input.
  - *Anchored* variant: the listed line plus features.
- **Distribution:** discrete normal × key-number weights, calibrated by iterative proportional fitting on training games.
  - The result is spikes at 3, 7, 10, 6 and 14, and a tie rate of 0.3%.
  - Predicted spread pushes out of sample: 90 vs 93 actual (anchored model).
- **Uncertainty:** parameter standard error plus an explicit τ = 1.5-point model-uncertainty term. τ is a policy assumption and has not been validated.

## Results: out of sample, 2012–2025, about 3,800 games (lower log loss is better)

| Market | Independent model | Anchored model | Market (de-vigged) | Coin flip |
|---|---|---|---|---|
| Moneyline win prob, log loss | 0.6268 | 0.6113 | **0.6092** | 0.6931 |
| Spread cover \| no push, log loss | 0.7082 | 0.6946 | **0.6936** | 0.6931 |
| Total over \| no push, log loss | 0.7057 | 0.6937 | **0.6932** | 0.6931 |
| Margin RMSE (points) | 13.22 | 12.96 | **12.93** | — |

- **The independent model is significantly worse than the market** on all three markets: the 95% CI of the log-loss difference excludes zero. It is also worse than a coin flip on spreads, so its disagreements with the line are noise, not information.
- **The anchored model is statistically indistinguishable from the market.** The CI includes zero, and the market still scores best. In the anchored fit the market margin carries a coefficient of 0.92, and team features add nothing measurable.
- Season by season, the anchored model's spread log-loss difference stays within ±0.005 of the market every year from 2012 to 2025; no season shows a durable edge.
- **2026 to date (106 games):** the anchored model scores 0.685 vs the market's 0.693 on spreads. Over 106 games that is well within noise, and **it is not evidence of an edge.**

## Conclusions for the platform

1. **Engine C cannot claim an edge from team-strength modelling** against near-closing NFL lines. This is consistent with the literature, and the system must not issue plays from model-vs-close disagreement.
2. Engine C is still valuable as a **calibrated pricing engine**: fair lines, key-number push probabilities, half-point values, alternate lines and totals. Those let the decision layer judge whether a *specific book's price* is off the fair distribution.
3. The realistic edge hypotheses (rebuild plan §D) need data this file lacks:
   - **early-week and opening prices**;
   - **multi-book executable prices** for line shopping against the sharp consensus;
   - CLV measurement.

   All three require timestamped multi-book odds: the V3 database copy or Odds API historical snapshots. This is the first point where development genuinely needs data access beyond what the sandbox has (see the milestone note in DEVLOG.md).
