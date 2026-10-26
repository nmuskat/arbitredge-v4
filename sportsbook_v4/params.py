"""
sportsbook_v4/params.py -- versioned behavioural parameters.

EVERY NUMBER IN THIS FILE IS AN UNVALIDATED MODELLING ASSUMPTION.
None of it is fitted to, or derived from, observed sportsbook customer
data (ArbitrEdge has none). The values exist so the engine has a concrete,
inspectable, versioned starting point whose consequences can be measured
by sportsbook_v4/diagnostics.py. Changing any value requires a NEW
PARAMS_VERSION -- a parameter set is frozen once a population or run has
been created from it (its hash is stored on every population, run and
wager).

Design rules enforced by validate_params():
  * every directional preference is a SIGNED trait whose effect is
    antisymmetric under a home/away swap, so a perfectly symmetric game
    produces no systematic side lean unless a parameter explicitly says so;
  * the population mean of `home_pref` is 0.0 for every archetype in the
    active set (no home bias is assumed -- see V3 Defect A);
  * stake size is driven by bankroll x unit size, not by a tier lottery, so
    "high stakes" is a property of the bettor, not a coefficient sign.
"""

import copy
from contextlib import contextmanager

from sportsbook_v4.util import json_hash

PARAMS_VERSION = "v4_params_m3_unvalidated"  # m3: totals + moneyline markets, over_pref trait

# Trait spec: (mean, sd, lo, hi) -> normal draw clamped to [lo, hi].
# Bankroll: lognormal with the given median and log-sd.

TIMING_PROFILES = {
    # weights by Central-time weekday (Mon=0..Sun=6) and by hour band.
    # UNVALIDATED: shapes encode the commonly described pattern
    # "recreational volume concentrates near the weekend/kickoff,
    # respected money is earlier in the week", nothing more.
    "weekend_late": {
        "weekday": [0.30, 0.20, 0.30, 0.70, 0.60, 1.00, 1.00],
        "hours": [(0, 8, 0.10), (8, 12, 0.60), (12, 18, 1.00), (18, 24, 0.80)],
    },
    "steady": {
        "weekday": [0.70, 0.70, 0.80, 0.90, 0.80, 0.90, 0.90],
        "hours": [(0, 8, 0.15), (8, 12, 0.80), (12, 18, 1.00), (18, 24, 0.80)],
    },
    "early_week": {
        "weekday": [1.00, 0.90, 0.60, 0.45, 0.35, 0.45, 0.80],
        "hours": [(0, 8, 0.40), (8, 12, 1.00), (12, 18, 1.00), (18, 24, 0.90)],
    },
}

