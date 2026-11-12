# Original ArbitrEdge market intelligence: reconstruction and 2026 audit (research; nothing deployed)

**Branch:** `research/original-intel`.
**Code:** `original_v3/adapter.py`, `original_v3/replay.py`, `original_v3/evaluate.py`.
**Data:** V4 collected odds + final scores, read-only.
**Results:** all EXPLORATORY; the 2026 season has already been studied.

## A. What the original system was (from the unmodified baseline archive, commit 0cf5495)
| Component | File | What it actually computes | Status in the original |
|---|---|---|---|
| Side Preference V1 | `behavioral/side_preference_v1.py` | Signed home/away lean. Equal-weighted, slate-z-scored sum of 6 differentials: wins last 3, margin last 3, win/loss streak, last-game blowout, rest days, AP-ranked. Divided by the number of available components. | "EXPERIMENTAL / UNCALIBRATED". No real ticket data. Favourite enrichment written but OFF. |
| Side Preference NFL V2 | `behavioral/side_preference_nfl_v2.py` | Direction = the consensus favourite; V1 as secondary. Disagreement → "even". | Experimental |
| Handle Intensity V1 | `behavioral/handle_intensity_v1.py` | Direction-neutral attention score: standalone slot, competing games, ranked teams, TV network. Slate percentile. | Experimental; does not measure handle |
| Handle Intensity V2 (NFL) / CFB V2 | `behavioral/handle_intensity_v2.py`, `_cfb_v2.py` | Market activity over the prior 6 h: spread and juice changes per book, share of books moving, last-hour acceleration, coordinated 10-min moves | Experimental, "for validation only" |
| Public Pressure | `book_need/public_pressure.py` | Bundle: Side Preference direction + magnitude percentile (vs slate / prior NFL games), Handle Intensity percentile | Estimate only |
| Market Response regime | `book_need/regime.py`, `spread.py`, `juice.py` | Per book over the segment: S = spread with/against pressure, J = juice worse/better for the pressured side. Table: S+1 CONFIRMATION, S−1 DIVERGENCE, S0/J+1 RESISTANCE, S0/J−1 PARTIAL_DIVERGENCE, else NO_MOVEMENT | Locked spec |
| Cross-book aggregation | `book_need/aggregation.py` | Unanimous / majority / MIXED vote of books (no book weighting) | Locked spec |
| Key numbers | `book_need/key_numbers.py` | AT / CROSSED / STOPPED-AT / TOWARD key number (3, 7, …) | Locked spec |
| **Book NEED + strength** | `book_need/need_strength.py`, `inference.py` | Needs clear pressure (SP magnitude > 50th pct). NEED = the side **opposite** the estimated public. Strength: RESISTANCE/DIVERGENCE → STRONG, PARTIAL → MODERATE, CONFIRMATION → LEAN. Downgrades: majority-only, contradiction, resistance without key number. Capped at LEAN when Handle Intensity ≤ 50th pct. | Locked spec, **spreads only (no totals NEED exists)** |
| Segments | `book_need/segments.py` | A: T−24h → T−6h, B: T−6h → T−1h, C: T−1h → kickoff. The latest segment governs. Staleness vs the V3 cadence tiers (≤ 20 min in the final hour, ≤ 60 min at 1–6 h). | Locked spec |
| Dashboard | `web/app.py` (Flask) | V3-only web app | Not used by V4 |

**Signals were generated as follows:**
1. Estimate the public side from team-form/ranking proxies, with no real ticket or handle data.
2. Watch how each book's spread and juice responded during the segment.
3. Infer that the book "needs" the other side.

None of it was ever validated on outcomes in the original project.

**Missing from this repository (not recreated):**
- the original `db/` package and `collectors/config.py` (API settings; only inert stand-ins for 3 imported names);
- TV network and team-record context (V4 never collected it; those components drop out by the original's own
  coverage rule);
- the original V3 database itself (off-limits);
- V4's ~40-minute cadence is slower than V3's 20-minute final-hour tier. The audit is therefore run twice:
  - with the original staleness rule unchanged ("v3");
  - with the tolerance widened to V4's cadence, labelled as an adapter variant ("v4").

