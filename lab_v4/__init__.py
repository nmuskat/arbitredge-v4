"""
lab_v4 -- CONTROLLED NEED experiments on the production V4 synthetic sportsbook (engine sportsbook_v5.r2).  RESEARCH.

Isolated: never writes to any ledger, release or production file. Every configuration calls the UNCHANGED production
engine functions (sportsbook_v4.engine) through an in-memory copy of engine.advance's loop, with optional hooks.
Configuration A (no hooks) is tested to reproduce the production engine's wagers exactly. All randomness uses the
production engine's common-random-number key, so configurations differ only by the change under study; seeds > 0
re-key every random draw to measure simulation randomness.
"""
VERSION = "lab_v4.0"
