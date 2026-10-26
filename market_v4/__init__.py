"""
market_v4 -- V4 market-data layer.

Odds API payload -> provenance-tagged, append-only quote store ->
point-in-time market views -> observed closes -> data-quality monitors.

Rules: nothing is imputed. A market with no fresh quote at time T is
reported STALE/NO_DATA, never filled from later or closing prices.
"""

MARKET_VERSION = "market_v4.0.1"
