"""
book_need/regime.py

Per-book Market Response regime, from the locked (S,J) table. Reuses
ReversalTarget's own sign-change-in-consecutive-deltas logic for
intra-segment reversal detection, applied to the segment's own raw
observation window.
"""

CONFIRMATION = "CONFIRMATION"
DIVERGENCE = "DIVERGENCE"
RESISTANCE = "RESISTANCE"
PARTIAL_DIVERGENCE = "PARTIAL_DIVERGENCE"
NO_MOVEMENT = "NO_MOVEMENT"
INSUFFICIENT = "INSUFFICIENT"


def classify_regime(S, J):
    if S is None or J is None:
        return INSUFFICIENT
    if S == 1:
        return CONFIRMATION
    if S == -1:
        return DIVERGENCE
    if J == 1:
        return RESISTANCE
    if J == -1:
        return PARTIAL_DIVERGENCE
    return NO_MOVEMENT


def detect_intra_segment_reversal(raw_spread_sequence):
    values = [v for v in raw_spread_sequence if v is not None]
    if len(values) < 2:
        return False
    signs = []
    for a, b in zip(values, values[1:]):
        delta = b - a
        if delta != 0:
            signs.append(1 if delta > 0 else -1)
    if len(signs) < 2:
        return False
    for s1, s2 in zip(signs, signs[1:]):
        if s1 != s2:
            return True
    return False


def compute_per_book_response(pressure_side, spread_home_start, spread_home_end,
                                pressured_side_price_start, pressured_side_price_end,
                                raw_spread_sequence=None):
    from book_need.spread import compute_S
    from book_need.juice import compute_J
    from book_need.key_numbers import classify_key_number_state

    S = compute_S(pressure_side, spread_home_start, spread_home_end)
    J = compute_J(pressured_side_price_start, pressured_side_price_end)
    regime = classify_regime(S, J)

    contradiction = (
        S is not None and J is not None and S != 0 and J != 0 and
        ((S == 1 and J == -1) or (S == -1 and J == 1))
    )
    key_number_state = classify_key_number_state(spread_home_start, spread_home_end)
    intra_reversal = detect_intra_segment_reversal(raw_spread_sequence) if raw_spread_sequence else False

    return {
        "regime": regime, "S": S, "J": J,
        "key_number_state": key_number_state,
        "contradiction": contradiction,
        "intra_segment_reversal": intra_reversal,
    }
