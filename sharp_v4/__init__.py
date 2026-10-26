"""
sharp_v4 -- Engine B: sharp-money evidence from REAL recorded market quotes.

Inputs are market_v4 quote rows whose source is a real provider. Rows from
the synthetic sportsbook (Engine A) are rejected: simulated sharp bettors
are never evidence of real professional action.
"""

SHARP_VERSION = "sharp_v4.0.1-experimental"
