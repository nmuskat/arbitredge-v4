"""
behavioral/handle_intensity_v1.py

HandleIntensityV1 -- EXPERIMENTAL / UNCALIBRATED. A slate-relative,
direction-agnostic, purely mechanical scoring function. No fitted
parameters anywhere -- there is no legitimate behavioral label to fit
against yet, and this module does not pretend otherwise.

PARAMETER CLASSIFICATION (as required before implementation):

MECHANICALLY DETERMINED:
  - The slate-relative z-scoring procedure itself (behavioral/_stats.py).
  - Percentile-rank conversion.
  - The sign of the competing-games-count terms: MORE simultaneous games
    necessarily means attention is split across more options -- this is a
    structural/definitional consequence of finite attention, not a
    behavioral hypothesis being asserted.

LITERATURE-MOTIVATED BUT UNVALIDATED:
  - Inclusion and positive sign of ranked_team_count and has_tv_network
    (marquee-matchup / national-broadcast attention hypothesis, from the
    original research). Never empirically confirmed for this specific
    model or population.

ESTIMATED FROM PERMISSIBLE DATA: none. No fitting occurs anywhere here.

DELIBERATELY LEFT UNSET:
  - day_of_week: categorical; any numeric encoding would itself be an
    undisclosed hand-tuned weight. Excluded until either a principled
    encoding or real behavioral data exists to fit a categorical-aware
    model responsibly.
  - tv_network's specific identity/prestige: only bare PRESENCE is used
    (has_tv_network); which network it is remains unused, since ranking
    networks by prestige would be an arbitrary judgment call.
  - spread_magnitude_at_cutoff and any other market-state field: reserved
    EXCLUSIVELY for the separate enrichment rung below, per explicit
    instruction not to assume a direction in the primary score.
  - ALL RELATIVE WEIGHTS between included features: equal-weighted by
    disclosed default, not fitted, not asserted as behaviorally correct.
    Subject to revision only once real evidence, via the validation
    harness, justifies differential weighting.

Output is a dimensionless raw_score (internal) and a slate-relative
percentile_rank (external). Never displayed as a percentage claiming a
real-world meaning, a probability, or a confidence value.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from behavioral._stats import slate_zscore, slate_percentile_ranks  # noqa: E402

EXPERIMENTAL_LABEL = "EXPERIMENTAL / UNCALIBRATED -- slate-relative ordering only, not a validated Handle Intensity measurement"

# Per instruction: the spread_magnitude enrichment term is an explicitly
# arbitrary, unvalidated placeholder. The plumbing is preserved below
# (compute_handle_intensity_v1_market_enrichment still works and is
# tested), but it must never be invoked by any active/default output
# path until real evidence supports a direction. This flag exists so
# that fact is discoverable at a glance, not just documented in prose.
ENRICHMENT_ACTIVE = False

PRIMARY_FEATURES = [
    "is_standalone_same_sport", "is_standalone_all_sports",
    "competing_games_same_window_same_sport", "competing_games_same_window_all_sports",
    "ranked_team_count", "has_tv_network",
]

# +1 or -1 applied to each feature's z-score before the equal-weighted sum.
# See classification above for the reasoning behind each sign.
FEATURE_SIGNS = {
    "is_standalone_same_sport": 1,
    "is_standalone_all_sports": 1,
    "competing_games_same_window_same_sport": -1,
    "competing_games_same_window_all_sports": -1,
    "ranked_team_count": 1,
    "has_tv_network": 1,
}

# Enrichment coefficient for spread_magnitude_at_cutoff -- EXPLICITLY an
# arbitrary, untested placeholder, NOT a hypothesis. Per instruction: do
# not assume close spreads attract more attention or large spreads
# attract less. Whether this term belongs at all, and with which sign, is
# exactly what future ablation against real evidence families is for.
ENRICHMENT_SPREAD_MAGNITUDE_COEFFICIENT = 1.0


def _derive_raw_features(game_row, context_row=None):
    """game_row: a V1/V2 engineered-features row (dict-like) for one game.
    context_row: the corresponding V2 context_state_as_of() result, or
    None if unavailable -- unavailable context degrades gracefully to
    'no ranking/TV information', never fabricated."""
    context_row = context_row or {}
    ranking_home = context_row.get("ranking_home")
    ranking_away = context_row.get("ranking_away")
    tv_network = context_row.get("tv_network")

    ranked_team_count = (1 if ranking_home is not None else 0) + (1 if ranking_away is not None else 0)
    has_tv_network = 1 if tv_network is not None else 0

    return {
        "is_standalone_same_sport": game_row.get("is_standalone_same_sport"),
        "is_standalone_all_sports": game_row.get("is_standalone_all_sports"),
        "competing_games_same_window_same_sport": game_row.get("competing_games_same_window_same_sport"),
        "competing_games_same_window_all_sports": game_row.get("competing_games_same_window_all_sports"),
        "ranked_team_count": ranked_team_count,
        "has_tv_network": has_tv_network,
    }


def compute_handle_intensity_v1(games):
    """
    games: list of dicts, each with 'canonical_game_id' and the raw
    engineered-feature fields (optionally '_context'). ALL games passed
    together form ONE comparison slate -- Handle Intensity is inherently
    slate-relative; calling this on a single isolated game produces a
    real normalized_score but a meaningless (None) percentile_rank.

    COVERAGE NORMALIZATION (locked policy, per explicit instruction):
    raw_score is the straight sum of all 6 signed z-score terms, with a
    missing raw input contributing exactly 0.0 to that term -- kept for
    diagnostic transparency (component_contributions below), but NEVER
    used directly for ranking. normalized_score = raw_score divided by
    coverage_count (how many of the 6 components had real, non-None
    input for THIS game) is the ACTIVE score: this prevents a
    well-covered game from being mechanically capable of larger-magnitude
    scores than a sparsely-covered game purely because it has more terms
    that could each swing the sum, independent of actual signal strength.
    coverage_count=0 -> normalized_score=0.0 (neutral, never fabricated).
    percentile_rank is computed from normalized_score, not raw_score.

    Returns a list of dicts, same order as input:
      {canonical_game_id, raw_score, coverage_count, coverage_fraction,
       normalized_score, percentile_rank, component_contributions, label}
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
        raw_score = sum(contributions.values())
        coverage_count = sum(1 for f in PRIMARY_FEATURES if derived[i][f] is not None)
        coverage_fraction = coverage_count / len(PRIMARY_FEATURES)
        normalized_score = raw_score / coverage_count if coverage_count > 0 else 0.0

        results.append({
            "canonical_game_id": g["canonical_game_id"],
            "raw_score": raw_score,
            "coverage_count": coverage_count,
            "coverage_fraction": coverage_fraction,
            "normalized_score": normalized_score,
            "component_contributions": contributions,
            "label": EXPERIMENTAL_LABEL,
        })

    normalized_scores = [r["normalized_score"] for r in results]
    percentiles = slate_percentile_ranks(normalized_scores)
    for i, r in enumerate(results):
        r["percentile_rank"] = percentiles[i]

    return results


