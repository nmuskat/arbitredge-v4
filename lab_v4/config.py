"""
lab_v4/config.py -- the five research configurations. Every value was fixed BEFORE any configuration was run and
none is fitted to results. Re-used from need_v6 (tested there) where noted.
"""
from need_v6.config import CONFIG as V6

PRICE_SENSITIVITY = {           # B: need_v6.customers acceptance rule (tested in tests_need_v6)
    "price_tolerance": V6["price_tolerance"],       # 0.030
    "bias_weight": V6["bias_weight"],               # 0.10
    "price_beta": V6["price_beta"],                 # per archetype
}

RISK = {                        # C: need_v6 limits and risk controls (tested in tests_need_v6)
    "wager_limit": V6["wager_limit"],               # per wager by sport/market (moneyline uses the spread limit)
    "kickoff_ramp": V6["kickoff_ramp"],             # limits rise toward kickoff
    "respected_limit_factor": V6["respected_limit_factor"],
    "game_limit": V6["game_limit"],                 # max worst-case loss per game-market
    "shade_step": V6["shade_step"],                 # juice shade on the heavy side per breach (no-vig prob)
    "max_shade": V6["max_shade"],
    "close_factor": 1.25,                           # close the heavy side beyond 1.25 x the game limit
    "reopen_factor": 0.8,                           # reopen once worst case is back within 0.8 x the limit
}

MARGIN = {                      # D: theoretical two-way overround on every offer, line unchanged
    "overround": 0.035,         # vs ~0.045-0.048 retail (-110/-110 = 0.0476); ~ -108/-108
}

CONFIGS = {
    "A": {"label": "Original V4 (production engine, unchanged)", "price": False, "risk": False, "margin": False},
    "B": {"label": "V4 + recreational price sensitivity", "price": True, "risk": False, "margin": False},
    "C": {"label": "V4 + customer limits and risk management", "price": False, "risk": True, "margin": False},
    "D": {"label": "V4 + reduced margin (3.5% overround)", "price": False, "risk": False, "margin": True},
    "E": {"label": "V4 + B + C + D", "price": True, "risk": True, "margin": True},
    # ---- realism audit corrections (v4r1); each switch is a separate, classified change
    "R_SHARP": {"label": "V4 + sharp EV rule (customer behaviour correction)", "price": False, "risk": False,
                "margin": False, "sharp_ev": True},
    "R_MAXWIN": {"label": "V4 + max-win limit (risk management improvement)", "price": False, "risk": False,
                 "margin": False, "max_win": True},
    "R": {"label": "V4r1 = V4 + sharp EV rule + max-win limit", "price": False, "risk": False, "margin": False,
          "sharp_ev": True, "max_win": True},
    # ---- bettor behaviour overhaul (V4b2; research only; see lab_v4/behavior_v2.py)
    "B2_20K": {"label": "V4b2 revised behaviour, 20,000 customers", "price": False, "risk": False, "margin": False,
               "sharp_ev": True, "max_win": True, "v2": True, "pop_frac": 1.0},
    "B2_10K": {"label": "V4b2 revised behaviour, 10,000 customers (fixed subset)", "price": False, "risk": False,
               "margin": False, "sharp_ev": True, "max_win": True, "v2": True, "pop_frac": 0.5},
    "B2_5K": {"label": "V4b2 revised behaviour, 5,000 customers (fixed subset)", "price": False, "risk": False,
              "margin": False, "sharp_ev": True, "max_win": True, "v2": True, "pop_frac": 0.25},
    # ---- three-book experiment (research only): three alternative 20,000-customer populations on V4b2
    "TB_PUBLIC": {"label": "Public-heavy book", "price": False, "risk": False, "margin": False, "sharp_ev": True,
                  "max_win": True, "v2": True, "pop_frac": 1.0, "profile": "public"},
    "TB_EMOTION": {"label": "Emotion-driven book", "price": False, "risk": False, "margin": False, "sharp_ev": True,
                   "max_win": True, "v2": True, "pop_frac": 1.0, "profile": "emotion"},
    "TB_PRICE": {"label": "Price-aware book", "price": False, "risk": False, "margin": False, "sharp_ev": True,
                 "max_win": True, "v2": True, "pop_frac": 1.0, "profile": "price"},
}
SEEDS = (0, 1, 2)
HORIZONS_H = (48, 24, 6, 1)
MATERIAL = {"abs": V6["materiality_abs"], "share": V6["materiality_share"]}   # 5,000 or 5% of handle (a priori, v6)
