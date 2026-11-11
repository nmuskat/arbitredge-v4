"""
need_v6/config.py -- every assumption of the v6 sportsbook, in one versioned place.

None of these values was fitted to game results. Each is a documented ASSUMPTION (source in the comment) and
can be changed; any change produces a new CONFIG_HASH so frozen records always name the exact assumptions used.
"""

import hashlib
import json

CONFIG = {
    # ---------------------------------------------------------------- pricing (the desk's own book)
    "margin": 0.045,               # total overround of the desk's two-way price (= -110/-110); Circa-style 3-5% hold
    "shade_step": 0.012,           # one juice step, in no-vig probability (~5 cents at -110)
    "max_shade": 0.040,            # beyond this the desk moves the number instead of the juice
    "min_uncertainty_pts": 0.25,   # floor on fair-line uncertainty (points of implied mean)
    "key_numbers": [3, 7],         # NFL spread key numbers defended with juice (industry practice; A1 normalisation)
    "key_defend_max_mu_move": 0.40,  # defend a key number only if the normalised fair moved less than this (pts)
    "max_quote_age_s": 6 * 3600,   # market observations older than this are STALE: desk keeps its price, flags it

    # ---------------------------------------------------------------- risk limits (desk)
    "game_limit": {"nfl": 25000.0, "cfb": 15000.0},   # max worst-case loss per game-market ($, MODELED)
    "materiality_abs": 5000.0,     # a NEED is MATERIAL if swing >= max(this, share of handle)
    "materiality_share": 0.05,
    "accept_ev_to_sd": 0.20,       # ACCEPT EXPOSURE only if expected book P&L >= this x its SD (risk appetite; set a priori, not fitted)
    "respected_quiet_s": 2 * 3600, # price is "settled" when no respected (sharp) wager for this long

    # ---------------------------------------------------------------- customer limits
    # per-wager max by sport/market ($); multiplied by the kickoff ramp; respected (SHARP) customers get a lower cap
    "wager_limit": {"nfl": {"spread": 5000.0, "total": 3000.0}, "cfb": {"spread": 3000.0, "total": 2000.0}},
    "respected_limit_factor": 0.6,
    # limits rise toward kickoff (documented industry practice): factor by hours to kickoff
    "kickoff_ramp": [[72, 0.4], [24, 0.6], [6, 0.8], [0, 1.0]],

    # ---------------------------------------------------------------- price sensitivity (recreational customers)
    # A customer bets only if perceived value >= tolerance; acceptance falls exponentially with overpay:
    #   perceived = fair_prob(side, offered line) + bias_weight * (preference - 0.5) - implied(offered price)
    #   p_accept  = 1                              if perceived >= -tolerance
    #             = exp(beta * (perceived + tolerance)) otherwise
    "price_tolerance": 0.030,      # a standard -110 at the fair number (overpay ~0.024) is always acceptable
    "bias_weight": 0.10,           # how much a customer's own side preference raises its perceived value
    "price_beta": {"REC_CASUAL": 25.0, "HIGH_STAKES_REC": 30.0, "NARRATIVE_CHASER": 15.0, "TEAM_LOYAL": 8.0,
                   "UNDERDOG_CONTRARIAN": 20.0, "SELECTIVE_PRICE_SENSITIVE": 80.0},

    # ---------------------------------------------------------------- sharps
    # Sharps compare the desk's price with the market makers' no-vig fair at the SAME latest observation the desk
    # has; they never see moves between our fetches (unknown, not invented). Their only edge is the desk's own
    # deliberate deviation (shading, defending, holding exposure).
    "sharp_min_ev": 0.015,         # minimum EV (per unit staked) vs market-maker fair to bet

    # ---------------------------------------------------------------- markets
    "markets": ["spread", "total"],  # v6 scope: NEEDs for spreads and totals (moneyline customers re-weighted)
    # ---------------------------------------------------------------- official policy and independent information
    # financial_first_v2 (v6.1 default): the headline is a TRADING DESK NEED only for an INFORMED directional position,
    # i.e. one backed by an independent signal listed in validated_signals; otherwise FINANCIAL if material, else
    # NONE. desk_material_first_v1 (v6.0) is kept selectable; under it, liability tolerance was labelled a desk NEED.
    "official_policy": "financial_first_v2",
    # Independent signals that have passed PROSPECTIVE validation. Empty: the 2026 audit found none (engine C model,
    # market-maker disagreement, injuries, line moves: see docs_v4/NEED_V6_AUDIT.md). Only the owner adds one.
    "validated_signals": [],
}

CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:12]


def kickoff_factor(hours_to_kick):
    for h, f in CONFIG["kickoff_ramp"]:
        if hours_to_kick >= h:
            return f
    return CONFIG["kickoff_ramp"][-1][1]
