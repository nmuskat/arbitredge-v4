"""
edge_v4/pricing.py -- odds conversion and vig removal.

All probabilities are floats in (0, 1). American odds are ints/floats with
|odds| >= 100. Vig-removal methods follow the standard literature
(multiplicative / additive / power / Shin); none is assumed "correct" --
the method is a recorded parameter of every benchmark.
"""

import math


def american_to_decimal(american):
    a = float(american)
    if abs(a) < 100:
        raise ValueError(f"invalid American odds {american!r}")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / -a)


def decimal_to_american(decimal):
    d = float(decimal)
    if d <= 1.0:
        raise ValueError(f"invalid decimal odds {decimal!r}")
    return round((d - 1.0) * 100.0) if d >= 2.0 else round(-100.0 / (d - 1.0))


def net_profit_per_unit(american):
    """Profit on a winning 1-unit stake."""
    return american_to_decimal(american) - 1.0


def implied_prob(american):
    return 1.0 / american_to_decimal(american)


def overround(americans):
    return sum(implied_prob(a) for a in americans) - 1.0


def _multiplicative(raw):
    s = sum(raw)
    return [r / s for r in raw]


def _additive(raw):
    excess = (sum(raw) - 1.0) / len(raw)
    out = [r - excess for r in raw]
    if min(out) <= 0:
        raise ValueError("additive de-vig produced a non-positive probability")
    return out


def _power(raw):
    # find k with sum(r^k) == 1 (k > 1 when overround > 0)
    lo, hi = 0.01, 50.0
    for _ in range(200):
        k = (lo + hi) / 2
        s = sum(r ** k for r in raw)
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    out = [r ** k for r in raw]
    s = sum(out)
    return [o / s for o in out]


def _shin(raw):
    # Shin (1993): solve for insider fraction z so probabilities sum to 1.
    total = sum(raw)
    n = len(raw)

    def probs(z):
        return [
            (math.sqrt(z * z + 4 * (1 - z) * r * r / total) - z) / (2 * (1 - z))
            for r in raw
        ]

    lo, hi = 0.0, 0.5
    for _ in range(200):
        z = (lo + hi) / 2
        if sum(probs(z)) > 1.0:
            lo = z
        else:
            hi = z
    out = probs((lo + hi) / 2)
    s = sum(out)
    return [o / s for o in out] if n else out


DEVIG_METHODS = {
    "multiplicative": _multiplicative,
    "additive": _additive,
    "power": _power,
    "shin": _shin,
}


def devig(americans, method="multiplicative"):
    """No-vig probabilities for a complete, mutually exclusive market."""
    if len(americans) < 2:
        raise ValueError("de-vig needs every outcome of the market")
    raw = [implied_prob(a) for a in americans]
    if sum(raw) < 1.0 - 1e-9:
        # an under-round market (arbitrage or stale/mismatched quotes)
        raise ValueError("market is under-round; quotes are inconsistent or stale")
    return DEVIG_METHODS[method](raw)


def consensus_fair_prob(book_quotes, weights=None, method="multiplicative"):
    """
    book_quotes: {book: (price_side_a, price_side_b)} for the SAME line.
    weights: {book: w}; default equal. Returns fair probability of side A,
    the weighted mean of per-book no-vig probabilities.
    """
    if not book_quotes:
        raise ValueError("no quotes")
    num = den = 0.0
    for book, (a, b) in book_quotes.items():
        w = 1.0 if weights is None else float(weights.get(book, 0.0))
        if w <= 0:
            continue
        num += w * devig([a, b], method)[0]
        den += w
    if den == 0:
        raise ValueError("no positively weighted quotes")
    return num / den


def best_price(book_prices):
    """{book: american} -> (book, american) with the highest payout."""
    if not book_prices:
        raise ValueError("no prices")
    return max(book_prices.items(), key=lambda kv: american_to_decimal(kv[1]))
