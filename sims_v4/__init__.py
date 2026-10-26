"""
sims_v4 -- Engine C: football prediction and score simulation.

Point-in-time team strength (margin-aware Elo + opponent-blind EPA
efficiency), linear margin/total models refit walk-forward, and discrete
score-margin / total distributions with empirically learned key-number
mass. Every prediction states the data cutoff it used.
"""

SIMS_VERSION = "sims_v4.nfl.0.1"
