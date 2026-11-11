# V7 prospective evaluation specification (FROZEN)

**Frozen** at the commit that adds this file, for rules `intel_v7.0`, CONFIG_HASH **`622c296b99ce`**.
**Owner instruction (2026-11-11):** freeze a prospective evaluation specification before assessing future outcomes.

Nothing in this file may be changed after records start. A rule change means a new version (new CONFIG_HASH), whose
records are evaluated separately and never pooled with these.

## 1. What is evaluated
- **Unit:** one game-market (spread or total) of an eligible game:
  - every NFL game;
  - CFB games selected by the live site rule (AP Top-25 under the applicable poll).
- **Record:** the final pregame V7 card, built from the latest fetch at or before kickoff − 30 min. It is written
  once by the site build as `v7intel_<sport>_<event>_<market>.json.gz` in `v4-need-YYYY-Www`.
- **Counted:** only records with `written_before_final = true` (written within 3 h of kickoff). Late-written records
  are kept but excluded, and their number is reported.
- **Period:** from the first site build with `V7_INTEL=on` (after the owner's deployment approval) until
  **300 SIGNAL records** or **2027-02-15**, whichever comes first.
- **Excluded:** all 2026 data before that date. It is development data that was already examined.

## 2. Groups
1. **SIGNAL:** `signal_or_pass = SIGNAL` (PROFESSIONAL PRICING INFERENCE).
2. **OBSERVED DISAGREEMENT:** a lean with PASS.
3. **Baselines:** computed on the same decision fetches with the same execution rule:
   - SHOP: the best-EV side vs the professional fair price, every game-market.
   - MIRROR: the retail side of every material divergence.
   - BOTH: both sides of every game-market.

## 3. Measures (each at the frozen best observed executable price)
- **M1 (price discovery):** retail-consensus movement toward the side, decision fetch → last fetch before kickoff (points).
- **M2 (closing value):** EV of the frozen price at the **all-operator consensus close** (each operator once).
- **M3:** EV at the professional (Pinnacle or BetOnline-group) close. Reported, never confirmatory: it is circular for
  a Pinnacle-derived signal.
- **M4 (outcome):** W-L-P, ROI at the frozen price, and wins minus the wins expected at the professional close.

## 4. Confirmatory hypotheses (the only two)
- **H1 (price discovery):** for SIGNAL, mean M1 > 0. Game-clustered bootstrap (2,000 resamples); one-sided 95%
  lower bound > 0.
- **H2 (closing value beyond shopping):** for SIGNAL, mean M2 > 0 **and** mean M2(SIGNAL) − mean M2(SHOP) > 0.
  Both one-sided 95% lower bounds must be > 0.
- **ROI** is secondary. "Profitable" may be claimed only if H2 holds **and** the ROI 95% lower bound is > 0 with at
  least 300 graded SIGNALs.

Sport, market, strength and module breakdowns are descriptive only. No subgroup can be declared validated.

## 5. Looks and decisions
- **Interim (100 SIGNALs):** report only. No stopping, no rule changes.
- **Final:** at 300 SIGNALs or 2027-02-15.
  - Fewer than 100 SIGNALs → **INCONCLUSIVE**.
  - H1 holds → "V7 SIGNALs anticipate retail price movement (price discovery)". This is **not** a betting edge.
  - H2 also holds → "candidate betting edge, needs a second prospective season".
  - Otherwise V7 stays an **observational** tool, labelled unvalidated.
- **Missing data:**
  - no close within 3 h before kickoff → excluded and counted;
  - no final score → pending, never graded by assumption;
  - a CFB game without a matchable score stays unmatched.

## 6. What this cannot show
- **Real sportsbook liability is never observed.** No outcome of this test can confirm any book's NEED.
- **Collection cadence (median ~41 min in the last 6 h, ~76 min overall) limits timing.** Moves inside a fetch
  interval are unknown.
- **Sample size is likely small.** Development data gave about 13 NFL SIGNALs in ~10 weeks (CFB Top-25: more). 300
  SIGNALs may not be reached this season; INCONCLUSIVE is a likely, acceptable outcome.

Spec text SHA-256 prefix (sections 1-6, excluding this line): `57f73895e5ea0539` (also recorded in `docs_v4/V7_MARKET_INTEL.md`).
