"""
book_need/juice.py

Implied-probability-based juice orientation (J), per the final locked
spec. Raw American-odds subtraction breaks at the positive/negative zero
crossing (e.g. -105 is numerically less than +105 but represents a WORSE
price for the bettor) -- both endpoints are converted to implied
probability before differencing, which is continuous and monotonic
across the entire real odds range with no discontinuity.
"""


def implied_probability(american_odds):
    if american_odds is None:
        return None
    if american_odds < 0:
        return -american_odds / (-american_odds + 100)
    return 100 / (american_odds + 100)


def compute_J(pressured_side_price_start, pressured_side_price_end):
    """
    +1 = juice worsened for the pressured side (implied probability
         increased -- the price is less favorable to a bettor on that side,
         i.e. accommodating).
    -1 = juice improved (implied probability decreased).
     0 = unchanged.
    None = either price missing.
    """
    if pressured_side_price_start is None or pressured_side_price_end is None:
        return None
    p_start = implied_probability(pressured_side_price_start)
    p_end = implied_probability(pressured_side_price_end)
    delta = p_end - p_start
    if delta > 0:
        return 1
    if delta < 0:
        return -1
    return 0
