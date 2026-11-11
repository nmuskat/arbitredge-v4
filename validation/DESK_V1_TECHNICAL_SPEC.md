# DESK-v1 technical specification: how the four required corrections are implemented (for approval; no code yet)

## Blocking limitation: 2025 decision-time market data does NOT exist in our data
- **Our collection holds no 2025 odds.** The live store starts on 26 Oct 2026; the backfill covers 2026 only.
- **nflverse (free):** 2025 NFL **closing** spread and total for all 285 games, with closing prices (one consensus
  source). No snapshot 90 minutes before kickoff.
- **CFB 2025 (cfbfastR play-by-play):** closing spread and over/under for 1,563 of 1,657 games. No prices, no
  decision-time quotes.
- **True T−90 prices** would require the Odds API historical endpoint (10 credits per market per snapshot; spreads +
  totals = 20 per snapshot). Estimates:
  - NFL 2025 has 139 distinct kickoff times → **≈ 2,780 credits**; weeks 9–18 only ≈ 1,400 credits.
  - CFB 2025 is several thousand credits more (many kickoff times).
  - The one-time backfill allowance has 1,800 credits left, approved for 2026 only, expiring 1 Dec. Any use for 2025
    is **your decision**; I will not spend credits.

### Proposed alternatives (choose one per sport)
- **A. NFL only, buy T−90 snapshots** (your approval and budget; ≈ 2,780 credits for the full season, or ≈ 1,400 for
  weeks 9–18). w is then trained on the true decision-time Pinnacle fair, exactly as required.
- **B. No credits: train against the 2025 closing line as a CONSERVATIVE proxy, explicitly labelled.**
  - The close contains all the information available at T−90 plus later news. So "football adds information beyond
    the close" is a *harder* test than "beyond T−90".
  - A w that passes against the close is a lower bound. A w of 0 against the close does not prove 0 at T−90, but it
    errs toward NO POSITION, never toward a manufactured edge.
  - 2026 evaluation then uses the true T−90 2026 snapshots.
- **Recommendation:**
  - **B for CFB** (no practical alternative).
  - For NFL, **B now**, with A as an optional later robustness check if you approve the credits.

## Correction 1: chronological, forward-only validation within 2025
- **Expanding window, per sport.** For each 2025 week k from 4 to the end:
  1. hyper-parameters (λ, ρ), HFA, pace shrink, σ-scale and w are fitted **only on weeks < k**;
  2. week k is predicted;
  3. the predictions are stored.
- **Nested selection.** λ and ρ are chosen within each fold by an inner forward pass (weeks < k), minimising the mean
  squared error of game margin (totals: of game total). **Never by ROI.**
- **Reported:** the forward-chained 2025 predictions only (accuracy, calibration, ATS at the close, hypothetical ROI
  at −110 / recorded closing prices). In-sample fits are never presented as validation.
- **Final freeze.** Refit once with the identical procedure on all 2025 weeks, then freeze the parameters, the code
  commit and a sha256 **before any 2026 game is scored**.
- **2026 rolling updates.** Within 2026, ratings update weekly from new play-by-play (point in time) with the frozen
  hyper-parameters. Only the data changes, never the parameters.

## Correction 2: train against the market at decision time
- **Decision time T** = kickoff − 90 min.
- **Market baseline** = Pinnacle's no-vig fair (μ_mkt, τ_mkt) from the latest snapshot at or before T, at most 6 h
  old; otherwise the BetOnline group; otherwise **PASS (no market)**.
- **Training target:** final margin − μ_mkt(T), or the closing-proxy defined in option B if 2025 T−90 data is
  unavailable, labelled as such.
- **Closing lines** are used only for CLV in 2026 evaluation.
- **2026:** only quotes with fetched_at ≤ T are visible to the model (the existing point-in-time history class).

## Correction 3: probability distributions, calibration, cover, push and EV
- **NFL margin.** The existing validated discrete distribution: sims_v4 `nfl_distribution_2026.json`, fitted on
  2008–2025 finals with key-number weights. Integer support −75..75; shifted so its mean equals μ_desk.
- **NFL total.** The same construction for totals (σ and weights from the same file).
- **CFB margin and total.** Discrete normal on integers, with σ **re-estimated on 2025 only** (residual SD of final
  minus closing line). The current 15.7 / 15.4 values came from 2026 data and are **not** used.