ARCHETYPES = {
    "REC_CASUAL": {
        "share": 0.51,
        "bankroll_median": 400.0, "bankroll_log_sd": 0.80,
        "weekly_risk_fraction": 0.50,
        "unit_fraction": (0.05, 0.02, 0.01, 0.15),
        "stake_log_sd": 0.50,
        "arrivals_per_week": (2.0, 0.9, 0.2, 6.0),
        "fav_pref": (0.50, 0.30, -0.5, 1.5),
        "home_pref": (0.0, 0.10, -0.5, 0.5),
        "popularity_weight": (1.0, 0.3, 0.0, 2.0),
        "recency_weight": (0.5, 0.3, 0.0, 1.5),
        "loyalty": 0.0,
        "max_implied_prob": 0.565,   # will lay up to about -130
        "shops": False,
        "conviction_threshold": 0.0,
        "timing": "weekend_late",
        "kickoff_proximity_hours": 36.0,
        "primetime_weight": 0.8, "national_tv_weight": 0.2,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.55, "total": 0.30, "moneyline": 0.15},
        "over_pref": (0.30, 0.30, -0.5, 1.0),
        "ml_max_implied_prob": 0.80,
        "is_sharp": False,
    },
    "TEAM_LOYAL": {
        "share": 0.12,
        "bankroll_median": 400.0, "bankroll_log_sd": 0.80,
        "weekly_risk_fraction": 0.50,
        "unit_fraction": (0.05, 0.02, 0.01, 0.15),
        "stake_log_sd": 0.50,
        "arrivals_per_week": (1.5, 0.6, 0.2, 4.0),
        "fav_pref": (0.20, 0.30, -0.5, 1.0),
        "home_pref": (0.0, 0.10, -0.5, 0.5),
        "popularity_weight": (0.5, 0.2, 0.0, 1.5),
        "recency_weight": (0.3, 0.2, 0.0, 1.0),
        "loyalty": 2.0,               # logit boost toward own team
        "own_team_game_weight": 8.0,  # game-selection boost for own team's game
        "max_implied_prob": 0.565,
        "shops": False,
        "conviction_threshold": 0.0,
        "timing": "weekend_late",
        "kickoff_proximity_hours": 48.0,
        "primetime_weight": 0.5, "national_tv_weight": 0.2,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.50, "total": 0.15, "moneyline": 0.35},
        "over_pref": (0.10, 0.30, -0.5, 1.0),
        "ml_max_implied_prob": 0.85,
        "is_sharp": False,
    },
    "UNDERDOG_CONTRARIAN": {
        "share": 0.08,
        "bankroll_median": 600.0, "bankroll_log_sd": 0.80,
        "weekly_risk_fraction": 0.40,
        "unit_fraction": (0.04, 0.02, 0.01, 0.12),
        "stake_log_sd": 0.50,
        "arrivals_per_week": (1.5, 0.6, 0.2, 4.0),
        "fav_pref": (-0.60, 0.30, -1.5, 0.3),
        "home_pref": (0.0, 0.10, -0.5, 0.5),
        "popularity_weight": (-0.3, 0.2, -1.0, 0.5),
        "recency_weight": (-0.2, 0.2, -1.0, 0.5),
        "loyalty": 0.0,
        "max_implied_prob": 0.545,
        "shops": False,
        "conviction_threshold": 0.0,
        "timing": "steady",
        "kickoff_proximity_hours": 60.0,
        "primetime_weight": 0.3, "national_tv_weight": 0.1,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.50, "total": 0.15, "moneyline": 0.35},
        "over_pref": (-0.20, 0.30, -1.0, 0.5),
        "ml_max_implied_prob": 0.60,
        "is_sharp": False,
    },
    "NARRATIVE_CHASER": {
        "share": 0.12,
        "bankroll_median": 400.0, "bankroll_log_sd": 0.80,
        "weekly_risk_fraction": 0.60,
        "unit_fraction": (0.06, 0.02, 0.01, 0.20),
        "stake_log_sd": 0.60,
        "arrivals_per_week": (2.5, 1.0, 0.3, 7.0),
        "fav_pref": (0.30, 0.30, -0.5, 1.2),
        "home_pref": (0.0, 0.10, -0.5, 0.5),
        "popularity_weight": (1.2, 0.3, 0.0, 2.5),
        "recency_weight": (1.5, 0.4, 0.3, 3.0),
        "loyalty": 0.0,
        "max_implied_prob": 0.58,
        "shops": False,
        "conviction_threshold": 0.0,
        "timing": "weekend_late",
        "kickoff_proximity_hours": 30.0,
        "primetime_weight": 1.5, "national_tv_weight": 0.4,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.50, "total": 0.35, "moneyline": 0.15},
        "over_pref": (0.40, 0.30, -0.5, 1.2),
        "ml_max_implied_prob": 0.80,
        "is_sharp": False,
    },
    "HIGH_STAKES_REC": {
        "share": 0.04,
        "bankroll_median": 40000.0, "bankroll_log_sd": 0.70,
        "weekly_risk_fraction": 0.25,
        "unit_fraction": (0.03, 0.01, 0.005, 0.08),
        "stake_log_sd": 0.45,
        # High stakes, NOT high frequency (explicit requirement).
        "arrivals_per_week": (1.5, 0.6, 0.2, 4.0),
        "fav_pref": (0.40, 0.30, -0.5, 1.2),
        "home_pref": (0.0, 0.10, -0.5, 0.5),
        "popularity_weight": (0.8, 0.3, 0.0, 2.0),
        "recency_weight": (0.5, 0.3, 0.0, 1.5),
        "loyalty": 0.0,
        "max_implied_prob": 0.565,
        "shops": False,
        "conviction_threshold": 0.0,
        "timing": "weekend_late",
        "kickoff_proximity_hours": 40.0,
        "primetime_weight": 1.0, "national_tv_weight": 0.3,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.55, "total": 0.25, "moneyline": 0.20},
        "over_pref": (0.20, 0.30, -0.5, 1.0),
        "ml_max_implied_prob": 0.85,
        "is_sharp": False,
    },
    "SELECTIVE_PRICE_SENSITIVE": {
        "share": 0.10,
        "bankroll_median": 3000.0, "bankroll_log_sd": 0.70,
        "weekly_risk_fraction": 0.20,
        "unit_fraction": (0.02, 0.008, 0.005, 0.05),
        "stake_log_sd": 0.35,
        "arrivals_per_week": (1.2, 0.5, 0.2, 3.0),
        "fav_pref": (0.0, 0.40, -1.0, 1.0),
        "home_pref": (0.0, 0.10, -0.5, 0.5),
        "popularity_weight": (0.0, 0.3, -1.0, 1.0),
        "recency_weight": (0.5, 0.4, -0.5, 1.5),
        "loyalty": 0.0,
        "max_implied_prob": 0.530,   # about -113 or better
        "shops": True,
        "shop_line_tolerance": 0.5,   # passes if any book offers >= 0.5 pt better
        "shop_prob_tolerance": 0.010, # ...or the same line >= 1 pt implied prob cheaper
        "conviction_threshold": 0.58, # passes unless P(side) >= 0.58
        "timing": "steady",
        "kickoff_proximity_hours": 96.0,
        "primetime_weight": 0.0, "national_tv_weight": 0.0,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.60, "total": 0.30, "moneyline": 0.10},
        "over_pref": (0.0, 0.30, -0.8, 0.8),
        "ml_max_implied_prob": 0.70,
        "is_sharp": False,
    },
    "SHARP": {
        "share": 0.03,
        "bankroll_median": 60000.0, "bankroll_log_sd": 0.60,
        "weekly_risk_fraction": 0.20,
        "unit_fraction": (0.02, 0.008, 0.005, 0.05),
        "stake_log_sd": 0.30,
        # Checks the board often; bets ONLY when the synthetic book's offered
        # number is worse than the reference market at that moment.
        "arrivals_per_week": (8.0, 3.0, 2.0, 20.0),
        "fav_pref": (0.0, 0.0, 0.0, 0.0),
        "home_pref": (0.0, 0.0, 0.0, 0.0),
        "popularity_weight": (0.0, 0.0, 0.0, 0.0),
        "recency_weight": (0.0, 0.0, 0.0, 0.0),
        "loyalty": 0.0,
        "max_implied_prob": 0.535,
        "shops": True,
        "edge_threshold_points": 0.5,
        "edge_threshold_prob": 0.015,
        "edge_stake_multiplier": 1.5,  # stake x (1 + mult * extra edge units)
        "conviction_threshold": 0.0,
        "timing": "early_week",
        "kickoff_proximity_hours": 400.0,
        "primetime_weight": 0.0, "national_tv_weight": 0.0,
        # UNVALIDATED: market mix and over/under lean (no observed customer data)
        "market_weights": {"spread": 0.50, "total": 0.30, "moneyline": 0.20},
        "over_pref": (0.0, 0.0, 0.0, 0.0),
        "ml_max_implied_prob": 0.75,
        "is_sharp": True,
    },
}

