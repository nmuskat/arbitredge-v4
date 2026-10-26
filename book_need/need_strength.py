"""
book_need/need_strength.py

Book Need decision table, Need Strength, and Data Quality. Need Strength
is deliberately structured to avoid double-counting: it derives from
exactly two genuinely independent axes (Pressure-direction-clarity x
current-segment-regime), with everything else acting as a
DOWNGRADE-ONLY modifier -- never an independent additional "vote," since
Market Response and Cross-book Agreement are not independent of each
other (agreement is a property of the same per-book data that produced
the aggregate regime).

IMPLEMENTATION NOTE, disclosed: the spec left one micro-rule genuinely
open -- exactly which regimes require key-number corroboration to avoid
the "no corroboration" downgrade. This module applies it only to
RESISTANCE (where key-number involvement is conceptually expected, since
resistance is fundamentally about a line holding near a number), not
universally. A reasonable, disclosed interpretation filling a real gap
in the otherwise-fully-locked spec, not a silent assumption.
"""

NOT_YET_EVALUABLE = "NOT_YET_EVALUABLE"
NO_CLEAR_NEED = "NO_CLEAR_NEED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

STRONG = "STRONG"
MODERATE = "MODERATE"
LEAN = "LEAN"
_LEVELS = [STRONG, MODERATE, LEAN]

PRESSURE_DIRECTION_MEDIAN_PERCENTILE = 50
HANDLE_INTENSITY_MEDIAN_PERCENTILE = 50


def pressure_direction_is_clear(public_pressure):
    if public_pressure is None:
        return False
    if public_pressure["side_preference_direction"] == "even":
        return False
    if public_pressure["side_preference_magnitude_undefined"]:
        return False
    mag_pct = public_pressure["side_preference_magnitude_percentile"]
    if mag_pct is None or mag_pct <= PRESSURE_DIRECTION_MEDIAN_PERCENTILE:
        return False
    return True


def infer_book_need(public_pressure, current_segment, usable_book_count, nothing_attempted):
    """
    nothing_attempted: True when no segment has reached its start time yet
    (all segments NOT_YET_OBSERVED) -- distinguishes "the evaluation
    window hasn't opened" (NOT_YET_EVALUABLE) from "the window is open
    but no usable data exists" (INSUFFICIENT_EVIDENCE). These are not the
    same situation and must never share a label.
    """
    if nothing_attempted:
        return NOT_YET_EVALUABLE, None

    if current_segment is None or usable_book_count < 2 or public_pressure is None:
        return INSUFFICIENT_EVIDENCE, None

    if not pressure_direction_is_clear(public_pressure):
        return NO_CLEAR_NEED, None

    regime = current_segment["aggregate_regime"]
    if regime == "MIXED":
        return NO_CLEAR_NEED, None

    pressure_side = "home" if public_pressure["side_preference_direction"] == "home" else "away"
    opposite_side = "away" if pressure_side == "home" else "home"

    if regime in ("RESISTANCE", "DIVERGENCE"):
        return opposite_side, STRONG
    if regime == "CONFIRMATION":
        return opposite_side, LEAN
    if regime == "PARTIAL_DIVERGENCE":
        return opposite_side, MODERATE
    return NO_CLEAR_NEED, None


def apply_modifiers_and_cap(base_strength, breadth, contradiction_flag, current_segment_regime,
                             key_number_states, public_pressure):
    if base_strength is None:
        return None
    idx = _LEVELS.index(base_strength)

    if breadth == "MAJORITY":
        idx = min(idx + 1, len(_LEVELS) - 1)
    if contradiction_flag:
        idx = min(idx + 1, len(_LEVELS) - 1)

    if current_segment_regime == "RESISTANCE":
        corroborated = any(s and s != "NO_KEY_NUMBER_INVOLVEMENT" for s in key_number_states)
        if not corroborated:
            idx = min(idx + 1, len(_LEVELS) - 1)

    strength = _LEVELS[idx]

    hi_pct = public_pressure["handle_intensity_percentile"] if public_pressure else None
    if hi_pct is None or hi_pct <= HANDLE_INTENSITY_MEDIAN_PERCENTILE:
        strength = LEAN

    return strength


def compute_data_quality(usable_book_count, any_stale, hi_coverage, sp_coverage, fallback_occurred,
                          nothing_attempted=False):
    """
    nothing_attempted: same meaning as in infer_book_need -- Data Quality
    must not pretend an assessment happened when no segment has started.
    """
    if nothing_attempted:
        return NOT_YET_EVALUABLE

    quality = "HIGH"
    if usable_book_count < 3:
        quality = "MODERATE"
    if any_stale or fallback_occurred:
        quality = "LOW" if quality == "MODERATE" else "MODERATE"
    if hi_coverage is not None and hi_coverage <= 2:
        quality = "LOW"
    if sp_coverage is not None and sp_coverage <= 1:
        quality = "LOW"
    return quality
