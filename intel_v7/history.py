"""
intel_v7/history.py -- point-in-time history of one game-market from the append-only quote store.

Every quote is normalised (need_v6.normalize: push-aware, key-number aware for the NFL) to an implied fair mean:
expected HOME MARGIN for spreads, expected TOTAL for totals. Snapshots are per FETCH: a book is in a snapshot only if
it was quoted in that fetch (fresh). Nothing after the requested time is ever visible.
"""
import bisect
import functools
import statistics as st

from intel_v7.config import CONFIG
from need_v6.market import normalizer

SPORT_KEY = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}
MK = {"spread": "spreads", "total": "totals"}
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}


@functools.lru_cache(maxsize=500_000)
def _mu(sport, market, pa_point, pa_price, pb_point, pb_price):
    a, b = SIDES[market]
    z = normalizer(sport, market).quote(a, pa_point, pa_price, b, pb_point, pb_price)
    return None if z is None else z["mu"]


def operator_of(book):
    return CONFIG["operators"].get(book, book)


class History:
    def __init__(self, conn, sport, event_id, market, until=None):
        self.sport, self.event_id, self.market = sport, event_id, market
        a, b = SIDES[market]
        q = ("SELECT fetched_at, book, side, point, price, market_last_update, commence, home, away FROM mkt_quote "
             "WHERE event_id=? AND market=?" + (" AND fetched_at<=?" if until is not None else "") + " ORDER BY fetched_at")
        args = (event_id, MK[market]) + ((until,) if until is not None else ())
        raw = {}
        self.kickoff = self.home = self.away = None
        for fa, bk, sd, pt, pr, lu, cm, h, aw in conn.execute(q, args):
            if fa >= cm:
                continue                                    # in-play / post-kickoff quotes are never used
            self.kickoff, self.home, self.away = cm, h, aw
            raw.setdefault(fa, {}).setdefault(bk, {})[sd] = (pt, pr, lu)
        self.snaps = []                                     # [(fetched_at, {book: quote})]
        for fa in sorted(raw):
            books = {}
            for bk, sd in raw[fa].items():
                if a in sd and b in sd:
                    (pa, xa, lu), (pb, xb, _) = sd[a], sd[b]
                    mu = _mu(sport, market, pa, xa, pb, xb)
                    books[bk] = {"point": pa, "price_a": xa, "price_b": xb, "mu": mu, "last_update": lu, "fetched_at": fa}
            if books:
                self.snaps.append((fa, books))
        self.times = [t for t, _ in self.snaps]

    def snap_at(self, t):
        """Latest fetch at or before t: (fetched_at, books) or (None, {})."""
        i = bisect.bisect_right(self.times, t)
        return self.snaps[i - 1] if i else (None, {})

    def snaps_between(self, t0, t1):
        i, j = bisect.bisect_left(self.times, t0), bisect.bisect_right(self.times, t1)
        return self.snaps[i:j]


def view(books):
    """Operator-level view of one fetch: pro operators (one value each), retail consensus, dispersion."""
    ops = {}
    for bk, q in books.items():
        if q["mu"] is None:
            continue
        ops.setdefault(operator_of(bk), []).append(q["mu"])
    pro = {op: st.median(v) for op, v in ops.items() if op in CONFIG["pro_operators"]}
    retail = [st.median(ops[b]) for b in CONFIG["retail_books"] if b in ops]
    out = {"pro": pro, "retail_n": len(retail), "retail_books": sorted(b for b in CONFIG["retail_books"] if b in ops)}
    if len(retail) >= CONFIG["min_retail_books"]:
        med = st.median(retail)
        out["retail_mu"] = med
        out["retail_disp"] = max(CONFIG["min_dispersion_pts"], st.median([abs(x - med) for x in retail]))
    # professional fair: Pinnacle when quoted in this fetch, else the BetOnline group
    if "pinnacle" in pro:
        out["pro_mu"], out["pro_basis"] = pro["pinnacle"], "pinnacle"
    elif "betonline_group" in pro:
        out["pro_mu"], out["pro_basis"] = pro["betonline_group"], "betonline_group"
    allops = [st.median(v) for v in ops.values()]
    out["consensus_mu"] = st.median(allops) if allops else None
    return out


def games(conn, sport, start=None, end=None):
    """[(event_id, kickoff, home, away)] of every game with spread/total quotes, kickoff in [start, end)."""
    # SQLite returns the bare columns of the row holding MAX(fetched_at): the LATEST reported kickoff and names
    q = ("SELECT event_id, commence, home, away, MAX(fetched_at) FROM mkt_quote WHERE sport=? "
         "AND market IN ('spreads','totals') AND fetched_at < commence GROUP BY event_id")
    rows = [(e, k, h, a) for e, k, h, a, _ in conn.execute(q, [SPORT_KEY[sport]])]
    return sorted([r for r in rows if (start is None or r[1] >= start) and (end is None or r[1] < end)], key=lambda r: (r[1], r[0]))