BOOK = {
    # UNVALIDATED book policy, not an empirical claim.
    "min_wager": 5.0,
    "max_wager_default": 10000.0,
    "max_wager_by_archetype": {"SHARP": 3000.0},
    # The synthetic book re-quotes the representative market with this lag;
    # sharps compare it with the reference market at their own decision time.
    "offer_lag_seconds": 600,
    # Reference market for sharp/shopper comparisons: lower-median across
    # these books if any quote exists at time t, else across all books.
    "reference_books": ["lowvig", "betonlineag"],
    "decision_time_granularity_seconds": 60,
    "markets": ["spread", "total", "moneyline"],
}

TEAM_POPULARITY_PRIOR = {
    # Empty == every team 1.0 (neutral). Any non-neutral table must be
    # supplied as a NEW params version and labelled with its source.
}


def _check_trait(name, spec):
    mean, sd, lo, hi = spec
    if not (lo <= mean <= hi) or sd < 0:
        raise ValueError(f"bad trait spec {name}: {spec}")


def validate_params(archetypes=None, book=None):
    archetypes = archetypes or ARCHETYPES
    book = book or BOOK
    total = sum(a["share"] for a in archetypes.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"archetype shares must sum to 1.0, got {total}")
    for name, a in archetypes.items():
        for t in ("unit_fraction", "arrivals_per_week", "fav_pref", "home_pref",
                  "popularity_weight", "recency_weight"):
            _check_trait(f"{name}.{t}", a[t])
        if a["home_pref"][0] != 0.0:
            raise ValueError(f"{name}: active params must not assume a home preference")
        if a["timing"] not in TIMING_PROFILES:
            raise ValueError(f"{name}: unknown timing profile {a['timing']}")
        if not (0.5 < a["max_implied_prob"] < 1.0):
            raise ValueError(f"{name}: max_implied_prob out of range")
        mw = a["market_weights"]
        if set(mw) != {"spread", "total", "moneyline"} or min(mw.values()) < 0 or sum(mw.values()) <= 0:
            raise ValueError(f"{name}: market_weights must cover spread/total/moneyline, non-negative")
        _check_trait(f"{name}.over_pref", a["over_pref"])
        if not (0.5 < a["ml_max_implied_prob"] < 1.0):
            raise ValueError(f"{name}: ml_max_implied_prob out of range")
    if book["min_wager"] <= 0:
        raise ValueError("min_wager must be positive")
    return True