def compute_handle_intensity_v1_market_enrichment(games, book):
    """
    INACTIVE by default (ENRICHMENT_ACTIVE=False) -- per instruction, the
    spread_magnitude coefficient below is an explicitly arbitrary,
    unvalidated placeholder and must not appear in any active output.
    This function is preserved as tested plumbing for future ablation
    only; nothing in this codebase currently calls it as part of a
    default/active score.

    Builds on the PRIMARY function's normalized_score (not raw_score),
    for consistency with the coverage-normalization policy above.
    """
    primary_results = compute_handle_intensity_v1(games)

    spread_magnitudes = [(g.get("market_state") or {}).get("spread_magnitude") for g in games]
    z_spread_magnitude = slate_zscore(spread_magnitudes)

    enrichment_scores = [
        primary_results[i]["normalized_score"] + ENRICHMENT_SPREAD_MAGNITUDE_COEFFICIENT * z_spread_magnitude[i]
        for i in range(len(games))
    ]
    percentiles = slate_percentile_ranks(enrichment_scores)

    return [
        {
            "canonical_game_id": games[i]["canonical_game_id"],
            "book": book,
            "primary_normalized_score": primary_results[i]["normalized_score"],
            "enrichment_score": enrichment_scores[i],
            "spread_magnitude_used": spread_magnitudes[i],
            "percentile_rank": percentiles[i],
            "label": EXPERIMENTAL_LABEL,
            "active": ENRICHMENT_ACTIVE,
        }
        for i in range(len(games))
    ]
