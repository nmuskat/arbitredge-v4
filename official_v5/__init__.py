"""
official_v5 -- OFFICIAL ARBITREDGE NEEDS (EXPERIMENTAL research classification, versioned rules).

Reads what the synthetic sportsbook already computed (Book Need from modeled P&L, Need stability across the four
sensitivity assumptions, accepted wagers, observed market odds) and classifies each game OFFICIAL / WATCHLIST / PASS.
It never changes the simulation, Book Need, sharp-vs-public or the C1-C4 cohorts. Selections are frozen prospectively
(immutable pre-kickoff records; the last one before kickoff is sealed) and never created retroactively.
"""
