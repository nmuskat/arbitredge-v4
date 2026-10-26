"""
book_need/spread.py

Orientation-normalized spread movement (S). pressure_sign is necessary
here (unlike juice, which is already oriented by selecting the pressured
side's own price) since spread_home_delta is a raw, unoriented value.
"""


def compute_S(pressure_side, spread_home_start, spread_home_end):
    """+1 = spread moved WITH pressure (toward the pressured side becoming
    more favored). -1 = moved AGAINST. 0 = held exactly. None = missing."""
    if spread_home_start is None or spread_home_end is None:
        return None
    pressure_sign = -1 if pressure_side == "home" else 1
    delta = spread_home_end - spread_home_start
    signed = pressure_sign * delta
    if signed > 0:
        return 1
    if signed < 0:
        return -1
    return 0
