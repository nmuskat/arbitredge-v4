# Original ArbitrEdge Market Intelligence in V4: spreads + totals (research branch; NOT deployed)

**Branches:**
- `research/mi-integration`: built on production `v4-dev` 55e162a; the website integration.
- `research/original-intel`: the 2026 audit code (`original_v3/`) and the results.

**Status:** nothing is live. The tab and the recorder are both behind owner switches that are OFF.

## 1. Spread engine (original, unchanged)
- **Code:** `book_need/*` + `behavioral/*` + `features/*` from the untouched baseline archive. It runs unchanged
  through `market_intel/compat.py`, which lays V4's collected data out in the original database tables.
- **Components (all as originally written):**
  - Side Preference V1 / NFL V2;
  - Handle Intensity V1 / V2;
  - Public Pressure;
  - per-book S/J regimes (confirmation, divergence, resistance, partial divergence, no movement);
  - cross-book vote;
  - key numbers;
  - Book NEED and strength;
  - data quality and staleness.
- **Totals:** the original has none.
- **Absent from the repository:** the original `db/` package and collector config (inert stand-ins for 3 imported
  names), and the TV-network/record context (V4 never collected it; the original drops those inputs by design).

## 2. Totals engine `mi_totals.v1` (new; rules frozen in commit 6535d3d before any result)
- **A. Estimated public totals preference** (estimate only), four slate-z-scored components, equal-weighted (the
  Side Preference V1 procedure):
  - the two teams' recent combined scoring;
  - that scoring minus the current consensus total;
  - the two teams' recent over rate vs their own closing totals;
  - public attention (Handle Intensity V1 percentile).

  Clear only above the slate median (the original rule). With 2–3 inputs it is REDUCED INPUT; with fewer than 2 it
  abstains. It is not a default to over: CFB T−1h picks were 41 over / 40 under.
- **B. Totals Handle Intensity:** Handle Intensity V2's five market-activity measures on totals (line changes, O/U
  juice changes, share of books moving, last-hour acceleration, coordinated moves), as a slate percentile. It is not
  money.
- **C. Response per book:** the total moved toward/against the public lean, and juice worse/better for that side,
  using the original regime table and vote. NFL key totals are {41, 44, 47}. Pro-vs-recreational sequencing is shown
  as evidence only, never used in the decision.
- **D. NEED:** the side opposite the estimated public lean, with the original strength table, modifiers and
  activity cap. Otherwise PASS or INSUFFICIENT, always with a written reason.

## 3. 2026 audit (completed games; spreads 136 NFL / 685 CFB; best real price; EXPLORATORY)
**Original freshness rule:**

| Market | Cutoff | Insufficient | NEED picks | Record | ROI (95% CI) | CLV | $ at $100 |
|---|---|---|---|---|---|---|---|
| NFL spread | T−6h | 49% | 10 | 6-3-1 | +27.8% (−30, +78) | −1.70% | +$278 |
| NFL spread | T−1h | 6% | 15 | 11-3-1 | +51.3% (+6, +86) | −1.39% | +$769 |
| CFB spread | T−6h | 43% | 62 | 31-26-1 | +6.2% (−20, +32) | −0.19% | +$360 |
| CFB spread | T−1h | 18% | 95 | 50-36-2 | +14.3% (−6, +35) | −1.20% | +$1,259 |
| NFL total | T−6h | 49% | 4 | 2-2 | −4.3% | −0.34% | −$17 |
| NFL total | T−1h | 6% | 12 | 11-1 | +77.5% (+44, +95) | −1.96% | +$930 |
| CFB total | T−6h | 43% | 54 | 25-29 | −10.9% (−36, +14) | −1.67% | −$589 |
| CFB total | T−1h | 18% | 81 | 35-45-1 | −14.6% (−35, +5) | −2.33% | −$1,183 |

**V4-compatible freshness rule (adapter variant):**

| Market | T−6h | T−1h |
|---|---|---|
| NFL spread | 8-7-1, +4.6% | 9-6-1, +16.5% |
| CFB spread | 45-36-1, +8.5% | 49-43-1, +4.8% |
| NFL total | 4-5, −13.2% | 13-4, +48.0% |
| CFB total | 36-42, −10.9% | 34-50-1, **−21.0%** |

- **Multiple testing:** none of the tests with ≥ 20 bets passes Holm.
- **T−24h is unavailable by the original's design:** evaluation starts at T−24h.

## 4. Dashboard
- **Tab:** a new "Market Intelligence" tab (only when `MARKET_INTEL=on`). Each game shows two independent panels,
  Spread and Totals:
  - estimated public side or lean;
  - market activity;
  - per-book response;
  - key numbers / totals path;
  - market-derived NEED and strength;
  - written reason;
  - freshness.
- **Labelling:** labelled separately from Financial NEED and SIMS.
- **Screenshots:** `docs_v4/mi_nfl.png`, `mi_cfb.png`, `mi_mobile.png` (offline build from production odds as of
  2026-11-07 22:30 UTC).
- **Cost:** the step takes about 21 s per build.

## 5. Prospective records (built, DISABLED)
- **Code:** `market_intel/record.py`, enabled only by `MARKET_INTEL_RECORD=on`.
- **Records:** one write-once record per game × market × cutoff (T−6h, T−1h), PASS included.
  - Evaluated as of the cutoff, never later.
  - Each holds every book's quotes at the cutoff, the best price for the NEED side, the evidence and a checksum.
  - Saved to release `v4-market-intel`, separate from CF1/PX1.

## 6. Activation (owner approval required; nothing done)
1. Merge `research/mi-integration` into `v4-dev`, after a review and the full test run.
2. Set repository variable `MARKET_INTEL=on`: the tab appears on the next build.
3. Optionally set `MARKET_INTEL_RECORD=on`: records start at the next T−6h/T−1h cutoffs.
4. Rollback: unset the variables (instant); records stay immutable.

## 7. Synthetic sharp agreement (`market_intel/sharp.py`; thresholds frozen in 73e280d before any outcome)
- **Inputs:** read-only SHARP wagers from the synthetic book. Only wagers accepted by the cutoff count, and only
  when the book has been simulated through min(cutoff, latest market data). Otherwise the status is
  INSUFFICIENT HISTORICAL SHARP DATA.
- **Classification thresholds:**
  - **Sparse:** fewer than 3 wagers or fewer than 2 bettors.
  - **STRONG:** ≥ 65% of handle, ≥ 60% of tickets, and still ≥ 55% of handle without the largest wager.
  - **LEAN:** ≥ 55% of handle with no opposite ticket majority.
  - **Conflict:** a ticket majority against the handle majority counts as no consensus.
- **Categories:** strong/moderate agreement, disagreement, no sharp consensus, NEED only, sharp only, no signal.
- **Labelling:** always shown as SIMULATED, never as real money.
- **Records:** format v2 adds the sharp position, the agreement class, input versions and thresholds.
- **Grading:** writes separate `graded_*` files and never edits a record.
