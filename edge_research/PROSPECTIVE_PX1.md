# PX1: NFL Pinnacle-anchored shop (frozen prospective experiment; research only, NOT deployed)

Code: `edge_research/prospective.py` (`PX1_HASH` in the module). It is frozen at the commit that adds this file.
Any change creates a new hash and a new experiment.

## Question
When a US book's NFL point-spread price is at or better than Pinnacle's no-vig fair value, does the market move
toward that bet by kickoff by enough to beat the vig?

## Signal
| Item | Definition |
|---|---|
| Games | Every NFL regular-season and playoff game from the first decision after approval, through the 2026-27 Super Bowl |
| Market | Point spread only (totals are excluded: negative in development) |
| Arm A (primary) | Decision fetch = latest collected fetch ≤ kickoff − 24 h, no more than 12 h old |
| Arm B (secondary) | Decision fetch = latest collected fetch ≤ kickoff − 60 min, no more than 3 h old |
| Pinnacle fair | Pinnacle quoted **in that fetch**, converted to a no-vig, push- and key-number-aware fair home margin (`need_v6.normalize`). No substitute book. |
| SIGNAL | Among all other books in that fetch, the side and price with the highest EV against Pinnacle fair is ≥ 0 |
| PASS | No decision fetch in the window, Pinnacle not quoted, or no price at or above fair (reason recorded) |
| Price recorded | That book, side, point and American price, plus every fresh book's quote in the fetch |

Records are written once (`open(..., "x")`), before kickoff, each with its own checksum.

## Outcomes (graded after kickoff)
1. **Primary:** CLV against the all-operator consensus close = EV of the recorded price at the consensus fair
   mean of the last fetch before kickoff (within 90 min of kickoff). Tested one-sided against 0, bootstrapped by
   game, separately for each arm.
2. **Secondary:**
   - CLV against Pinnacle's close (partly mechanical for this signal);
   - Pinnacle's own move toward the bet;
   - consensus move toward the bet (points);
   - whether the price was still available at the same book in the next fetch;
   - result and ROI at the recorded price (reported; underpowered).

## Duration and decision rules (fixed now)
- **Expected volume:** about 3–4 SIGNALs per week per arm (33 over 9 development weeks in arm A). So about 25–35
  per arm by the Super Bowl.
- **End-of-season review** (report only, no tuning):
  - **ABANDON** if arm A's mean consensus CLV < −0.5%, or its consensus move toward the bet ≤ 0.
  - **CONTINUE into 2027** only if arm A's mean consensus CLV ≥ 0 **and** its move toward the bet has a 95% lower
    bound > 0.
  - Anything in between: INCONCLUSIVE (continue collecting; claim nothing).
- **VALIDATED EDGE** requires the lower 95% bound of arm A's consensus CLV > 0 at ≥ 150 SIGNALs, with price
  availability ≥ 50% in the next fetch.
- Outcome ROI is never the deciding metric at these sample sizes.

## What it needs (nothing new)
- The existing collected quote store, Pinnacle included, at the current balanced cadence.
- The existing NFL finals.
- No API calls, no schedule change, no new books.

## Integration path (needs owner approval later; nothing done now)
- One flag-guarded step in the existing site build (`edge_research.prospective record --now <build time>`), with
  records saved to their own release prefix (`px1_*`). This is the same pattern as the V7 and DESK-v1
  prospective records.
- Grading in the daily `odds-analyze` job.
- No change to the sportsbook, its bettors, the ledgers, Financial NEED or collection.
