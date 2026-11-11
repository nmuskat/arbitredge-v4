"""
edge_research/plan.py -- the FIXED research plan for the comprehensive edge-discovery study.

Written and committed BEFORE any of the new hypotheses below was evaluated against outcomes, closing values or
subsequent market movement. Honesty note: the same 2026 quote store and finals were already examined by Tier A,
V7 and DESK-v1, so every result here is EXPLORATORY on development data, never independent validation.

Thresholds are taken from the earlier a-priori definitions (Tier A / V7 CONFIG: 0.5-pt material gap, 0.25-pt hold,
0.03 no-vig juice shift) or are round numbers fixed here; none is chosen from ROI.
"""
import hashlib
import json

PLAN = {
    "version": "edge_research.v1",
    # ------------------------------------------------------------------ timing (all point-in-time)
    "decision_offset_s": 60 * 60,          # T_dec = latest fetch <= kickoff - 60 min ...
    "decision_max_age_s": 3 * 3600,        # ... and no older than 3 h before that time
    "early_offset_s": 24 * 3600,           # H5b early horizon: latest fetch <= kickoff - 24 h ...
    "early_max_age_s": 12 * 3600,          # ... no older than 12 h before that time
    "ref_min_gap_s": 90 * 60,              # reference fetch for movement signals: <= T_dec - 90 min ...
    "ref_max_gap_s": 12 * 3600,            # ... and >= T_dec - 12 h
    "close_max_before_kick_s": 90 * 60,    # close = last fetch before kickoff, at most 90 min before it,
    "close_min_after_dec_s": 15 * 60,      # and at least 15 min after the decision fetch
    # ------------------------------------------------------------------ market definitions
    # professional fair = Pinnacle no-vig fair mean when quoted IN the fetch, else the BetOnline/LowVig operator
    # retail consensus = median of the fresh retail books (>= 3) ; consensus = median of all operators
    # fair means are push/key-number aware (need_v6.normalize via intel_v7.history); nothing carried forward
    "min_retail_books": 3,
    # ------------------------------------------------------------------ hypotheses (primary)
    "H1_gap_pts": 0.5,                     # A. |pro - retail| >= 0.5 at T_dec  -> pro side
    "H2_retail_move_pts": 0.5,             # A. resistance: retail moved >= 0.5 (ref -> T_dec) ...
    "H2_pro_hold_pts": 0.25,               # ... while pro moved <= 0.25 the same way (or the other way) -> against retail move
    "H3_juice_shift_p": 0.03,              # B. pro point unchanged, no-vig prob of one side up >= 0.03 (ref -> T_dec) -> that side
    "H5_min_ev": 0.0,                      # E/line shop: retail price with EV >= 0 vs pro fair at T_dec (H5a) / T-24h (H5b)
    # ------------------------------------------------------------------ H4 (C) leadership, movement only, no bets
    # consecutive-fetch pairs with pro + >= 3 retail fresh in both; regress next-interval moves on the current gap
    # ------------------------------------------------------------------ evaluation
    "primary_metric": "mean CLV of the bet = EV of the taken price at the all-operator consensus CLOSE fair mean",
    "primary_test": "one-sided H0: mean CLV <= 0; game-clustered bootstrap; Holm across the 20 primary cells "
                    "(H1, H2, H3, H5a, H5b x NFL/CFB x spread/total), family alpha 0.05",
    "secondary": ["consensus move T_dec->close toward the bet side (pts)",
                  "directional CLV: bet side minus opposite side at their best prices (removes shop/vig effects)",
                  "outcome excess: wins - closing-fair expected wins (push-aware)",
                  "ROI at the actual best available price", "split: archive era (provider historical snapshots) vs "
                  "live-collected era"],
    "min_cell_n": 30,                      # fewer qualifying game-markets -> INSUFFICIENT (reported, not tested)
    "bet_price": "best price for the bet side among FRESH retail + pro books in the decision fetch (one bet per "
                 "game-market; ties: first book alphabetically)",
    "bootstrap": 4000,
    "seed": 20261111,
    # ------------------------------------------------------------------ interactions (only if a primary cell passes Holm)
    "interactions": ["H1 x NFL spread key number (pro and retail on different sides of 3 or 7)",
                     "H5 x horizon (T-24h vs T_dec on the same games)"],
    # ------------------------------------------------------------------ not testable (declared up front)
    "not_testable": {"D_public_splits": "no real ticket/handle data exists in any project store; synthetic bettor "
                                         "percentages are not a substitute",
                     "Circa_BetCRIS": "not in the Odds API feed we collect",
                     "SIMS": "verified export not in this session; only 1 forecast in the local SIMS clone"},
}
PLAN_HASH = hashlib.sha256(json.dumps(PLAN, sort_keys=True).encode()).hexdigest()
