# DESK-v1 results: research branch `research/desk-v1` (2026-11-11)

Nothing is merged or deployed. Production is still `v4-dev` 8c003b8. No API credits were used, the collection
schedule is unchanged, and the frozen TNF pilot is untouched.

## Verdict
**DESK-v1 adds no measurable predictive value beyond the market, in any of the four markets.**
- The pre-specified forward 2025 test set w = 0 for NFL spreads, NFL totals, CFB spreads and CFB totals.
- DESK-v1 therefore makes **no selections**: its forecast equals the market.
- The 2026 exploratory diagnostics agree (below).

## 1. What was built (modular; no sportsbook dependency)
- **`desk_v1/spec.py`:** every fixed choice; spec sha256 `ad82a0af379ec592…`.
- **`desk_v1/data.py`:** play-by-play and games with the frozen filters (garbage time, overtime, kneels, spikes and
  two-point tries excluded). Points-in-time only: earlier weeks.
- **`desk_v1/model.py`:**
  - opponent-adjusted ridge ratings with a prior-season prior;
  - pace; a points model;
  - market anchoring μ_desk = μ_mkt + w(μ_fb − μ_mkt);
  - a discrete push-aware distribution (NFL key-number model; CFB discrete normal);
  - EV including the actual price and the push.
- **`desk_v1/fit.py`:** forward-only 2025 procedure, nested selection by forecast error, bootstrap w rule, and the
  final fit.
- **`desk_v1/frozen/`:** parameters (sha256 `7f0682a532416cf5…`), forward 2025 predictions, input-data checksums and
  `FREEZE.md`. Frozen at commit **b2675de, 2026-11-11 16:57:21 UTC, before any 2026 evaluation.**
- **Independence:** a test fails if any `desk_v1` module imports the sportsbook, Financial NEED, SIMS or market-
  intelligence code. Only the tested normaliser maths is shared.
- **Integration design (for later approval):**
  - The desk is a pure function: (pregame play-by-play, timestamped market snapshot, frozen parameters) → card.
  - A future dashboard step could call it behind a flag at kickoff − 90 min, using the build's existing market
    store and the free play-by-play.
  - It needs no sportsbook, bettor, ledger or collection change.
- **Post-freeze additions (disclosed; no frozen formula or parameter changed):**
  - `validation/desk_v1_eval2026.py` (2026 exploratory evaluation, outside the package);
  - `validation/desk_v1_prospective.py` (write-once prospective records);
  - `desk_v1/data.cfb_schedule`, a reader for the free schedule file, needed to identify upcoming CFB games.

## 2. Data issues found and fixed before the freeze
1. **Team-column comparison:** with Arrow string types, the home-team comparison failed silently. Now compared as
   strings.
2. **CFB final scores:** the score columns flip on kickoffs and scoring plays, so taking the maximum gave fake ties.
   Scores now come from the last play.
3. **2025 CFB play-by-play is incomplete for 797 of 1,657 games** (they never reach the 4th quarter, or end level).
   Those scores are treated as missing, leaving **790 usable 2025 CFB games with lines**. 2024 loses 43 games and
   2026 loses 95 under the same rule.

The first fit, run before these fixes, produced an impossible CFB w ≈ 1.04 with a CI of ±0.01. It was discarded and
never frozen, and no 2026 data had been looked at.

## 3. Forward-only 2025 (proxy market = CLOSING line; labelled; exploratory)
| Market | Forward games | Weeks with w > 0 | RMSE: market / football-only / desk | Brier at the close line: desk / market | LEANs (hypothetical, at closing prices) |
|---|---|---|---|---|---|
| NFL spread | 237 | 0 / 19 | 12.18 / 13.48 / 12.18 | 0.2486 / 0.2486 | 0 |
| NFL total | 237 | 1 / 19 | 12.94 / 13.42 / 13.05 | 0.2501 / 0.2493 | 11: 6-5, +4.5% (CI −55% to +64%) |
| CFB spread | 495 | 0 / 7 | 15.14 / 17.57 / 15.14 | 0.2500 / 0.2500 | 0 |
| CFB total | 500 | 0 / 7 | 15.45 / 16.98 / 15.45 | 0.2501 / 0.2501 | 0 |

### Final frozen fit
| Market | w estimate | 95% CI | Frozen w |
|---|---|---|---|
| NFL spread | −0.30 | −0.64 to +0.01 | **0** |
| NFL total | +0.22 | −0.28 to +0.70 | **0** |
| CFB spread | −0.05 | −0.17 to +0.07 | **0** |
| CFB total | +0.09 | −0.10 to +0.29 | **0** |

The football-only forecast is less accurate than the closing line in every market, by 0.5–2.4 points of RMSE.

