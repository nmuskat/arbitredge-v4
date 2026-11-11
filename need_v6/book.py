"""
need_v6/book.py -- incremental wager-level exposure of one game-market (the desk's live risk view).

Book P&L is tracked on a grid of every possible final home margin (spread) or total (totals), each wager at its
OWN number, price and stake: pushes, middles and different numbers are exact. The official Financial NEED
record still comes from the existing sportsbook_v4.settlement functions (see need.py); this module only gives
the desk a fast running view, and tests check both agree.
"""

import numpy as np

from sportsbook_v4.market import profit_on_win

GRID = {"spread": np.arange(-90, 91), "total": np.arange(0, 181)}


def wager_win(market, side, point, x):
    """+1 win / 0 push / -1 loss for the bettor, vectorised over outcomes x (margin or total)."""
    if market == "spread":
        v = (x + point) if side == "home" else (-x + point)
    else:
        v = (x - point) if side == "over" else (point - x)
    return np.sign(v)


class Exposure:
    def __init__(self, market):
        self.market = market
        self.x = GRID[market]
        self.pnl = np.zeros(len(self.x))
        self.handle = 0.0
        self.side_handle = {}
        self.n = 0

    def add(self, side, point, price, stake):
        r = wager_win(self.market, side, point, self.x)
        win = profit_on_win(stake, price)
        self.pnl += np.where(r > 0, -win, np.where(r < 0, stake, 0.0))
        self.handle += stake
        self.side_handle[side] = self.side_handle.get(side, 0.0) + stake
        self.n += 1

    def worst(self):
        return float(self.pnl.min()) if self.n else 0.0

    def stats(self, pmf):
        """(expected book P&L, standard deviation) under a probability vector over the same grid."""
        if not self.n:
            return 0.0, 0.0
        ev = float(np.dot(pmf, self.pnl))
        sd = float(np.sqrt(max(0.0, np.dot(pmf, (self.pnl - ev) ** 2))))
        return ev, sd

    def clean(self):
        """(book P&L if side B covers every accepted number, ... if side A covers every accepted number)."""
        return float(self.pnl[0]), float(self.pnl[-1])

    def heavy_side(self, sides):
        """Side whose clean cover costs the book most (None when balanced)."""
        a, b = sides
        lo, hi = self.clean()
        # high outcomes = home margin large (home covers every number) / total high (every over wins)
        if hi == lo:
            return None
        return a if hi < lo else b
