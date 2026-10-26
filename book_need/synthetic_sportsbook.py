"""
ArbitrEdge Synthetic Sportsbook Risk Desk.

Purpose:
Estimate which side a hypothetical sportsbook would prefer to cover
using observable bettor pressure and sportsbook market response.

This is an exposure model, not a game-prediction model.
"""

from book_need.synthetic_bettors import simulate_bettor_ledger

VERSION = "synthetic_sportsbook_v2_shared_pool"

BOOK_SIDE_REGIMES = {
    "RESISTANCE",
    "DIVERGENCE",
    "PARTIAL_DIVERGENCE",
}

PUBLIC_SIDE_REGIMES = {"CONFIRMATION"}


def _opposite(side):
    if side == "home":
        return "away"
    if side == "away":
        return "home"
    return None


def evaluate_synthetic_book(public_pressure, segment, usable_book_count, canonical_game_id=None, synthetic_ledger=None):
    result = {
        "version": VERSION,
        "public_side": None,
        "book_need_side": None,
        "liability_score": None,
        "liability_strength": None,
        "decision": "PASS",
        "reason": None,
        "book_side_responses": 0,
        "public_side_responses": 0,
        "neutral_responses": 0,
        "usable_books": usable_book_count or 0,
        "handle_intensity_percentile": None,
        "side_preference_magnitude_percentile": None,
        "synthetic_bettor_ledger": None,
    }

    if public_pressure is None or segment is None:
        result["reason"] = "insufficient_market_evidence"
        return result

    public_side = public_pressure.get("side_preference_direction")
    if public_side not in ("home", "away"):
        result["reason"] = "no_directional_public_pressure"
        return result

    result["public_side"] = public_side
    result["book_need_side"] = _opposite(public_side)

    hi = public_pressure.get("handle_intensity_percentile")
    sp = public_pressure.get("side_preference_magnitude_percentile")

    result["handle_intensity_percentile"] = hi
    result["side_preference_magnitude_percentile"] = sp

    # NFL V2 supplies a ledger created from one shared slate population.
    # Legacy/direct callers fall back to the original per-game simulator.
    ledger = synthetic_ledger
    if ledger is None:
        ledger = simulate_bettor_ledger(
            canonical_game_id,
            public_side,
            sp,
            hi,
        )
    result["synthetic_bettor_ledger"] = ledger

    regimes = []
    for response in (segment.get("per_book") or {}).values():
        regime = response.get("regime")
        if regime and regime != "INSUFFICIENT":
            regimes.append(regime)

    book_side = sum(r in BOOK_SIDE_REGIMES for r in regimes)
    public_side_count = sum(r in PUBLIC_SIDE_REGIMES for r in regimes)
    neutral = len(regimes) - book_side - public_side_count

    result["book_side_responses"] = book_side
    result["public_side_responses"] = public_side_count
    result["neutral_responses"] = neutral

    if usable_book_count < 3:
        result["reason"] = "insufficient_book_coverage"
        return result

    # Synthetic liability score (0-100).
    #
    # Public concentration estimates how one-sided the hypothetical ledger is.
    # Market activity estimates how economically meaningful the observed market is.
    # Sportsbook response measures whether books resist/oppose that public pressure.
    #
    # Missing components are treated conservatively rather than manufactured.
    # A synthetic liability estimate requires exposure evidence.
    # Book movement by itself cannot establish which side the ledger needs.
    if synthetic_ledger is not None:
        exposure = ledger.get("exposure_score")
        handle_share = ledger.get("handle_share_of_slate")

        if exposure is None:
            result["reason"] = "insufficient_exposure_evidence"
            return result

        # V2: modeled ledger imbalance is the primary liability evidence.
        # exposure_score is an imbalance measure, not naturally a 0-100
        # liability scale. Rescale it so realistic strong imbalances can
        # contribute appropriately while balanced ledgers remain near zero.
        exposure_pressure = min(100.0, max(0.0, float(exposure) * 2.5))

        # Slate handle share adds economic importance without pretending
        # observable market activity is literal sportsbook handle.
        components = [exposure_pressure]
        weights = [0.60]

        if handle_share is not None:
            # Convert slate share into a bounded importance score.
            # 20%+ of slate handle receives maximum importance credit.
            handle_importance = min(100.0, float(handle_share) * 5.0)
            components.append(handle_importance)
            weights.append(0.10)
    else:
        # Legacy compatibility path.
        if sp is None:
            result["reason"] = "insufficient_exposure_evidence"
            return result

        components = [float(sp)]
        weights = [0.50]

        if hi is not None:
            components.append(float(hi))
            weights.append(0.20)

    # Include neutral books in the denominator. One resisting book out of
    # nine is weak evidence; eight resisting books out of nine is strong.
    regime_total = book_side + public_side_count + neutral
    if regime_total:
        response_score = 50.0 + (
            50.0 * (book_side - public_side_count) / regime_total
        )
        response_score = max(0.0, min(100.0, response_score))
        components.append(response_score)
        weights.append(0.30)

    # Missing optional inputs reduce confidence instead of being
    # re-normalized into an artificial maximum score.
    score = sum(v * w for v, w in zip(components, weights))
    score = round(max(0.0, min(100.0, score)), 1)
    result["liability_score"] = score

    if score >= 75:
        strength = "MAJOR"
    elif score >= 65:
        strength = "STRONG"
    elif score >= 55:
        strength = "MODERATE"
    else:
        strength = "LOW"

    result["liability_strength"] = strength

    # A sportsbook need requires both modeled liability and observable
    # book-side response. Public popularity alone cannot create a bet.
    response_support = book_side > public_side_count

    if score >= 65 and response_support:
        result["decision"] = "BOOK_NEED"
        result["reason"] = "liability_and_market_response_converge"
    else:
        result["decision"] = "PASS"
        result["reason"] = (
            "market_response_not_supportive"
            if not response_support
            else "liability_below_action_threshold"
        )

    return result
