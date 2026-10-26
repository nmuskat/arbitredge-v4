"""
book_need/aggregation.py

Cross-book regime aggregation (UNANIMOUS / MAJORITY / NO_MAJORITY -> MIXED)
and multi-segment temporal aggregation: latest usable segment governs
current response; earlier segments remain trajectory/contradiction
metadata only.
"""

from collections import Counter

UNANIMOUS = "UNANIMOUS"
MAJORITY = "MAJORITY"
NO_MAJORITY = "NO_MAJORITY"
MIXED = "MIXED"

CONFIRM_CAMP = {"CONFIRMATION", "NO_MOVEMENT"}
RESIST_CAMP = {"RESISTANCE", "DIVERGENCE", "PARTIAL_DIVERGENCE"}


def aggregate_book_regimes(per_book_regimes):
    """per_book_regimes: list of regime strings (INSUFFICIENT excluded --
    those books contribute to Data Quality, not to the regime vote).
    Returns (aggregate_regime_or_None, breadth_or_None)."""
    usable = [r for r in per_book_regimes if r != "INSUFFICIENT"]
    n = len(usable)
    if n == 0:
        return None, None
    counts = Counter(usable)
    top_regime, top_count = counts.most_common(1)[0]
    if top_count == n:
        return top_regime, UNANIMOUS
    if top_count > n / 2:
        return top_regime, MAJORITY
    return MIXED, NO_MAJORITY


def select_current_segment(segment_results):
    """Latest COMPLETE/IN_PROGRESS segment with a real aggregate_regime
    governs. Returns (current_segment_dict_or_None, fallback_occurred)."""
    attempted = [s for s in segment_results if s["status"] != "NOT_YET_OBSERVED"]
    if not attempted:
        return None, False

    latest_attempted = attempted[-1]
    if latest_attempted.get("aggregate_regime") is not None:
        return latest_attempted, False

    for s in reversed(attempted[:-1]):
        if s.get("aggregate_regime") is not None:
            return s, True
    return None, True


def contradiction_camp(regime):
    if regime in CONFIRM_CAMP:
        return "confirm"
    if regime in RESIST_CAMP:
        return "resist"
    return None


def check_prior_contradiction(current_segment, segment_results):
    order = {"A": 0, "B": 1, "C": 2}
    current_idx = order[current_segment["name"]]
    for s in reversed(segment_results[:current_idx]):
        if s.get("aggregate_regime") is not None:
            cur_camp = contradiction_camp(current_segment["aggregate_regime"])
            prior_camp = contradiction_camp(s["aggregate_regime"])
            return bool(cur_camp and prior_camp and cur_camp != prior_camp)
    return False
