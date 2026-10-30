"""
sportsbook_v4/customers_v5.py -- V5 individual customers (engine releases sportsbook_v5.r1, sportsbook_v5.r2).

The same 20,000 persistent customers as m4 (same identities, opening bankrolls and base traits, same
population ids). V5 adds, per customer, persistent INDIVIDUAL traits drawn from a seed of the customer's own
id, so two customers of one group differ in how often they show up, which markets they like, when in the week
they bet, how much price they accept, how much primetime pulls them and how much of their bankroll they risk.
Each week a customer is independently ACTIVE or sits the week out. Stakes and weekly budgets scale with the
customer's CURRENT bankroll (frozen at the week's open from settled results); a customer whose bankroll is
below the minimum stake is out until it recovers.

Nothing here targets a ticket share, handle share or Need. Every number in V5_PARAMS is an UNVALIDATED
assumption (docs_v5/V5_RELEASE.md lists them); a change requires a new engine release (each release's parameter
hash is pinned by a test). Releases are kept side by side so an older release replays exactly:
  r1  individual traits, weekly participation, bankroll-driven stakes
  r2  r1 + participation-neutral volume (being active changes WHICH weeks a customer bets, not how much over a
      season: when active, arrivals = the customer's long-run weekly rate / P(active)) + disciplined sharp staking
      (quarter-Kelly: at the sharps' own minimum edge, 1.5% at -110, full Kelly is about 3.2% of bankroll; a
      quarter is about 0.8%, i.e. 0.4 x the 2% average unit; stakes still scale with the edge)
"""

import math
import random

import sportsbook_v4 as _pkg
from sportsbook_v4.params import ARCHETYPES, BOOK, TIMING_PROFILES
from sportsbook_v4.util import json_hash, seed_int

V5_PARAMS_R1 = {
    # weekly engagement ~ Beta(a, b) per group; P(active in a week) = floor + span x engagement
    "engagement_beta": {"REC_CASUAL": [2.0, 3.0], "TEAM_LOYAL": [2.5, 2.0], "UNDERDOG_CONTRARIAN": [2.0, 2.5],
                        "NARRATIVE_CHASER": [3.0, 2.0], "HIGH_STAKES_REC": [3.0, 2.5],
                        "SELECTIVE_PRICE_SENSITIVE": [3.0, 2.0], "SHARP": [6.0, 1.5]},
    "active_floor": 0.10, "active_span": 0.85,
    "market_mix_concentration": 12.0,       # Dirichlet around the group's market mix (lower = more individual)
    "timing_steady_blend": [2.0, 5.0],      # Beta: share of a customer's timing taken from the 'steady' profile
    "price_tolerance_jitter": 0.015,        # +- on the group's maximum implied probability
    "primetime_log_sd": 0.5,                # lognormal multiplier on the group's primetime / national-TV pull
    "proximity_log_sd": 0.35,               # lognormal multiplier on how far ahead of kickoff they look
    "risk_log_sd": 0.35,                    # lognormal multiplier on the weekly share of bankroll risked
    "risk_bounds": [0.05, 0.90],
    "bust_multiple_of_min_wager": 2.0,      # below this bankroll a customer cannot bet
}
V5_PARAMS_R2 = dict(V5_PARAMS_R1, **{
    "volume": "participation_neutral",      # active-week arrivals = long-run weekly rate / P(active)
    "sharp_unit_scale": 0.4,                # quarter-Kelly unit for sharps (see module docstring)
})
V5_RELEASES = {"sportsbook_v5.r1": V5_PARAMS_R1, "sportsbook_v5.r2": V5_PARAMS_R2}
V5_PARAMS = V5_RELEASES.get(_pkg.ENGINE_VERSION, V5_PARAMS_R2)      # the active release's parameters
V5_PARAMS_HASH = json_hash(V5_PARAMS)
MARKETS = ("spread", "total", "moneyline")


def _dirichlet(rng, means, conc):
    draws = [rng.gammavariate(max(1e-6, m * conc), 1.0) for m in means]
    s = sum(draws)
    return [d / s for d in draws]


