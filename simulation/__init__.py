"""
ArbitrEdge V3 Phase 1 -- Synthetic Bettor Simulation (persistent identity).

This package is structurally isolated from every behavioral/book_need
evidence stream it is meant to independently corroborate. See
bet_selection.py's module docstring and FORBIDDEN_GAME_FACT_KEYS for the
enforced boundary, and tests/test_anti_circularity_simulation.py for the
AST-based import guard.

Feature-flagged, OFF by default. See book_need/synthetic_bettors_v2.py's
SYNTHETIC_BETTOR_ENGINE environment variable.
"""
