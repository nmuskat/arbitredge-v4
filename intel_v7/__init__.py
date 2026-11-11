"""
intel_v7 -- ArbitrEdge V7: professional sportsbook market intelligence (DEVELOPMENT).

A separate ANALYTICAL layer over the real collected odds. It never reads or writes the synthetic sportsbook, its
bettors, ledger, settlement or Financial NEED, and it makes no API calls. It infers which side PROFESSIONAL pricing
(Pinnacle; the BetOnline/LowVig operator group counted once) appears to favour relative to the retail books, from
observable prices only. Real sportsbook liability is never observed: every card says ACTUAL BOOK LIABILITY: UNKNOWN.
"""

VERSION = "intel_v7.0"
LABEL = ("REAL MARKET PRICES, MODELED INFERENCE: professional pricing lean inferred from observed odds; actual "
         "sportsbook liability is UNKNOWN; not a betting recommendation")
