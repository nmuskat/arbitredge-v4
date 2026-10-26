"""
simulation/accounting.py

Phase 2A. American-odds money math, as a clean, reusable, pure-function
primitive -- no game, no bettor, no side-naming concepts live here, only
dollars. ledger.py builds the spread-settlement logic on top of this.

NOTHING here reads wall-clock time, touches a database, or depends on
any forbidden model-derived field. This module is intentionally the
smallest possible surface: two functions.
"""


class InvalidPriceError(ValueError):
    """Raised for a price of 0, None, or a non-numeric value."""


def implied_probability(price):
    """
    American odds -> implied probability, no vig removal (this is a
    single quoted price, not a two-sided market -- removing vig would
    require both sides and is not this function's job).

    price > 0 (underdog-style price):  100 / (price + 100)
    price < 0 (favorite-style price):  abs(price) / (abs(price) + 100)
    price == 0 or None: rejected -- 0 is not a legitimate American price
    (push-only/even-money markets quote +100 or -100, never 0), and a
    missing price must never be silently treated as "even money."
    """
    if price is None:
        raise InvalidPriceError("price is None")
    price = float(price)
    if price == 0:
        raise InvalidPriceError("price of 0 is not a legitimate American odds quote")

    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def profit_and_gross_return(stake, price):
    """
    Returns (profit, gross_return) for a winning wager of `stake` at
    American odds `price`. gross_return INCLUDES the returned stake
    (this is "what the book pays out on a win," not "what the bettor
    nets"). Rejects price == 0 / None rather than fabricating a
    default price (e.g. -110) -- a caller with no legitimate price for
    this side must skip the wager, not invent one.

    price > 0: profit = stake * price / 100
    price < 0: profit = stake * 100 / abs(price)
    +100/-100 (even money) both resolve to profit == stake, as they
    must -- this is the edge case explicitly required to work.

    No premature rounding: callers that need a display value round at
    the point of display/aggregation, not here.
    """
    if stake is None or stake < 0:
        raise ValueError(f"stake must be a non-negative number, got {stake!r}")
    if price is None:
        raise InvalidPriceError("price is None")
    price = float(price)
    if price == 0:
        raise InvalidPriceError("price of 0 is not a legitimate American odds quote")

    if price > 0:
        profit = stake * price / 100.0
    else:
        profit = stake * 100.0 / abs(price)

    gross_return = stake + profit
    return profit, gross_return
