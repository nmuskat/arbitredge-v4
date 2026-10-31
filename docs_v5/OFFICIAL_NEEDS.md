# Official ArbitrEdge Needs (EXPERIMENTAL, rules `official_needs.v1`)

Versioned research thresholds applied to the MODELED V5 synthetic sportsbook. **Not proven profitable and not an
official play under rebuild plan §G** (CLAUDE.md rule 6); the site's default decision stays NO BET.

## Rules (fixed; hash pinned in tests; a change needs `official_needs.v2`)

| Rule | Passes when |
|---|---|
| R1 Stable Need | the spread Book Need side is the same under the baseline and all 4 sensitivity assumptions |
| R2 Top handle | the game's spread handle ranks in the top 25% of the week's games (rank <= ceil(n/4)) |
| R3 Recreational opposes | >= 65% of recreational spread handle AND > 50% of recreational spread tickets are against the Need |
| R4 Sharp supports | >= 60% of sharp spread handle is on the Need side; >= 3 sharp wagers, >= 2 sharp customers, no sharp customer > 70% of sharp handle, sharp handle >= 5% of the game |
| R5 Top swing | the book's spread P&L swing ranks in the top 25% of the week's directional games |
| R6 Valid data | directional Need, priced offer, odds observed <= 3 h before the ledger time, ledger <= 3 h behind the build, within 24 h of kickoff, >= 100 distinct spread bettors, no bettor > 15% of the game's spread handle |

OFFICIAL = all six. WATCHLIST = R1 and R6 plus at least three of R2-R5. PASS = anything else. Games more than 24 h away
show a provisional indication (EARLY) and are never frozen.

## Freezing, sealing, grading
- Every build inside the 24 h window writes an immutable, hash-verified record (`official_snap_<sport>_<UTC>.json.gz` in
  the weekly `v4-need-YYYY-Www` release): rule results with values, pick (side, line, price, book, offer), supporting
  data, ledger and build times, engine version, run id, rules version and hash. Only builds at or after
  `REGISTERED_AT` (2026-10-31T04:00Z) and before kickoff can write one: never retroactive.
- After kickoff (+20 min) the next build seals the LAST record taken and built before kickoff
  (`official_dec_<UTC>.json.gz` in `v4-need-decisions`). A game with no record gets no selection.
- Graded ATS at the frozen line and price, 1 unit per selection (win pays the American price, push 0); Official and
  Watchlist summarised separately, by sport; never mixed with the Need Tracker or C1-C4.

## Isolation
New package `official_v5/`; the build calls it after Book Need is computed and reads the same values. The engine,
Book Need, sharp vs public, the Need tracker, C1-C4, settlement and the ledger are unchanged: a build of this branch and
of the live code on identical inputs give byte-identical site data, ledger wagers and Need/settlement files except the
new Official Needs fields (the only ledger difference is each interval's wall-clock `committed_at`).
