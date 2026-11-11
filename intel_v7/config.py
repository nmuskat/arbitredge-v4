"""
intel_v7/config.py -- every V7 threshold, fixed A PRIORI (before any 2026 outcome was evaluated with V7) and never
fitted to results. Sources: Tier A research definitions (0.5-pt material gap, 0.25-pt "held"), industry key numbers,
and the collection design (fetch cadence). Any change creates a new CONFIG_HASH; frozen records name the hash used.
"""
import hashlib
import json

CONFIG = {
    # ---------------------------------------------------------------- sources
    # operators: books owned by one operator count ONCE (median of the members present)
    "operators": {"pinnacle": "pinnacle", "betonlineag": "betonline_group", "lowvig": "betonline_group"},
    "pro_operators": ["pinnacle", "betonline_group"],       # market-making / low-margin professional operators
    "retail_books": ["draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "espnbet", "fanatics"],
    "min_retail_books": 3,            # retail consensus needs at least this many retail books in the same fetch

    # ---------------------------------------------------------------- freshness (observed, never invented)
    # A book is FRESH at a fetch only if it was quoted IN that fetch. Price changes are only known to have happened
    # between two fetches ("change window"); the provider's market_last_update is a refresh time, not a change time.
    "max_decision_fetch_age_s": 3 * 3600,   # the decision fetch must be this recent at decision time

    # ---------------------------------------------------------------- A. divergence
    "gap_material_pts": 0.5,          # |pro fair - retail consensus fair| (normalised points) to be material
    "gap_min_uncertainty_multiple": 2.0,   # and >= this x retail dispersion (median absolute deviation)
    "min_dispersion_pts": 0.15,       # dispersion floor

    # ---------------------------------------------------------------- B. resistance
    "resistance_window_s": 8 * 3600,  # look back this far for the reference fetch
    "retail_move_pts": 0.5,           # retail consensus moved at least this
    "pro_hold_pts": 0.25,             # pro operator moved at most this ("held")
    "min_window_fetches": 2,          # pro operator must be quoted in at least this many fetches in the window

    # ---------------------------------------------------------------- C. key numbers (NFL spreads)
    "key_numbers": [3, 7],
    "secondary_numbers": [1, 4, 6, 10, 14],
    "key_juice_shift": 0.03,          # no-vig probability shift at an unchanged key number (~10-15 cents)

    # ---------------------------------------------------------------- D. convergence (walk-forward, prior games only)
    "convergence_min_games": 30,
    "convergence_boot": 200,

    # ---------------------------------------------------------------- E. classifier / signal
    "signal_min_strength": "MODERATE",
    # evaluation and freezing
    "final_snapshot_before_kick_s": 30 * 60,
    "write_before_final_s": 3 * 3600,
}

STRENGTH_ORDER = ["NONE", "WEAK", "MODERATE", "STRONG"]
CONFIG_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:12]
