"""
behavioral/side_preference_v1.py

RecreationalSidePreferenceV1 -- EXPERIMENTAL / UNCALIBRATED. One signed,
dimensionless recreational-lean score. Per instruction: NOT split into
ticket-weighted/dollar-weighted outputs -- we have no empirical basis
for claiming those are even distinguishable with current inputs.

DIRECTION CONVENTION (explicit, documented): POSITIVE lean_score means
the recreational lean is toward the HOME side. NEGATIVE means AWAY. Every
input differential below is computed as (home_value - away_value) so
this convention holds uniformly.

PARAMETER CLASSIFICATION:

MECHANICALLY DETERMINED:
  - The slate-relative z-scoring procedure (shared with Handle Intensity).
  - The (home - away) differencing convention for every feature.

LITERATURE-MOTIVATED BUT UNVALIDATED (all positive-sign, all from the
recency/recreational-bias research already conducted):
  - recent_wins_differential, margin_differential, streak_differential,
    blowout_differential: recency/"hot-hand" bias -- documented as real
    but WEAKER evidence than favorite-longshot bias specifically.
  - rest_advantage_days: a plausible narrative-driven hypothesis, flagged
    as comparatively the MOST speculative of the primary features -- the
    original research described this as "plausible," not well-established.
  - rank_prestige_differential: brand/prestige bias.

ESTIMATED FROM PERMISSIBLE DATA: none. No fitting occurs anywhere here.

DELIBERATELY LEFT UNSET:
  - ALL relative weights between primary features: equal-weighted,
    disclosed default, not fitted, not asserted as correct.
  - Price/juice at cutoff: no distinct documented behavioral hypothesis
    was found separating price from the spread number itself -- deferred
    rather than included on a weak, undisclosed justification.

ENRICHMENT RUNG (per-book market state) -- favorite_side_at_cutoff:
  - LITERATURE-MOTIVATED, and notably on STRONGER evidentiary footing
    than any primary-score feature: favorite-longshot bias is the single
    most consistently documented bias in the sports-betting literature.
    Unlike Handle Intensity's spread_magnitude enrichment term
    (deliberately unsigned, a true placeholder), this term's sign
    (+1 if home is favorite, -1 if away, 0 if pick_em/unknown) follows
    that literature directly. Still UNVALIDATED for this specific model
    and population -- not an arbitrary placeholder, but not confirmed
    either.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavioral._stats import slate_zscore  # noqa: E402

EXPERIMENTAL_LABEL = "EXPERIMENTAL / UNCALIBRATED -- signed recreational-lean score only, not a validated Side Preference measurement"
DIRECTION_CONVENTION = "positive = lean toward HOME, negative = lean toward AWAY"

# Per instruction: favorite_side_at_cutoff, while literature-motivated
# (favorite-longshot bias -- the strongest-evidenced bias in the
# literature), is still unvalidated in our own data and must not appear
# in the active/default Side Preference score. Plumbing preserved for
# future ablation only.
ENRICHMENT_ACTIVE = False

PRIMARY_FEATURES = [
    "recent_wins_differential", "margin_differential", "streak_differential",
    "blowout_differential", "rest_advantage_days", "rank_prestige_differential",
]

FEATURE_SIGNS = {
    "recent_wins_differential": 1,
    "margin_differential": 1,
    "streak_differential": 1,
    "blowout_differential": 1,
    "rest_advantage_days": 1,
    "rank_prestige_differential": 1,
}


def _none_safe_sub(a, b):
    if a is None or b is None:
        return None
    return a - b


def _derive_raw_features(game_row, context_row=None):
    context_row = context_row or {}
    ranking_home = context_row.get("ranking_home")
    ranking_away = context_row.get("ranking_away")

    home_win_streak = game_row.get("home_current_win_streak")
    home_loss_streak = game_row.get("home_current_loss_streak")
    away_win_streak = game_row.get("away_current_win_streak")
    away_loss_streak = game_row.get("away_current_loss_streak")
    home_momentum = _none_safe_sub(home_win_streak, home_loss_streak)
    away_momentum = _none_safe_sub(away_win_streak, away_loss_streak)

    home_blowout = _none_safe_sub(game_row.get("home_last_game_blowout_win"), game_row.get("home_last_game_blowout_loss"))
    away_blowout = _none_safe_sub(game_row.get("away_last_game_blowout_win"), game_row.get("away_last_game_blowout_loss"))

    rank_prestige_home = 1 if ranking_home is not None else 0
    rank_prestige_away = 1 if ranking_away is not None else 0

    return {
        "recent_wins_differential": _none_safe_sub(game_row.get("home_wins_last_3"), game_row.get("away_wins_last_3")),
        "margin_differential": _none_safe_sub(
            game_row.get("home_point_margin_avg_last_3"), game_row.get("away_point_margin_avg_last_3")
        ),
        "streak_differential": _none_safe_sub(home_momentum, away_momentum),
        "blowout_differential": _none_safe_sub(home_blowout, away_blowout),
        "rest_advantage_days": game_row.get("rest_advantage_days"),
        "rank_prestige_differential": rank_prestige_home - rank_prestige_away,
    }


def compute_side_preference_v1(games):
    """
    games: list of dicts, each with 'canonical_game_id' and the raw
    engineered-feature fields (optionally '_context'). Standardized
    relative to this SAME batch of games (the evaluation slate), the same
    uniform methodology Handle Intensity uses.

    COVERAGE NORMALIZATION (locked policy, identical to Handle Intensity):
    raw_lean_score is the straight sum of all 6 signed z-score terms
    (kept for diagnostic transparency), never used directly for
    direction/comparison. normalized_lean_score = raw_lean_score divided
    by coverage_count is the ACTIVE score -- a game with only 3 of 6
    components populated (e.g. a season opener) must not be mechanically
    capable of a larger-magnitude lean purely from having fewer terms
    forced to zero. coverage_count=0 -> normalized_lean_score=0.0.
    direction is based on normalized_lean_score's sign, not raw's.

    Returns a list of dicts, same order as input:
      {canonical_game_id, raw_lean_score, coverage_count, coverage_fraction,
       normalized_lean_score, direction, direction_convention,
       component_contributions, label}
    """
    derived = [_derive_raw_features(g, g.get("_context")) for g in games]

    per_feature_zscores = {}
    for feature in PRIMARY_FEATURES:
        raw_values = [d[feature] for d in derived]
        per_feature_zscores[feature] = slate_zscore(raw_values)

    results = []
    for i, g in enumerate(games):
        contributions = {
            f: FEATURE_SIGNS[f] * per_feature_zscores[f][i] for f in PRIMARY_FEATURES
        }
        raw_lean_score = sum(contributions.values())
        coverage_count = sum(1 for f in PRIMARY_FEATURES if derived[i][f] is not None)
        coverage_fraction = coverage_count / len(PRIMARY_FEATURES)
        normalized_lean_score = raw_lean_score / coverage_count if coverage_count > 0 else 0.0

        direction = "home" if normalized_lean_score > 0 else ("away" if normalized_lean_score < 0 else "even")
        results.append({
            "canonical_game_id": g["canonical_game_id"],
            "raw_lean_score": raw_lean_score,
            "coverage_count": coverage_count,
            "coverage_fraction": coverage_fraction,
            "normalized_lean_score": normalized_lean_score,
            "direction": direction,
            "direction_convention": DIRECTION_CONVENTION,
            "component_contributions": contributions,
            "label": EXPERIMENTAL_LABEL,
        })
    return results


def compute_side_preference_v1_market_enrichment(games, book):
    """
    INACTIVE by default (ENRICHMENT_ACTIVE=False) -- per instruction,
    favorite_side_at_cutoff is a candidate for future ablation only, not
    part of the default score, despite being literature-motivated on
    comparatively strong evidentiary footing. Plumbing preserved, not
    invoked by any active path.

    Builds on the PRIMARY function's normalized_lean_score, for
    consistency with the coverage-normalization policy above.
    """
    primary_results = compute_side_preference_v1(games)

    favorite_directional_values = []
    for g in games:
        state = g.get("market_state") or {}
        favorite_side = state.get("favorite_side")
        if favorite_side == "home":
            favorite_directional_values.append(1.0)
        elif favorite_side == "away":
            favorite_directional_values.append(-1.0)
        elif favorite_side == "pick_em":
            favorite_directional_values.append(0.0)
        else:
            favorite_directional_values.append(None)

    z_favorite = slate_zscore(favorite_directional_values)

    results = []
    for i, g in enumerate(games):
        enriched_score = primary_results[i]["normalized_lean_score"] + z_favorite[i]
        direction = "home" if enriched_score > 0 else ("away" if enriched_score < 0 else "even")
        results.append({
            "canonical_game_id": g["canonical_game_id"],
            "book": book,
            "primary_normalized_lean_score": primary_results[i]["normalized_lean_score"],
            "enrichment_lean_score": enriched_score,
            "favorite_side_used": (g.get("market_state") or {}).get("favorite_side"),
            "direction": direction,
            "direction_convention": DIRECTION_CONVENTION,
            "label": EXPERIMENTAL_LABEL,
            "active": ENRICHMENT_ACTIVE,
        })
    return results
