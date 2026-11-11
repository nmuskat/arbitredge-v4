"""
desk_v1/spec.py -- every fixed choice of DESK-v1, approved 2026-11-11 (Option B: 2025 closing lines are a LABELLED
proxy for the missing 2025 decision-time market). Nothing here may change after the freeze; a change is DESK-v2.
"""
import hashlib
import json

SPEC = {
    "decision_minutes_before_kickoff": 90,
    "market_max_age_hours": 6,
    "market_sources": ["pinnacle", "betonline_group"],          # no-vig fair; else PASS (no market)
    # ---- plays
    "nfl_play_types": ["pass", "run"],                           # excludes kneels, spikes, two-point tries, overtime
    "cfb_play_types": ["Rush", "Rushing Touchdown", "Pass Reception", "Pass Incompletion", "Pass Completion",
                       "Passing Touchdown", "Sack", "Interception Return", "Interception Return Touchdown",
                       "Fumble Recovery (Opponent)", "Fumble Recovery (Own)", "Fumble Return Touchdown",
                       "Pass Interception Return"],
    "garbage_wp": [0.05, 0.95],                                  # excluded where pre-snap offence WP outside
    "point_in_time": "ratings for a game use only plays from EARLIER weeks of the season (+ prior season prior)",
    # ---- ratings
    "lambda_grid": [25, 50, 100, 200, 400],                      # ridge penalty, in plays
    "rho_grid": [0.0, 0.3, 0.5],                                 # prior = previous season final rating x rho
    "selection": "nested forward MSE of game margin (totals: game total) over earlier weeks; never ROI",
    "pace_shrink_to_league": 0.5,
    "points_model": "team points = alpha + beta * expected plays * expected EPA/play (OLS on forward features)",
    # ---- market combination
    "w_rule": "w = OLS through origin of (final - mu_mkt) on (model - mu_mkt), forward 2025; game bootstrap 95% CI "
              "(2000 resamples); lower bound <= 0 -> w = 0 (NO POSITION for that sport-market); else clip to <= 1",
    "w_bootstrap": 2000,
    "proxy_2025_market": "nflverse/cfbfastR CLOSING line (and NFL closing prices) -- LABELLED proxy, not T-90",
    # ---- distributions
    "nfl_distribution": "sims_v4 nfl_distribution_2026.json key-number model (2008-2025), sigma x k",
    "cfb_distribution": "discrete normal, sigma estimated on forward 2025 residuals, x k",
    "k_grid": [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25],
    "calibration": "k by maximum likelihood of forward 2025 cover/over outcomes at the proxy lines; no 2026 recalibration",
    # ---- decision
    "min_gap_points": {"spread": 1.0, "total": 1.5},
    "min_ev": 0.02,
    "min_prior_games": 3,
    "qb_rule": "NFL: depth-chart QB1 (dt <= decision time) differs from previous game's top-dropback QB -> PASS; "
               "CFB: no timestamped depth charts -> no QB rule (documented risk)",
    "excluded_inputs": ["injury reports (no publication timestamps)", "weather", "news", "Financial NEED", "SIMS"],
    "first_forward_week_2025": 4,
}
SPEC_HASH = hashlib.sha256(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()
