# Pre-registered paper-tracking cohorts (WATCH / paper only)

| | |
|---|---|
| Registered | 2026-10-29, effective for snapshots taken from **2026-10-29T20:00:00Z** |
| Committed | before that time; the git commit is the record |
| Code | `sportsbook_v4/cohorts.py` |
| Registry hash | `131cfb6b0819b1393e9df0163c4dfdce307b9abe0c6ef5790e1284fd4685342a` (pinned by a test) |

## Rules that make these prospective

1. **Membership is evaluated when each pre-kickoff snapshot is taken** and is stored inside that immutable, hash-verified snapshot.
   - The sealed decision is the last snapshot before kickoff.
   - Grading reads the stored membership; it is never recomputed (tested).
2. A snapshot taken before a cohort's registration time is never in that cohort.
3. **No rule is ever edited.** A changed rule is a new cohort with a new id and a new registration time, starting its own count.
4. **Grading:**
   - every pick is graded at the line and price recorded in its snapshot, against the real final score;
   - closing-line value uses the observed closing consensus (a quote no more than 45 min old at kickoff), else NOT_OBSERVED.
5. **Statistics:**
   - C1 is primary; C2–C4 are **exploratory**.
   - Each cohort's p-value (wins against the break-even of its recorded prices, one-sided exact binomial) is shown raw and **Holm-adjusted across all four**.
   - Fewer than 300 decided games is marked INSUFFICIENT SAMPLE.
6. Everything is derived from the modeled sportsbook. Need comes only from the book's P&L; nothing manufactures pressure or forces a side.

## The cohorts

All figures are the game's spread wagers in the synthetic book at the snapshot.

| | Name | Role | Rules (all required) | Pick |
|---|---|---|---|---|
| C1 | Original selective Need | PRIMARY | Need directional; top 25% weekly spread handle; ≥ 100 spread wagers; no bettor > 25% of handle; ≥ 70% of recreational handle on one side; recreational ticket majority on that side; ALIGNED | the Need side |
| C2 | High-handle Need | exploratory | Need directional; top 25% weekly spread handle; ≥ 100 spread wagers; no bettor > 25% (no pressure requirement; ALIGNED or DISAGREE) | the Need side |
| C3 | Broad recreational pressure Need | exploratory | Need directional; ≥ 100 spread wagers; no bettor > 25%; ≥ 60% recreational handle on one side; ≥ 55% recreational tickets on that side; ALIGNED (no handle-rank requirement) | the Need side |
| C4 | Recreational vs sharp disagreement | exploratory | ≥ 100 spread wagers; ≥ 60% recreational handle on one side; simulated SHARP handle ≥ 10% of the game's handle from ≥ 2 sharp bettors; ≥ 60% of sharp handle on the other side | the sharp side (the book's Need is recorded alongside) |

**Definitions:**
- **Recreational:** every archetype except SHARP.
- **Weekly handle rank:** point-in-time among the sport's weekly-run games; top 25% = rank ≤ ceil(0.25 × games).
- **ALIGNED:** the side fading the recreational majority is the side the book needs.

The cohorts overlap by design: C1 ⊂ C3, and C1 ⊂ C2 when the filter qualifies. They are compared, not added together.
