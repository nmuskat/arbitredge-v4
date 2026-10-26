"""
book_need/key_numbers.py

Exact key-number states -- no proximity band, only exact equality and
strict comparative distance. Reuses crosses_key_number/KEY_NUMBERS
directly rather than reimplementing.
"""

from validation.targets.market_movement import crosses_key_number, KEY_NUMBERS

AT_KEY_NUMBER = "AT_KEY_NUMBER"
CROSSED_KEY_NUMBER = "CROSSED_KEY_NUMBER"
MOVED_TOWARD_KEY_AND_STOPPED_AT_KEY = "MOVED_TOWARD_KEY_AND_STOPPED_AT_KEY"
MOVED_TOWARD_KEY_WITHOUT_REACHING = "MOVED_TOWARD_KEY_WITHOUT_REACHING"
NO_KEY_NUMBER_INVOLVEMENT = "NO_KEY_NUMBER_INVOLVEMENT"


def _distance_to_nearest_key_number(value):
    if value is None:
        return None
    return min(abs(abs(value) - k) for k in KEY_NUMBERS)


def _strictly_crossed(spread_home_start, spread_home_end):
    """Genuinely passed THROUGH a key number (strictly between the two
    endpoints) -- distinct from merely landing exactly ON one, which is
    a 'stopped at' case, not a 'crossed' case."""
    lo, hi = (spread_home_start, spread_home_end) if spread_home_start <= spread_home_end else (spread_home_end, spread_home_start)
    thresholds = set()
    for k in KEY_NUMBERS:
        thresholds.add(k)
        thresholds.add(-k)
    return any(lo < t < hi for t in thresholds)


def classify_key_number_state(spread_home_start, spread_home_end):
    if spread_home_start is None or spread_home_end is None:
        return None

    at_end = any(abs(abs(spread_home_end) - k) == 0 for k in KEY_NUMBERS)

    if spread_home_start == spread_home_end:
        # held -- nothing moved, so "crossed"/"approached" are meaningless here
        return AT_KEY_NUMBER if at_end else NO_KEY_NUMBER_INVOLVEMENT

    dist_start = _distance_to_nearest_key_number(spread_home_start)
    dist_end = _distance_to_nearest_key_number(spread_home_end)
    approached = dist_end < dist_start

    if _strictly_crossed(spread_home_start, spread_home_end):
        return CROSSED_KEY_NUMBER
    if approached and at_end:
        return MOVED_TOWARD_KEY_AND_STOPPED_AT_KEY
    if approached:
        return MOVED_TOWARD_KEY_WITHOUT_REACHING
    if at_end:
        return AT_KEY_NUMBER
    return NO_KEY_NUMBER_INVOLVEMENT