def individual(customer):
    """Persistent individual traits of one customer (deterministic in the customer's id)."""
    arch = ARCHETYPES[customer["archetype"]]
    rng = random.Random(seed_int("v5_individual", customer["bettor_uid"]))
    a, b = V5_PARAMS["engagement_beta"][customer["archetype"]]
    eng = rng.betavariate(a, b)
    mw = [arch["market_weights"][m] for m in MARKETS]
    tot = sum(mw)
    mix = _dirichlet(rng, [x / tot for x in mw], V5_PARAMS["market_mix_concentration"])
    j = V5_PARAMS["price_tolerance_jitter"]
    lo, hi = V5_PARAMS["risk_bounds"]
    return {
        "engagement": eng,
        "p_active": V5_PARAMS["active_floor"] + V5_PARAMS["active_span"] * eng,
        "market_weights": dict(zip(MARKETS, mix)),
        # quantised to quarters: 5 timing shapes per group keep the arrival envelope cheap to compute
        "steady_blend": round(4 * rng.betavariate(*V5_PARAMS["timing_steady_blend"])) / 4,
        "max_implied_prob": min(0.75, max(0.505, arch["max_implied_prob"] + rng.uniform(-j, j))),
        "primetime_mult": math.exp(rng.gauss(0.0, V5_PARAMS["primetime_log_sd"])),
        "proximity_mult": math.exp(rng.gauss(0.0, V5_PARAMS["proximity_log_sd"])),
        "weekly_risk_fraction": min(hi, max(lo, arch["weekly_risk_fraction"]
                                            * math.exp(rng.gauss(0.0, V5_PARAMS["risk_log_sd"])))),
    }


def active_this_week(customer, ind, week_start_epoch):
    """Independent weekly participation draw (same customer and week -> same answer)."""
    u = random.Random(seed_int("v5_active", customer["bettor_uid"], int(week_start_epoch))).random()
    return u < ind["p_active"]


def effective(customer, bankroll, week_start_epoch):
    """(customer view with the current bankroll, the customer's own behaviour parameters, active?)."""
    ind = individual(customer)
    arch = dict(ARCHETYPES[customer["archetype"]])
    arch["market_weights"] = ind["market_weights"]
    arch["max_implied_prob"] = ind["max_implied_prob"]
    arch["primetime_weight"] = arch["primetime_weight"] * ind["primetime_mult"]
    arch["national_tv_weight"] = arch["national_tv_weight"] * ind["primetime_mult"]
    arch["kickoff_proximity_hours"] = arch["kickoff_proximity_hours"] * ind["proximity_mult"]
    arch["weekly_risk_fraction"] = ind["weekly_risk_fraction"]
    arch["timing_key"] = (arch["timing"], ind["steady_blend"])
    busted = bankroll < BOOK["min_wager"] * V5_PARAMS["bust_multiple_of_min_wager"]
    active = (not busted) and active_this_week(customer, ind, week_start_epoch)
    traits = dict(customer["traits"])
    if V5_PARAMS.get("volume") == "participation_neutral":
        traits["arrivals_per_week"] = traits["arrivals_per_week"] / ind["p_active"]
    if arch["is_sharp"] and "sharp_unit_scale" in V5_PARAMS:
        traits["unit_fraction"] = traits["unit_fraction"] * V5_PARAMS["sharp_unit_scale"]
    return dict(customer, bankroll=bankroll, traits=traits), arch, active, {"busted": busted, **ind}


def blended_profile(name, steady_share):
    base, steady = TIMING_PROFILES[name], TIMING_PROFILES["steady"]
    wk = [(1 - steady_share) * x + steady_share * y for x, y in zip(base["weekday"], steady["weekday"])]
    hours = []
    for (a0, a1, w0), (b0, b1, w1) in zip(base["hours"], steady["hours"]):
        assert (a0, a1) == (b0, b1)
        hours.append((a0, a1, (1 - steady_share) * w0 + steady_share * w1))
    return {"weekday": wk, "hours": hours}
