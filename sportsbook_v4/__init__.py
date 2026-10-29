"""
sportsbook_v4 -- ArbitrEdge V4 synthetic sportsbook (DEVELOPMENT ONLY).

Event-sourced, time-aware synthetic sportsbook:

    market opens -> bettors arrive over the week (deterministic Poisson
    clock) -> each arrival sees ONLY the market as of its own timestamp ->
    accepted wagers are appended, immutably, at the line/price offered at
    that moment -> exposure is computed from the accepted wagers themselves.

ISOLATION (milestone 1)
-----------------------
* Nothing in this package is imported by production code (web/, book_need/,
  operations/). It is not wired into run_slate_book_need.
* V4 writes only to its OWN database (a separate SQLite file supplied by the
  caller). Market data is read through a MarketSource adapter; the
  snapshot adapter opens the market database read-only (`mode=ro`).
* The simulation core imports nothing from book_need, behavioral, web or
  operations (enforced by tests_v4/test_isolation.py).
"""

# The ACTIVE engine. Runs created under any other engine version are kept, immutable, and reported as
# superseded; only runs of the active engine are advanced. Rollback to m4 = set this back to
# "sportsbook_v4.m4" (m4 runs then resume, catching up deterministically).
ENGINE_VERSION = "sportsbook_v5.r1"   # V5 r1: individual customers, weekly participation, bankroll-driven stakes
M4_ENGINE_VERSION = "sportsbook_v4.m4"   # m4: coherent side-neutral offer; book moves on respected action
