"""
sportsbook_v4/market.py -- point-in-time market access.

A MarketSource answers exactly one question: "what had each book quoted
for this game as of time t?" -- never anything after t. The engine builds
its own offered number from that (lower-median across books, same method
as simulation/offered_line.py and book_need/signal_line.py) and never
fabricates a price.

Two implementations:
  * FixtureMarket   -- in-memory, for tests and diagnostics.
  * SnapshotMarket  -- reads a production-shaped `market_snapshots` table
                       through a READ-ONLY sqlite connection (mode=ro).
                       Effective time = book_last_update if present, else
                       observed_at (same rule as
                       features/market_state_as_of.py).
"""

import bisect
import sqlite3
from dataclasses import dataclass, field

from sportsbook_v4.util import parse_ts, to_epoch


class InvalidPrice(ValueError):
    pass


def implied_probability(price):
    if price is None:
        raise InvalidPrice("price is None")
    price = float(price)
    if -100 < price < 100:
        raise InvalidPrice(f"{price} is not a legitimate American price")
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def profit_on_win(stake, price):
    implied_probability(price)  # validates
    price = float(price)
    return stake * price / 100.0 if price > 0 else stake * 100.0 / -price


@dataclass(frozen=True)
class Quote:
    book: str
    effective_epoch: int
    home_spread: float
    home_price: float | None
    away_price: float | None
    ref: str  # snapshot id / fixture id -- provenance only


def _lower_median(sorted_values):
    n = len(sorted_values)
    return sorted_values[n // 2] if n % 2 == 1 else sorted_values[n // 2 - 1]


def _representative_price(matching, side):
    cands = []
    for q in matching:
        price = q.home_price if side == "home" else q.away_price
        try:
            cands.append((implied_probability(price), q.book, price))
        except InvalidPrice:
            continue
    if not cands:
        return None
    cands.sort()
    return _lower_median(cands)[2]


def representative_offer(quotes, books=None):
    """
    quotes: latest Quote per book (as returned by MarketSource.latest_quotes).
    books:  optional subset of books to use; falls back to all books when no
            quote from the subset exists.
    Returns {home_spread, home_price, away_price, books, refs, book_count}
    or None when no book has a usable spread.
    """
    usable = [q for q in quotes if q.home_spread is not None]
    if books:
        subset = [q for q in usable if q.book in set(books)]
        if subset:
            usable = subset
    if not usable:
        return None
    rep = _lower_median(sorted(float(q.home_spread) for q in usable))
    matching = sorted((q for q in usable if float(q.home_spread) == rep), key=lambda q: q.book)
    return {
        "home_spread": rep,
        "home_price": _representative_price(matching, "home"),
        "away_price": _representative_price(matching, "away"),
        "books": [q.book for q in matching],
        "refs": [q.ref for q in matching],
        "book_count": len(usable),
    }


class MarketSource:
    source_id = "abstract"

    def latest_quotes(self, game_id, as_of_epoch):
        """Latest quote per book with effective time <= as_of_epoch."""
        raise NotImplementedError

    def first_quote_epoch(self, game_id):
        raise NotImplementedError


@dataclass
class FixtureMarket(MarketSource):
    """quotes: {game_id: [Quote, ...]} in any order."""
    quotes: dict = field(default_factory=dict)
    source_id: str = "fixture"

    def __post_init__(self):
        # {game_id: {book: (quotes sorted by time, their epochs)}}
        self._index = {}
        for gid, qs in self.quotes.items():
            by_book = {}
            for q in qs:
                by_book.setdefault(q.book, []).append(q)
            self._index[gid] = {}
            for book, lst in by_book.items():
                lst = sorted(lst, key=lambda q: (q.effective_epoch, q.ref))
                self._index[gid][book] = (lst, [q.effective_epoch for q in lst])

    def latest_quotes(self, game_id, as_of_epoch):
        out = []
        for book, (lst, epochs) in sorted(self._index.get(game_id, {}).items()):
            i = bisect.bisect_right(epochs, as_of_epoch)
            if i:
                out.append(lst[i - 1])
        return out

    def first_quote_epoch(self, game_id):
        firsts = [epochs[0] for _, (lst, epochs) in self._index.get(game_id, {}).items() if epochs]
        return min(firsts) if firsts else None


class SnapshotMarket(MarketSource):
    """
    Read-only adapter over a production-shaped market_snapshots table.
    Opens the database with mode=ro so it is structurally incapable of
    writing. Loads each game's snapshots once and answers as-of queries
    in memory.
    """

    def __init__(self, market_db_path, source_id=None):
        self.conn = sqlite3.connect(f"file:{market_db_path}?mode=ro", uri=True)
        self.source_id = source_id or f"market_snapshots:{market_db_path}"
        self._cache = {}

    def _load(self, game_id):
        if game_id not in self._cache:
            rows = self.conn.execute(
                "SELECT snapshot_id, book, observed_at, book_last_update, spread_home, "
                "spread_price_home, spread_price_away FROM market_snapshots "
                "WHERE canonical_game_id = ? AND spread_home IS NOT NULL",
                (game_id,),
            ).fetchall()
            quotes = [
                Quote(book=book, effective_epoch=to_epoch(blu or obs), home_spread=float(sh),
                      home_price=ph, away_price=pa, ref=f"snapshot:{sid}")
                for sid, book, obs, blu, sh, ph, pa in rows
            ]
            self._cache[game_id] = FixtureMarket({game_id: quotes}, source_id=self.source_id)
        return self._cache[game_id]

    def latest_quotes(self, game_id, as_of_epoch):
        return self._load(game_id).latest_quotes(game_id, as_of_epoch)

    def first_quote_epoch(self, game_id):
        return self._load(game_id).first_quote_epoch(game_id)

    def close(self):
        self.conn.close()


def quote(book, ts, home_spread, home_price=-110, away_price=-110, ref=None):
    """Fixture convenience constructor."""
    return Quote(book=book, effective_epoch=to_epoch(ts), home_spread=float(home_spread),
                 home_price=home_price, away_price=away_price,
                 ref=ref or f"fx:{book}:{parse_ts(ts).isoformat()}")