- **Cover, push and win.** For side s at line L and price P:
  - p_win = P(outcome beats L for s); p_push = P(outcome = L exactly) for integer L, 0 otherwise;
    p_loss = 1 − p_win − p_push.
  - The same function already used for market normalisation.
- **EV per unit staked** = p_win × payout(P) − p_loss, where payout(P) = P/100 if P > 0, else 100/|P|. A push
  returns the stake, i.e. EV contribution 0.
- **Calibration (fitted forward on 2025, frozen).**
  - One scale factor k on σ (the distribution's spread), chosen by maximum likelihood of the forward 2025 cover/over
    outcomes at the closing (or T−90) lines.
  - Reported with a reliability diagram (10 bins), Brier score and log loss vs the market-only baseline.
  - No recalibration on 2026.
- **Projected advantage → probability.** μ_desk = μ_mkt + w·(μ_fb − μ_mkt) moves the centre of the distribution. Every
  probability is evaluated at each book's own line and price, so key numbers and pushes are handled exactly.

## Correction 4: everything frozen before 2026 is evaluated
- **Plays.**
  - Included: pass (including sacks and scrambles) and run plays with non-null EPA.
  - Excluded: kneels, spikes, no-plays, two-point tries, overtime.
  - Garbage time excluded where pre-snap win probability is < 0.05 or > 0.95 (nflverse `wp`; cfbfastR `wp_before`).
- **Opponent adjustment.**
  - Ridge regression of play EPA on [offence one-hot, defence one-hot, home indicator].
  - Penalty λ toward a prior: last season's final rating × ρ.
  - Grid λ ∈ {25, 50, 100, 200, 400} (in plays), ρ ∈ {0, 0.3, 0.5}, chosen by the nested forward rule (Correction 1).
- **Pace.** Plays per game, both teams' prior games, shrunk 50/50 to the league mean.
- **Points.**
  - μ_fb = plays × net EPA difference + HFA.
  - τ_fb = league points-per-play × plays × (1 + β·combined efficiency).
  - HFA and β are fitted forward on 2025.
- **Missing-data rules → PASS:**
  - fewer than 3 current-season games for either team;
  - no market snapshot within 6 h of T;
  - no depth chart (NFL).
  - Teams new to FBS or with no prior season use ρ = 0.
- **QB-change logic (NFL).**
  - QB1 = the lowest pos_rank QB on the timestamped depth chart with dt ≤ T.
  - If QB1's gsis_id differs from the QB with the most dropbacks in the team's previous game → PASS (QB change).
  - **CFB has no timestamped depth charts:** no QB rule. This is a documented risk, and CFB results are reported
    separately.
- **Injury reports:** excluded (no publication timestamps).
- **Statistical criterion for w = 0.**
  - Per sport and market, w is estimated by least squares through the origin on the **forward-chained 2025**
    residuals (final − μ_mkt) against (μ_fb − μ_mkt).
  - 95% CI by game-level bootstrap (2,000 resamples); one row per game, so the rows are independent.
  - **If the lower bound ≤ 0 → w = 0** (that sport and market is NO POSITION for 2026). Otherwise w = the estimate,
    clipped to ≤ 1.
- **Decision thresholds (unchanged from the proposal; fixed now):** |μ_desk − μ_mkt| ≥ 1.0 pt (spread) / 1.5 pt
  (total); EV ≥ +2.0% at the best executable price in the decision snapshot.
- **Four separate models and reports:** NFL spread, NFL total, CFB spread, CFB total.
- **Excluded from the forecast:** Financial NEED and SIMS (unit-tested: the forecast function has no such inputs).
  NEED stays a liability metric.

## Evaluation order (after your approval of this spec)
1. Implement and unit-test (point-in-time filters, no NEED/SIMS inputs, EV/push maths).
2. Forward-chained 2025 diagnostics, labelled.
3. Final 2025 fit → **freeze** (spec, commit, sha256).
4. 2026 exploratory run on the original pregame snapshots: every LEAN and PASS reported, against the market-only
   baseline (accuracy, calibration, ROI, CLV).
5. Prospective records from the first slate after the freeze.
