"""
need_v6/market.py -- point-in-time view of the collected external market for one game (spreads and totals).

Only quotes with fetched_at <= t are ever visible at time t (no look-ahead). Each book's latest quote counts only
while it is fresh (CONFIG max_quote_age_s); a market with no fresh market-maker quote is STALE.
Fair value = Pinnacle's no-vig implied mean when Pinnacle is fresh, else the median of the BetOnline group
(BetOnline/LowVig: one operator, flagged "maker proxy"). Uncertainty = dispersion of all fresh books' implied means.
"""

import bisect
import statistics as st

from need_v6 import normalize as NZ
from need_v6.config import CONFIG

MAKERS = ("pinnacle", "betonlineag", "lowvig")
REC = ("draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "espnbet", "fanatics")
OWNER = ("fanatics", "betonlineag", "lowvig")
MK = {"spread": "spreads", "total": "totals"}
SIDES = {"spread": ("home", "away"), "total": ("over", "under")}

_NORM = {}


def normalizer(sport, market):
    if not _NORM:
        nm, nt = NZ.nfl()
        cm, ct = NZ.cfb_plain()
        _NORM.update({("nfl", "spread"): nm, ("nfl", "total"): nt, ("cfb", "spread"): cm, ("cfb", "total"): ct})
    return _NORM[(sport, market)]


class GameMarket:
    """All collected quotes of one game-market, preloaded; queries are point-in-time."""

    def __init__(self, conn, sport, event_id, market, kickoff):
        self.sport, self.event_id, self.market, self.kickoff = sport, event_id, market, kickoff
        self.nz = normalizer(sport, market)
        a, b = SIDES[market]
        rows = conn.execute("SELECT fetched_at, book, side, point, price FROM mkt_quote WHERE event_id=? AND market=? "
                            "AND fetched_at < ? ORDER BY fetched_at", (event_id, MK[market], kickoff)).fetchall()
        snaps = {}
        for fa, bk, sd, pt, pr in rows:
            snaps.setdefault((fa, bk), {})[sd] = (pt, pr)
        self.by_book = {}
        for (fa, bk), sd in sorted(snaps.items()):
            if a in sd and b in sd:
                z = self.nz.quote(a, sd[a][0], sd[a][1], b, sd[b][0], sd[b][1])
                self.by_book.setdefault(bk, []).append((fa, sd[a][0], sd[a][1], sd[b][1], z["mu"] if z else None))
        self.times = sorted({fa for v in self.by_book.values() for fa, *_ in v})
        self._cache = {}

    def obs_time(self, t):
        """Latest observation time <= t (None before the first)."""
        i = bisect.bisect_right(self.times, t)
        return self.times[i - 1] if i else None

    def state(self, t):
        """Market state at t, computed at the latest observation <= t: {status, fair_mu, uncertainty, line,
        basis, rec_mu, books, fresh, obs_at}. Books count only if fresh at that observation; if the observation
        itself is older than CONFIG max_quote_age_s at t, the market is STALE (the desk holds its price)."""
        ot = self.obs_time(t)
        if ot is None:
            return {"status": "NO_DATA"}
        if ot not in self._cache:
            self._cache[ot] = self._state_at(ot)
        s = self._cache[ot]
        if t - ot > CONFIG["max_quote_age_s"]:
            return {**s, "status": "STALE", "fair_mu": None}
        return s

    def _state_at(self, ot):
        books = {}
        for bk, seq in self.by_book.items():
            i = bisect.bisect_right(self._times_of(bk), ot)
            if i:
                fa, pt, pa, pb, mu = seq[i - 1]
                books[bk] = {"at": fa, "point": pt, "pa": pa, "pb": pb, "mu": mu}
        s = {"obs_at": ot, "books": books}
        fresh = {k: v for k, v in books.items() if ot - v["at"] <= CONFIG["max_quote_age_s"] and v["mu"] is not None}
        s["fresh"] = fresh
        pin = fresh.get("pinnacle")
        proxy = [v["mu"] for k, v in fresh.items() if k in ("betonlineag", "lowvig")]
        if pin:
            s.update(fair_mu=pin["mu"], basis="pinnacle", line=pin["point"])
        elif proxy:
            lines = sorted(v["point"] for k, v in fresh.items() if k in ("betonlineag", "lowvig"))
            s.update(fair_mu=st.median(proxy), basis="maker proxy (BetOnline group)", line=lines[0])
        else:
            s.update(status="STALE" if books else "NO_DATA", fair_mu=None, basis=None, line=None)
            return s
        mus = [v["mu"] for v in fresh.values()]
        mad = st.median([abs(x - s["fair_mu"]) for x in mus]) if mus else 0.0
        s["uncertainty"] = max(CONFIG["min_uncertainty_pts"], round(mad, 3))
        rec = [v["mu"] for k, v in fresh.items() if k in REC]
        s["rec_mu"] = st.median(rec) if rec else None
        s["status"] = "OK"
        return s

    def _times_of(self, bk):
        c = self.__dict__.setdefault("_tcache", {})
        if bk not in c:
            c[bk] = [x[0] for x in self.by_book[bk]]
        return c[bk]

    def fair_prob(self, mu, side, point):
        """(P(win), P(push)) of `side` at its own `point` given implied mean mu."""
        return self.nz.prob_at(mu, side, point)

    def side_point(self, side, line):
        """The side's own number from the home spread / total `line`."""
        if self.market == "spread":
            return line if side == "home" else -line
        return line


def pmf_on_grid(gm, mu, market):
    """Fair outcome distribution (implied mean mu) on the exposure grid of book.py."""
    import numpy as np
    from need_v6.book import GRID
    nz = gm.nz
    i = int(np.clip(round((mu - nz.mus[0]) / 0.05), 0, len(nz.mus) - 1))
    row = nz.pmf[i]
    grid = GRID[market]
    out = np.zeros(len(grid))
    idx = (nz.values - grid[0]).astype(int)
    ok = (idx >= 0) & (idx < len(grid))
    out[idx[ok]] = row[ok]
    s = out.sum()
    return out / s if s else out
