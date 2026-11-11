"""
intel_v7/convergence.py -- module D: do professional and retail prices subsequently move toward one another?

Walk-forward only: a card at time t uses games whose KICKOFF is before t (their closes were observable by then).
For each sport x market x horizon, regress (through the origin) each side's move from the decision fetch to the
close on the gap at the decision fetch (gap = pro fair - retail fair, points):
  retail_toward_pro = sum(gap * retail_move) / sum(gap^2)     1 = retail closes the whole gap
  pro_toward_retail = sum(-gap * pro_move)  / sum(gap^2)      1 = the professional price closes it
95% intervals by bootstrap over games. Pinnacle is NOT assumed to lead; the data decide, per sport and market.
"""
import bisect
import random

from intel_v7.config import CONFIG
from intel_v7.history import view

HORIZONS = {"early": 6 * 3600, "late": 3600}          # decision fetch at <= kickoff - this


def horizon_for(hours_to_kick):
    return "early" if hours_to_kick >= 3 else "late"


def pair(hist, horizon):
    """(gap, retail_move, pro_move) for one finished game-market, or None. Uses the same operator at both ends."""
    if hist.kickoff is None:
        return None
    td = hist.kickoff - HORIZONS[horizon]
    fd, bd = hist.snap_at(td)
    fc, bc = hist.snap_at(hist.kickoff - 1)
    if fd is None or fc is None or fc <= fd or td - fd > CONFIG["max_decision_fetch_age_s"] or hist.kickoff - fc > 3 * 3600:
        return None
    vd, vc = view(bd), view(bc)
    if "retail_mu" not in vd or "retail_mu" not in vc or "pro_mu" not in vd:
        return None
    op = vd["pro_basis"]
    if op not in vc["pro"]:
        return None
    return (vd["pro_mu"] - vd["retail_mu"], vc["retail_mu"] - vd["retail_mu"], vc["pro"][op] - vd["pro"][op])


class Convergence:
    def __init__(self):
        self.rows = {}        # (sport, market, horizon) -> sorted [(kickoff, gap, rmove, pmove)]
        self._cache = {}

    def add(self, sport, market, kickoff, horizon, p):
        if p is None:
            return
        lst = self.rows.setdefault((sport, market, horizon), [])
        bisect.insort(lst, (kickoff, *p))
        self._cache.clear()

    def add_history(self, hist):
        for h in HORIZONS:
            self.add(hist.sport, hist.market, hist.kickoff, h, pair(hist, h))

    def stats(self, sport, market, horizon, before):
        lst = self.rows.get((sport, market, horizon), [])
        n = bisect.bisect_left(lst, (before,))
        key = (sport, market, horizon, n)
        if key in self._cache:
            return self._cache[key]
        rows = [r for r in lst[:n] if abs(r[1]) > 1e-9]
        out = {"sport": sport, "market": market, "horizon": horizon, "games": len(rows), "basis": "games kicked off before the decision"}
        if len(rows) < CONFIG["convergence_min_games"]:
            out.update(status="INSUFFICIENT", reason=f"fewer than {CONFIG['convergence_min_games']} prior games")
            self._cache[key] = out
            return out
        r, p = _slopes(rows)
        rng = random.Random(f"{sport}{market}{horizon}{n}")
        bs = [_slopes([rows[rng.randrange(len(rows))] for _ in rows]) for _ in range(CONFIG["convergence_boot"])]
        rs, ps = sorted(x[0] for x in bs), sorted(x[1] for x in bs)
        lo, hi = int(0.025 * len(bs)), int(0.975 * len(bs)) - 1
        rci, pci = (rs[lo], rs[hi]), (ps[lo], ps[hi])
        if rci[0] > 0 and pci[0] > 0:
            st = "TWO_WAY"
        elif rci[0] > 0:
            st = "PRO_LEADS"
        elif pci[0] > 0:
            st = "RETAIL_LEADS"
        else:
            st = "UNRESOLVED"
        out.update(status=st, retail_toward_pro=round(r, 3), retail_toward_pro_ci=[round(x, 3) for x in rci],
                   pro_toward_retail=round(p, 3), pro_toward_retail_ci=[round(x, 3) for x in pci])
        self._cache[key] = out
        return out


def _slopes(rows):
    s = sum(g * g for _, g, _, _ in rows) or 1e-9
    return sum(g * rm for _, g, rm, _ in rows) / s, sum(-g * pm for _, g, _, pm in rows) / s
