"""
desk_v1 -- ArbitrEdge DESK-v1: a deterministic, market-anchored football trading desk.  RESEARCH (branch only).

Modular by design: pure functions over (pregame play-by-play, timestamped market snapshot, frozen parameters) ->
a forecast card with LEAN / NO POSITION. It never reads the synthetic sportsbook, its bettors, Financial NEED or
SIMS (enforced by a test), so it can later be called from the existing V4 dashboard build without touching the
sportsbook. Every setting lives in desk_v1/spec.py; fitted parameters live in a frozen JSON with a checksum.
"""
VERSION = "desk_v1.0"
