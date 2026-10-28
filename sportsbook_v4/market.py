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
import hashlib
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
    total: float | None = None
    over_price: float | None = None
    under_price: float | None = None
    home_ml: float | None = None
    away_ml: float | None = None


def _lower_median(sorted_values):
    n = len(sorted_values)
    return sorted_values[n // 2] if n % 2 == 1 else sorted_values[n // 2 - 1]


# ---------------------------------------------------------------- the synthetic book's offered market
#
# The book re-quotes the market as ONE coherent two-way quote (line and both prices from the same real
# sportsbook), chosen side-neutrally:
#   line   the median line across books; when the two middle values differ, the line offered by more
#          books, then the one nearer the mean; an exact tie is broken by a hash of the quote set, so
#          no side is systematically favoured (the previous rule always took the LOWER middle value:
#          fewer points for the home side, a lower total for the over).
#   prices of the books on that line, the one with the median no-vig probability; with two middle
#          books, the higher-margin quote (a retail book prices to its own margin), then by book name.
# Mirroring a market (home <-> away, over <-> under) mirrors the offer, apart from exact-tie coin flips.

def _novig_first(p_a, p_b):
    pa, pb = implied_probability(p_a), implied_probability(p_b)
    return pa / (pa + pb), pa + pb - 1.0


def _pick_line(values, quotes):
    vals = sorted(values)
    n = len(vals)
    if n % 2 == 1:
        return vals[n // 2]
    a, b = vals[n // 2 - 1], vals[n // 2]
    if a == b:
        return a
    ca, cb = vals.count(a), vals.count(b)
    if ca != cb:
        return a if ca > cb else b
    mean = sum(vals) / n
    if abs(a - mean) != abs(b - mean):
        return a if abs(a - mean) < abs(b - mean) else b
    h = hashlib.sha256("|".join(sorted(f"{q.book}:{q.ref}" for q in quotes)).encode()).digest()
    return (a, b)[h[0] % 2]


def _pick_book(cands):
    """cands: [(book, price_a, price_b)] at one line -> the representative (book, price_a, price_b) or None."""
    rows = []
    for book, pa, pb in cands:
        try:
            p, margin = _novig_first(pa, pb)
        except (InvalidPrice, ZeroDivisionError):
            continue
        rows.append((p, margin, book, pa, pb))
    if not rows:
        return None
    rows.sort(key=lambda r: (r[0], r[2]))
    n = len(rows)
    if n % 2 == 1:
        r = rows[n // 2]
    else:
        r = max(rows[n // 2 - 1], rows[n // 2], key=lambda x: (x[1], x[2]))
    return r[2], r[3], r[4]


def _subset(usable, books):
    if books:
        subset = [q for q in usable if q.book in set(books)]
        if subset:
            return subset
    return usable


def representative_offer(quotes, books=None):
    """
    quotes: latest Quote per book (as returned by MarketSource.latest_quotes).
    books:  optional subset of books to use; falls back to all books when no
            quote from the subset exists.
    Returns {home_spread, home_price, away_price, price_book, books, refs, book_count}
    or None when no book has a usable spread.
    """
    usable = _subset([q for q in quotes if q.home_spread is not None], books)
    if not usable:
        return None
    rep = _pick_line([float(q.home_spread) for q in usable], usable)
    matching = sorted((q for q in usable if float(q.home_spread) == rep), key=lambda q: q.book)
    pick = _pick_book([(q.book, q.home_price, q.away_price) for q in matching])
    return {
        "home_spread": rep,
        "home_price": pick[1] if pick else None,
        "away_price": pick[2] if pick else None,
        "price_book": pick[0] if pick else None,
        "books": [q.book for q in matching],
        "refs": [q.ref for q in matching],
        "book_count": len(usable),
    }


def representative_total_offer(quotes, books=None):
    """Same construction as representative_offer, for the game total."""
    usable = _subset([q for q in quotes if q.total is not None], books)
    if not usable:
        return None
    rep = _pick_line([float(q.total) for q in usable], usable)
    matching = sorted((q for q in usable if float(q.total) == rep), key=lambda q: q.book)
    pick = _pick_book([(q.book, q.over_price, q.under_price) for q in matching])
    return {"total": rep, "over_price": pick[1] if pick else None, "under_price": pick[2] if pick else None,
            "price_book": pick[0] if pick else None,
            "books": [q.book for q in matching], "refs": [q.ref for q in matching], "book_count": len(usable)}


def representative_ml_offer(quotes, books=None):
    usable = sorted(_subset([q for q in quotes if q.home_ml is not None and q.away_ml is not None], books),
                    key=lambda q: q.book)
    if not usable:
        return None
    pick = _pick_book([(q.book, q.home_ml, q.away_ml) for q in usable])
    return {"home_price": pick[1] if pick else None, "away_price": pick[2] if pick else None,
            "price_book": pick[0] if pick else None,
            "books": [q.book for q in usable], "refs": [q.ref for q in usable], "book_count": len(usable)}


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


def quote(book, ts, home_spread, home_price=-110, away_price=-110, ref=None,
          total=None, over_price=-110, under_price=-110, home_ml=None, away_ml=None):
    """Fixture convenience constructor."""
    return Quote(book=book, effective_epoch=to_epoch(ts),
                 home_spread=None if home_spread is None else float(home_spread),
                 home_price=home_price, away_price=away_price,
                 ref=ref or f"fx:{book}:{parse_ts(ts).isoformat()}",
                 total=None if total is None else float(total),
                 over_price=over_price if total is not None else None,
                 under_price=under_price if total is not None else None,
                 home_ml=home_ml, away_ml=away_ml)


class MarketV4Source(MarketSource):
    """
    Adapter over the V4 market store (market_v4.store / mkt_quote): spreads,
    totals and moneylines from real provider snapshots.

    FRESHNESS: latest_quotes(game, t) returns, per book, the latest
    observation at or before t ONLY if it was fetched within max_age_seconds
    of t. During a provider outage every quote goes stale, so the synthetic
    book has no open market and accepts no wagers -- simulated customers
    never bet into prices nobody could see.
    game ids are the provider event ids used in mkt_quote.
    """

    def __init__(self, conn, max_age_seconds=45 * 60, source_id=None):
        self.conn, self.max_age = conn, max_age_seconds
        self.source_id = source_id or f"market_v4:max_age={max_age_seconds}"
        self._cache = {}

    def _load(self, game_id):
        """All observations of a game, once: {(book, market): (fetch_times, [(t, mlu, {side: (price, point)})])}."""
        if game_id not in self._cache:
            rows = self.conn.execute(
                "SELECT book, market, side, point, price, fetched_at, market_last_update FROM mkt_quote "
                "WHERE event_id=? ORDER BY fetched_at", (game_id,)).fetchall()
            series = {}
            for book, market, side, point, price, fa, mlu in rows:
                lst = series.setdefault((book, market), [])
                if not lst or lst[-1][0] != fa:
                    lst.append((fa, mlu, {}))
                lst[-1][2][side] = (price, point)
            self._cache[game_id] = {k: ([x[0] for x in v], v) for k, v in series.items()}
        return self._cache[game_id]

    def latest_quotes(self, game_id, as_of_epoch):
        books = {}
        for (book, market), (times, obs) in self._load(game_id).items():
            i = bisect.bisect_right(times, as_of_epoch) - 1
            while i >= 0 and obs[i][1] > as_of_epoch:      # book-reported update after T: not visible yet
                i -= 1
            if i < 0 or as_of_epoch - obs[i][0] > self.max_age:
                continue
            fa, _mlu, sides = obs[i]
            b = books.setdefault(book, {"t": fa, "refs": set()})
            b["t"] = max(b["t"], fa)
            b["refs"].add(f"mkt:{book}:{market}:{int(fa)}")
            for side, v in sides.items():
                b[(market, side)] = v
        out = []
        for book, b in sorted(books.items()):
            hs = b.get(("spreads", "home"))
            aw = b.get(("spreads", "away"))
            ov, un = b.get(("totals", "over")), b.get(("totals", "under"))
            hm, am = b.get(("h2h", "home")), b.get(("h2h", "away"))
            out.append(Quote(book=book, effective_epoch=int(b["t"]),
                             home_spread=hs[1] if hs and aw else None,
                             home_price=hs[0] if hs and aw else None, away_price=aw[0] if hs and aw else None,
                             ref="|".join(sorted(b["refs"])),
                             total=ov[1] if ov and un else None,
                             over_price=ov[0] if ov and un else None, under_price=un[0] if ov and un else None,
                             home_ml=hm[0] if hm and am else None, away_ml=am[0] if hm and am else None))
        return out

    def first_quote_epoch(self, game_id):
        r = self.conn.execute("SELECT MIN(fetched_at) FROM mkt_quote WHERE event_id=?", (game_id,)).fetchone()
        return None if not r or r[0] is None else int(r[0])