## B. Coverage (2026 through 2026-11-10)
- **Universe:** every completed game in the V4 store, 136 NFL and 685 CFB (all CFB games with a final score, not
  only Top-25), at T−6h and T−1h. 1,642 inferences, 0 errors.
- **T−24h is unavailable by design:** segment A starts at T−24h, so the original returns NOT_YET_EVALUABLE there.
- **Book NEED signals (original cadence):**

  | | T−6h | T−1h |
  |---|---|---|
  | NFL | 10 | 15 |
  | CFB | 62 | 95 |

  Most games are NO_CLEAR_NEED (no clear pressure, or MIXED books) or INSUFFICIENT_EVIDENCE (stale or missing books).

## C. Signal performance
Spreads; one bet per game per test; best real price at the decision time; ROI with 95% CI; closing value vs the
consensus close. Original cadence; the V4-cadence variant is in brackets.

| Signal | Market / time | Bets | Record | ROI (95% CI) | CLV | V4-cadence variant |
|---|---|---|---|---|---|---|
| **DIVERGENCE regime → NEED** | CFB T−6h | 28 | 21-7 | +45.8% (+11, +74), p 0.005 | **+1.18%** | 27-13, +30.8%, CLV +0.26% |
| DIVERGENCE regime → NEED | CFB T−1h | 39 | 26-13 | +30.5% (0, +60), p 0.023 | −0.74% | 21-21, −2.4% |
| Book NEED, all | CFB T−1h | 88 | 50-36-2 | +14.3% (−6, +35), p 0.08 | −1.20% | 49-43-1, +4.8% |
| Book NEED, all | CFB T−6h | 58 | 31-26-1 | +6.2% | −0.19% | +8.5% |
| Book NEED, all | NFL T−1h | 15 | 11-3-1 | +51.3% | −1.39% | 9-6-1, +16.5% |
| Book NEED MODERATE | CFB T−1h | 21 | 15-6 | +39.7% | −1.04% | +11.5% |
| Book NEED LEAN | CFB T−1h | 59 | 31-26-2 | +7.4% | −1.56% | +7.9% |
| CONFIRMATION regime → NEED | CFB T−6h | 24 | 6-17-1 | −46.2% | −1.50% | −30.8% |
| RESISTANCE + key number | all | 1–5 per cell | — | not testable | — | — |
| Fade Side Preference (any magnitude) | CFB T−1h | 521 | 280-231-10 | +7.2% (−1, +15), p 0.04 | −1.37% | +6.8% |
| Fade Side Preference | NFL T−1h | 103 | 47-51-5 | −5.2% | −1.40% | −7.6% |
| Fade clear Public Pressure (> 50th pct) | CFB T−1h | 254 | 134-117-3 | +4.8% | −1.24% | +3.3% |
| Same, Handle Intensity ≤ 50 / > 50 | CFB T−1h | 100 / 154 | 57-42 / 77-75 | +13.0% / −0.5% | −1.46 / −1.10% | — |
| Opposite of Book NEED (control) | CFB T−1h | 88 | 37-51 | −18.0% | −1.78% | −7.7% |

**Multiple testing:** 24 tests with ≥ 20 bets. **None passes Holm**; the smallest p is 0.005 against a threshold
of 0.002.
- **Original cadence vs V4 cadence:** the two disagree materially at T−1h; the DIVERGENCE T−1h result disappears
  with the V4 tolerance. The T−6h DIVERGENCE result survives in both.
- **Not in the original (tested elsewhere; not re-counted):** pro- vs recreational-book disagreement, reverse
  line movement on real splits, price shading. See V7 / Tier A / edge study: no edge. The original's DIVERGENCE
  is RLM against *estimated* (not real) public.