ACTIVE_VARIANT = {"name": None, "overrides": None}


def params_snapshot():
    return {
        "params_version": PARAMS_VERSION,
        "variant": ACTIVE_VARIANT,
        "archetypes": ARCHETYPES,
        "book": BOOK,
        "timing_profiles": TIMING_PROFILES,
        "team_popularity_prior": TEAM_POPULARITY_PRIOR,
    }


def params_hash():
    return json_hash(params_snapshot())


@contextmanager
def variant(name, archetype_overrides=None, book_overrides=None, all_archetypes=None):
    """
    DIAGNOSTICS ONLY: temporarily apply a named parameter variant.

    archetype_overrides: {ARCHETYPE: {field: value}}
    all_archetypes:      {field: value} applied to every archetype first
    book_overrides:      {field: value}
    Shares are re-validated, and the variant name and overrides are part of
    params_hash() -- so any population/run created under a variant is
    distinguishable from the baseline and can never continue a baseline run.
    Restores the baseline on exit, even on error.
    """
    if ACTIVE_VARIANT["name"] is not None:
        raise RuntimeError("variants do not nest")
    saved_a, saved_b = copy.deepcopy(ARCHETYPES), copy.deepcopy(BOOK)
    try:
        for a in ARCHETYPES.values():
            a.update(copy.deepcopy(all_archetypes or {}))
        for arch, changes in (archetype_overrides or {}).items():
            if arch not in ARCHETYPES:
                raise KeyError(arch)
            ARCHETYPES[arch].update(copy.deepcopy(changes))
        BOOK.update(copy.deepcopy(book_overrides or {}))
        _validate_variant()
        ACTIVE_VARIANT.update(name=name, overrides={"all": all_archetypes, "archetypes": archetype_overrides,
                                                    "book": book_overrides})
        yield
    finally:
        ARCHETYPES.clear()
        ARCHETYPES.update(saved_a)
        BOOK.clear()
        BOOK.update(saved_b)
        ACTIVE_VARIANT.update(name=None, overrides=None)


def _validate_variant():
    """Variants may move home_pref (to measure it) but must still be well-formed."""
    total = sum(a["share"] for a in ARCHETYPES.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"variant archetype shares must sum to 1.0, got {total}")
    for name, a in ARCHETYPES.items():
        for t in ("unit_fraction", "arrivals_per_week", "fav_pref", "home_pref",
                  "popularity_weight", "recency_weight"):
            _check_trait(f"{name}.{t}", a[t])
        if a["timing"] not in TIMING_PROFILES:
            raise ValueError(f"{name}: unknown timing profile")


validate_params()
