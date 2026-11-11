# Validation-first research: Priority 1 evidence and proposed desk specification (2026-11-11)

Research only. Production is unchanged (`v4-dev` 8c003b8). The frozen TNF pilot is untouched. No API credits were
used.

## Priority 1: recovered evidence

### 1. Genuine pre-kickoff Financial NEED (production Need tracker, releases v4-need-2026-W44…W46)
- **Records:** for each game, the last record before kickoff, engine sportsbook_v5.r2 (13 records from the older m4
  engine are excluded, not pooled).
- **Market:** spread NEED only (the tracker records spreads).
- **Graded at the book's recorded offer** (a real retail book's line and price at record time) and at Pinnacle's own
  price at the same snapshot.
- **Market expectation:** Pinnacle's no-vig fair at the last snapshot before kickoff.
- **Independence:** one row per game, so rows are independent.

| | NFL | CFB |
|---|---|---|
| Games (all with final scores) | 28 | 35 |
| NEED side, at the book's recorded price | **10-17-1**, ROI −29.3% (95% CI −63.5% to +4.9%) | **20-15**, +9.3% (CI −22.5% to +41.2%) |
| At Pinnacle's price | 11-17, −23.7% (CI −59.5% to +12.1%) | 19-15 (34 with a Pinnacle quote), +7.8% (CI −24.9% to +40.5%) |
| CLV vs Pinnacle close (book / Pinnacle price) | −4.0% / −2.4% | −4.4% / −2.9% |
| Wins minus market expectation, z (book price) | −1.36 | +0.69 |
| "Qualified" subset (tracker's own rule) | 0 games | 1 game, 0-1 |
| Weekly (ISO week) | W44 5-6, W45 3-11-1, W46 2-0 | W44 9-9, W45 11-6 |
| Tracker stability label | 18 FRAGILE, 10 STABLE | 11 FRAGILE, 24 STABLE |

**Reading:** CLV is about −vig in both sports. Both records are within noise (|z| < 1.4) and point in opposite
directions. **No edge can be inferred**; the samples are far too small.

### 2. Original SIMS forecasts: PENDING owner action
The SIMS project's ledger shows pregame forecasts created before kickoff for **NFL week 7 (14 games), week 8
(14), week 9 (15) and CFB week 8 (17)**, each with a model version and payload hash. The ledger stores hashes
only; the probabilities are in the SIMS project's own database.

A read-only export request was sent to the SIMS session:
- export every pregame forecast;
- verify sha256(payload) against the ledger;
- push one file to a new branch, without touching master.

**The SIMS session is holding the export until the owner replies there** ("yes, push it" or "yes, send the file
only"). Items 3–5 of Priority 1 (overlap of NEED and SIMS, three-way comparison, calibration) wait for that file.

Known in advance:
- genuine NEED records start on 29 Oct, so the expected overlap is only the **NFL week 8–9** games with both
  records (at most about 28 games);
- there is no CFB overlap (SIMS CFB forecasts are week 8 only; NEED CFB records cover weeks 9–10).

## Priority 2: proposed deterministic trading desk, DESK-v1 (for approval; not implemented, not tested)

**Principle.** The baseline is the market. The desk may only move off the market by an amount fitted on **2025**
data (predicting margins and totals, never ROI). 2026 is never used to choose anything. If 2025 says football
information adds nothing beyond the closing line, the desk will PASS almost always. That is an acceptable, honest
outcome.

### Inputs (all point-in-time)
- **Decision time:** T = kickoff − 90 min.
- **Market baseline:** the latest collected snapshot at or before T, no older than 6 h. Pinnacle's no-vig fair home
  margin μ_mkt and total τ_mkt, from the push- and key-number-aware normaliser (NFL key-number distribution; CFB
  normal, σ 15.7 margin / 15.4 total). No Pinnacle quote → BetOnline group; neither → **PASS (no market)**.
- **Football data:** play-by-play of games that **kicked off before T** in the current season, plus the previous
  season for priors. Run and pass plays only. Garbage time excluded: win probability < 5% or > 95%, as given in the
  data.
- **Personnel:**
  - QB1 = the highest-ranked QB on the timestamped depth chart at or before T.
  - If QB1 differs from the QB with the most dropbacks in the team's previous game → **PASS (QB change)**. No
    adjustment is attempted, since none is validated.
  - Injury reports carry no publication timestamps, so they are **excluded** from strict tests.
- **Excluded:** weather, news, SIMS, Financial NEED.

### Ratings (per sport, refit at each decision time)
1. **Opponent-adjusted efficiency.** Ridge regression of play EPA on offence team, defence team and a home
   indicator: EPA = a_off(o) − d_def(d) + h + ε.
   - Ridge penalty λ toward a prior.
   - Prior mean = last season's final rating × ρ.
   - λ and ρ are chosen by **2025 leave-one-week-out prediction of game margins** (grid fixed in advance:
     λ ∈ {25, 50, 100, 200, 400} plays, ρ ∈ {0, 0.3, 0.5}).
2. **Pace.** Expected plays per team = mean plays per game of both teams' prior games, shrunk 50/50 to the league
   mean.
3. **Football-only margin.** μ_fb = plays × [(a_off(H) − d_def(A)) − (a_off(A) − d_def(H))] + HFA. HFA is fitted on
   2025 (one constant per sport); 0 at neutral sites.
4. **Football-only total.** τ_fb = league-average points per play (prior games) × plays × (1 + the combined
   offensive/defensive efficiency terms), with the scale fitted on 2025.

### Combination with the market (the only learned weights)
- μ_desk = μ_mkt + w_s · (μ_fb − μ_mkt); τ_desk = τ_mkt + w_t · (τ_fb − τ_mkt).
- **w_s and w_t:** least-squares regression on **2025** of (final margin − closing μ) on (μ_fb − closing μ),
  through the origin, per sport, clipped to [0, 1].
- Their 95% CIs are reported. **If the 2025 CI for w includes 0, w is set to 0** and that market is NO POSITION
  for the season.
- *Choosing w by 2025 predictive fit, not ROI, is the test of "does football information add anything beyond the
  market".*

### Probabilities and calibration
- Cover and over probabilities come from the same normaliser, evaluated at μ_desk / τ_desk for each book's own
  line.
- Calibration is checked on 2025 (reliability bins, Brier vs market). There is **no recalibration on 2026 data.**

### Decision (per market)
**LEAN** only if all of the following hold:
1. w > 0 for that sport and market (from the 2025 rule above);
2. |μ_desk − μ_mkt| ≥ 1.0 pt (spread) or |τ_desk − τ_mkt| ≥ 1.5 pt (total);
3. the best observed executable price in the decision snapshot has EV ≥ +2.0% at the desk probability;
4. both teams have ≥ 3 current-season games;
5. no QB-change PASS.

**Outputs:** HOME / AWAY / OVER / UNDER LEAN, or **NO POSITION** with the failing condition named. The thresholds
above are fixed a priori and **never changed after any result**.

### Freezing and evaluation order
1. On approval: implement, unit-test, then freeze the specification text, the code commit and a sha256 checksum.
   Only after that run 2–4.
2. **2025 diagnostics (exploratory):**
   - NFL: ATS and totals accuracy at nflverse **closing** lines; hypothetical ROI at the recorded closing prices,
     labelled not executable, with no CLV.
   - CFB: ATS at closing lines only; no prices exist.
3. **2026 diagnostics (exploratory, in-sample label):** quotes at or before T only; ROI at the best observed
   executable price; CLV vs Pinnacle close; wins vs market expectation; game-clustered CIs; NFL and CFB separate;
   week by week.
4. **Prospective:** every game after the freeze (first slate: NFL week 11 / CFB week 12). A pre-kickoff record of
   every LEAN and PASS, written once (the same immutable-record pattern as the Need tracker).
   - Primary metrics: CLV vs Pinnacle close and wins vs market expectation.
   - Secondary: ROI.
   - The desk, Financial NEED, SIMS (once exports flow) and two predefined combinations (desk ∧ SIMS agree;
     desk ∧ NEED agree) are each compared with the market baseline.
   - Benjamini–Hochberg across strategies.
   - **No claim of profitability from agreement alone.**

### Known limits of DESK-v1
- Unadjusted for injuries (no timestamps), weather and news.
- No pressure or coverage data.
- CFB ratings use only Top-25 opponents' full schedules, from the free play-by-play.
- 2025 CFB has no prices.
- The design itself was written by someone who has seen 2026 outcomes. The safeguards are: weights fitted only on
  2025, fixed thresholds, and prospective validation as the only test that counts.