## 4. 2026 exploratory (original pregame snapshots at T = kickoff − 90 min; season already studied)
| Market | Game-markets | DESK-v1 decisions | Passes | RMSE: market at T / football-only / market at close | 2026 w vs T (exploratory) | 2026 w vs close |
|---|---|---|---|---|---|---|
| NFL spread | 136 | 136 NO POSITION (w = 0) | 0 | 12.08 / 12.53 / 12.06 | +0.17 (−0.39, +0.68) | +0.14 (−0.41, +0.65) |
| NFL total | 136 | 136 NO POSITION | 0 | 13.24 / 13.86 / 13.23 | −0.12 (−0.67, +0.49) | −0.12 (−0.66, +0.47) |
| CFB spread | 199 | 162 NO POSITION | 27 no market, 10 not in play-by-play | 14.23 / 17.69 / 14.24 | +0.05 (−0.15, +0.26) | +0.06 (−0.15, +0.27) |
| CFB total | 199 | 170 NO POSITION | 6 no market, 23 not in play-by-play | 14.42 / 16.34 / 14.36 | +0.06 (−0.19, +0.32) | +0.04 (−0.20, +0.30) |

- **Selections, ROI and CLV:** DESK-v1 has 0 selections in all four markets, so ROI and CLV are not applicable. No
  unfavourable result is hidden; there are none to report.
- **Calibration:** the market-only probabilities at the market's own fair line are about 0.5 by construction (Brier
  0.25).

### The closing-proxy mismatch, measured as required
- The market moves on average only **0.29–0.34 points** between T − 90 min and the close.
- Market accuracy at T and at the close is almost identical (NFL spread RMSE 12.08 vs 12.06).
- 2026's w estimated against T and against the close are nearly the same in every market.
- The market's move from T to the close drifts toward the football-only view by a slope of only **0.00–0.02**.
- **Conclusion:** the proxy did not hide an advantage at T. But beating the close in 2025 would **not** have proven an
  advantage at T either. Here, the question doesn't arise: nothing beat the close.

### Counterfactual (NOT the desk): w = 1 with the same thresholds, at the best real T − 90 price
| Market | Bets | Record | ROI (95% CI) | Mean CLV |
|---|---|---|---|---|
| NFL spread | 71 | 35-34-2 | −0.6% (−23% to +22%) | −0.9% |
| NFL total | 62 | 30-31-1 | −3.8% (−28% to +21%) | −2.7% |
| CFB spread | 100 | 42-56-2 | −15.5% (−35% to +4%) | −2.4% |
| CFB total | 109 | 50-58-1 | −9.8% (−28% to +8%) | −2.1% |

The w = 0 rule protected against these losses. This table is shown only to document that; it is not a strategy.

## 5. Prospective records (started; write-once)
- **Location:** `pilots/desk_v1_prospective/`, **70 records** (NFL week 10: 14 games; CFB week 11 Top-25: 21 games;
  spread and total each), all recorded before kickoff.
- **Content:** all **NO POSITION (w = 0)**. Each record holds the market snapshot used, the football-only number as a
  labelled diagnostic, the spec and parameter checksums, and a record checksum.
- **Deviation:** records use the latest collected snapshot at record time (2026-11-11 13:13 UTC), not T − 90 min,
  because this research branch runs no job at T − 90 min. A pre-kickoff T − 90 run needs pipeline integration (your
  approval).
- **Correction disclosed:** 42 CFB files first written with a schedule bug (all PASS "not found") were removed
  **before any commit** and regenerated. The NFL records were unaffected.
- **Grading after the games:** each record against the market baseline (accuracy, calibration, and ROI/CLV if any
  LEAN ever occurs).

## 6. Independent components (kept separate; not inputs to the desk)
- **Financial NEED,** genuine frozen records (`validation/PRIORITY1_AND_DESK_SPEC_PROPOSAL.md`):
  - NFL 10-17-1 (−29%, CLV −4.0%);
  - CFB 20-15 (+9%, CLV −4.4%);
  - no edge.
- **SIMS:** the verified read-only export exists (226 forecasts, 226/226 hash checks per the SIMS session) but was
  delivered to you directly. **Upload it to this session** to add the SIMS comparison.

## 7. Limitations
- Training used the 2025 closing-line proxy (Option B); the mismatch is measured above.
- Incomplete 2025 CFB play-by-play.
- No injury, weather or news inputs.
- No CFB QB rule.
- k sits at the edge of its grid (not identified when w = 0; harmless).
- CFB market normalisation reuses the existing CFB width, a small effect on the fair line.
- 2026 is in-sample-exposed.

## 8. Tests
- `tests_desk_v1` (7 tests): point-in-time, independence, EV and push, w rule, ridge recovery, spec hash.
- Full required suite before each push (counts in the commit messages and the report).
