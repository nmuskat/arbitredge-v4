"""
sims_v5 -- read-only consumer of ArbitrEdge SIMS web exports (NFL only) for the V5 website.

V5 never runs simulations and never writes to SIMS. It reads versioned export files (produced by SIMS's
`arbsims web-export`), validates them, and passes whitelisted fields to the dashboard. It imports nothing from the
synthetic sportsbook (sportsbook_v4) and the sportsbook imports nothing from it (tests_v4/test_isolation.py).
"""