## D. Original vs V4 (independence checks, CFB T−1h, original cadence)
| Book NEED subset | Bets | Record | ROI |
|---|---|---|---|
| CF1 line-follow on the SAME side | 27 | 19-7-1 | +42.2% |
| CF1 on the OPPOSITE side | 23 | 10-13 | −13.6% |
| No CF1 signal | 38 | 21-16-1 | +11.4% |
| DIVERGENCE and CF1 agree | 19 | 15-4 | +54.7% (CLV −0.08%) |
| DIVERGENCE without CF1 agreement | 20 | 11-9 | +7.5% |
| V4 synthetic Financial NEED on the SAME side | 28 | 13-15 | **−8.6%** |
| V4 synthetic Financial NEED OPPOSITE | 16 | 10-5-1 | +28.5% |

At T−6h the pattern is the same: CF1 same side 15-3-1 (CLV +2.21%), CF1 opposite 5-12, synthetic NEED same 3-8.

- **The original's profitable part is the same information as CF1:** the line moved, and the bet goes with the
  move. DIVERGENCE means the book moved the line against the estimated public, i.e. toward the NEED side. When the
  original disagrees with CF1, it loses.
- **The synthetic Financial NEED adds nothing.** Agreeing with it made results worse.
- **SIMS:** the verified pregame export is still not available in this session (not tested).
- **V4b2:** not separately compared; its NEED was non-predictive in the overhaul study.

## E. Best research lead
- **CFB line movement** remains the lead, now seen two ways: CF1, and the original's DIVERGENCE regime, which is
  largely a subset of it.
- The only original-specific candidate is **"DIVERGENCE at T−6h"**: 28 bets, positive CLV in both cadences. It
  is small, overlaps CF1, and does not survive multiple testing.
- **Recommendation:** no new tracker. CF1 (live since 2026-11-12) already records the underlying signal.
  Optionally log the original's regime per CF1 record (no new bet rule), to see whether DIVERGENCE + CF1 beats CF1
  alone prospectively.

## F. Integration into the V4 website (plan only; not done)
V4 is not the Flask/SQLite server of V3. It is a static single page built by `web_v4/live_site.py` in the GitHub
Actions `site-build` job, from the odds shards, deployed to owner-only Cloudflare Pages. The faithful integration:

1. **Market-intelligence step in site-build** (flag-guarded): rebuild the store from shards, run
   `original_v3.adapter.build` into a temporary V3-layout SQLite, and call `book_need.inference.compute_inference`
   for each upcoming game at the build time. No API calls; the same data as now.
2. **New "Market Intelligence" tab on the existing page**, clearly labelled "MARKET-DERIVED NEED (inferred, not
   real liability)":
   - estimated public side;
   - Handle Intensity percentile;
   - per-book regime;
   - key-number state;
   - NEED side / strength / data quality.

   It sits beside the existing Synthetic Sportsbook (Financial NEED), SIMS and a Signal Validation tab (CF1/PX1
   records and grades).
3. **Write-once pre-kickoff records** (release `v4-market-intel`), graded like CF1, so the original has a genuine
   prospective record.
4. **Preserved untouched:** 20,000 bettors, ledgers, settlements, Financial NEED, SIMS, existing tabs.
5. **Rollout order:** research branch → flag off in production → owner turns the flag on.

**Estimated build cost:** about 3.5 minutes of CPU for a full season; one slate at build time is seconds.

## G. Verdict
1. **Profitable-looking 2026 patterns?** Yes, in CFB:
   - original Book NEED 50-36-2 at T−1h (+14%);
   - DIVERGENCE 21-7 at T−6h;
   - NFL 11-3-1 on 15 bets.
2. **Statistically credible after correction?** No. None of 24 tests passes Holm, and T−1h results change with
   the cadence assumption.
3. **Information V4 lacks?** Mostly no. The working part is line movement, which V4 already records via CF1. The
   behavioural "public side" estimate adds little on its own: fading it is +7% in CFB, −5% in NFL.
4. **Features worth restoring (as a display, not a pick):** the regime and key-number response view, and Handle
   Intensity V2's market-activity measures. These are useful descriptions of how books moved. Side Preference
   should be shown only as an estimate.
5. **Primary analytical component of V4?** No. It is an interpretable market view, not a demonstrated edge.
6. **Safest path:** a flag-guarded Market Intelligence tab plus write-once records, as in F. Production stays
   unchanged until you approve the flag.
