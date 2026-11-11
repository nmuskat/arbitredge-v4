"""
need_v6 -- ArbitrEdge NEED engine v6: synthetic sportsbook with a professional-style trading desk.

DEVELOPMENT VERSION. Runs ALONGSIDE the live sportsbook_v5 engine and never writes to its ledger:
  * reuses the existing synthetic customers (identities, profiles, archetypes, individual traits, arrival clocks,
    side preferences, staking), the existing settlement and Financial NEED code, and the collected odds;
  * adds a trading desk that SETS its own spread/total prices, price-sensitive recreational customers,
    sharps whose only edge is the desk's own deviation from the market (no fetch-cadence artefact),
    customer/market/game limits, and three separate NEED outputs (Financial, Market-Maker Inference,
    Trading Desk) plus one documented official ARBITREDGE NEED.
Everything is MODELED. Nothing here is real sportsbook liability or a betting recommendation.
"""

VERSION = "need_v6.1"   # 6.1: informed-position gate (audit 2026-11-11); 6.0 kept on branch dev/need-engine-v6
# Simulation seed: kept separate from VERSION so a labelling/policy release does not reshuffle simulated customers.
# Change it only for a deliberate new random draw (and say so).
SIM_SEED = "need_v6.0"
LABEL = "MODELED (synthetic sportsbook + trading desk on real odds; not real sportsbook liability; not a bet recommendation)"
