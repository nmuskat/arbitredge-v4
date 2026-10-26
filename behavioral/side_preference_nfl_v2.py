"""
NFL Side Preference v2 — experimental/offline.

Estimates likely recreational preference using:
1. Consensus market favorite as the stronger baseline prior.
2. V1 narrative/momentum preference as secondary evidence.

This is NOT a measurement of actual ticket or handle percentages.
It does not use ATS outcomes.
CFB is untouched.
"""

from behavioral.side_preference_v1 import compute_side_preference_v1


def _favorite_from_home_spread(home_spread):
    if home_spread is None:
        return None

    if home_spread < 0:
        return "home"
    if home_spread > 0:
        return "away"

    return None


def compute_side_preference_nfl_v2(games):
    """
    Each game may contain _consensus_home_spread.

    confidence_score is ordinal, NOT a probability:

      0 = no usable direction
      1 = V1 only; no market favorite available
      2 = market favorite and V1 disagree
      3 = market favorite available; V1 neutral
      4 = market favorite and V1 agree

    Direction follows the market-favorite prior when available.
    Disagreement is preserved explicitly rather than hidden.
    """

    v1_results = compute_side_preference_v1(games)

    v1_by_id = {
        r["canonical_game_id"]: r
        for r in v1_results
    }

    results = []

    for game in games:
        gid = game["canonical_game_id"]
        v1 = v1_by_id[gid]

        v1_direction = v1.get("direction")

        favorite_direction = _favorite_from_home_spread(
            game.get("_consensus_home_spread")
        )

        if favorite_direction is None:
            direction = v1_direction
            confidence_score = (
                1 if v1_direction in ("home", "away") else 0
            )
            relationship = "NO_MARKET_PRIOR"

        elif v1_direction not in ("home", "away"):
            direction = favorite_direction
            confidence_score = 3
            relationship = "MARKET_PRIOR_ONLY"

        elif v1_direction == favorite_direction:
            direction = favorite_direction
            confidence_score = 4
            relationship = "MARKET_AND_V1_AGREE"

        else:
            # Market favorite and behavioral attractiveness disagree.
            # Do not manufacture a public-side conclusion from conflicting
            # proxies. Preserve the disagreement as unresolved.
            direction = "even"
            confidence_score = 0
            relationship = "MARKET_V1_CONFLICT"

        results.append({
            "canonical_game_id": gid,
            "direction": direction,
            "confidence_score": confidence_score,
            "relationship": relationship,
            "favorite_direction": favorite_direction,
            "v1_direction": v1_direction,
            # Preserve V1 magnitude for ranking/clarity when the
            # market prior and behavioral direction agree. Conflicts
            # deliberately receive zero usable magnitude.
            "normalized_lean_score": (
                v1.get("normalized_lean_score", 0.0)
                if relationship == "MARKET_AND_V1_AGREE"
                else 0.0
            ),
            "v1_normalized_lean_score": v1.get(
                "normalized_lean_score"
            ),
            "coverage_count": v1.get(
                "coverage_count"
            ),
            "v1_coverage_count": v1.get(
                "coverage_count"
            ),
        })

    return results
