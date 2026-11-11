"""
lab_v4/bettors.py -- individual synthetic-bettor profitability and FADE/TAIL walk-forward test.  RESEARCH.

Inputs: a wager-level ledger (lab_v4.ledger; production engine = configuration A) and the market store.
Every bet is graded at its ACTUAL accepted price. Tail/fade bets are graded at the BEST OBSERVED EXECUTABLE price
of the latest collected snapshot at or before the bettor's wager time (real books; the opposite side at its own
number and price -- never assumed at the bettor's price). Market expectation: the market makers' no-vig fair at the
close (spreads/totals: push- and key-number-aware normaliser; moneyline: power de-vig).

Skill measure per bettor: EXCESS = actual units - expected units at the decision-time fair (per $ staked), shrunk
toward 0 with an empirical-Bayes factor estimated from the cross-section (method of moments).
"""
import bisect
import collections
import math
import random
import statistics as st

from intel_v7.classify import best_price
from intel_v7.history import History, view
from lab_v4.moneyline import close_quote, devig
from need_v6.market import normalizer
from sportsbook_v4.market import profit_on_win

SIDE_OPP = {"home": "away", "away": "home", "over": "under", "under": "over"}


# ------------------------------------------------------------------------------------------------ flatten
def flatten(led):
    """[wager] with week index per sport, side type and buckets."""
    out = []
    wk_idx = collections.Counter()
    kick = {(s, e): k for s, e, k, _, _ in led["games"]}
    for wk in led["weeks"]:
        i = wk_idx[wk["sport"]]
        wk_idx[wk["sport"]] += 1
        for w in wk["wagers"]:
            x = dict(w, week=i, kick=kick[(wk["sport"], w["canonical_game_id"])])
            x["side_type"] = side_type(x)
            x["units"] = x["customer_pnl"] / x["stake"]
            p = x.get("p_fair_decision")
            x["exp_units"] = None if p is None else p * profit_on_win(1.0, x["accepted_price"]) - (1 - p)
            x["hours"] = (x["kick"] - x["t"]) / 3600
            out.append(x)
    return out


def side_type(w):
    if w["market"] == "total":
        return w["side"]
    if w["market"] == "moneyline":
        return "favourite" if w["accepted_price"] < 0 else "underdog"
    return "favourite" if w["accepted_line"] < 0 else "underdog" if w["accepted_line"] > 0 else "pick"


def buckets(w):
    b = {"sport": w["sport"], "market": w["market"], "side": w["side_type"], "archetype": w["archetype"]}
    if w["market"] == "spread":
        a = abs(w["accepted_line"])
        b["spread_range"] = "0-3" if a <= 3 else "3.5-7" if a <= 7 else "7.5-14" if a <= 14 else "14.5+"
    if w["market"] == "moneyline":
        p = w["accepted_price"]
        b["ml_odds"] = ("<=-300" if p <= -300 else "-299..-101" if p < 0 else "+100..+299" if p < 300 else
                        "+300..+999" if p < 1000 else ">=+1000")
    h = w["hours"]
    b["timing"] = ">72h" if h > 72 else "24-72h" if h > 24 else "6-24h" if h > 6 else "1-6h" if h > 1 else "<1h"
    s = w["stake"]
    b["stake"] = "<$50" if s < 50 else "$50-499" if s < 500 else "$500-1,999" if s < 2000 else "$2,000+"
    return b


# ------------------------------------------------------------------------------------------------ Phase 1
def bettor_table(ws):
    """{uid: summary} + long breakdown rows."""
    by = collections.defaultdict(list)
    for w in ws:
        by[w["bettor_uid"]].append(w)
    summ, long = {}, []
    for uid, xs in by.items():
        summ[uid] = summarize(xs, uid)
        groups = collections.defaultdict(list)
        for w in xs:
            for dim, val in buckets(w).items():
                groups[(dim, val)].append(w)
        for (dim, val), g in groups.items():
            s = summarize(g, uid)
            long.append({"bettor_uid": uid, "dimension": dim, "bucket": val, **{k: s[k] for k in
                         ("wagers", "record", "staked", "pnl", "roi", "excess_units", "expected_pnl")}})
    return summ, long


def summarize(xs, uid=None):
    w = sum(x["result"] == "win" for x in xs)
    l_ = sum(x["result"] == "loss" for x in xs)
    p = len(xs) - w - l_
    staked = sum(x["stake"] for x in xs)
    pnl = sum(x["customer_pnl"] for x in xs)
    ex = [x for x in xs if x["exp_units"] is not None]
    exp_pnl = sum(x["exp_units"] * x["stake"] for x in ex)
    var = sum(x["stake"] ** 2 * unit_var(x) for x in ex)
    return {"bettor_uid": uid, "archetype": xs[0]["archetype"], "sport": xs[0]["sport"], "wagers": len(xs),
            "record": f"{w}-{l_}-{p}", "staked": round(staked, 2), "pnl": round(pnl, 2), "roi": round(pnl / staked, 4),
            "expected_pnl": round(exp_pnl, 2),
            "excess_units": round((sum(x["customer_pnl"] for x in ex) - exp_pnl) / max(1e-9, sum(x["stake"] for x in ex)), 4),
            "excess_z": round((sum(x["customer_pnl"] for x in ex) - exp_pnl) / math.sqrt(var), 2) if var else None,
            "games": len({x["canonical_game_id"] for x in xs})}


def unit_var(x):
    """Variance of one unit staked at the decision-time fair (push ignored)."""
    p = x["p_fair_decision"]
    win = profit_on_win(1.0, x["accepted_price"])
    m = p * win - (1 - p)
    return p * (win - m) ** 2 + (1 - p) * (-1 - m) ** 2


def shrunk(summaries):
    """Empirical-Bayes shrinkage of excess per $: B = tau^2 / (tau^2 + s_i^2), tau^2 by method of moments."""
    rows = [s for s in summaries if s["excess_z"] is not None and s["excess_units"] is not None]
    if not rows:
        return {}
    se2 = {s["bettor_uid"]: (s["excess_units"] / s["excess_z"]) ** 2 if s["excess_z"] else None for s in rows}
    obs = [s["excess_units"] for s in rows if se2[s["bettor_uid"]]]
    mean_se2 = st.mean(v for v in se2.values() if v)
    tau2 = max(0.0, st.pvariance(obs) - mean_se2)
    out = {}
    for s in rows:
        v = se2[s["bettor_uid"]]
        if not v:
            continue
        B = tau2 / (tau2 + v) if tau2 > 0 else 0.0
        out[s["bettor_uid"]] = {"shrunk_excess": B * s["excess_units"], "B": B}
    return {"tau2": tau2, "mean_se2": mean_se2, "bettors": out}


def split_half(ws):
    """Repeatability: correlation of each bettor's excess in odd vs even weeks (per sport x archetype)."""
    out = {}
    keys = {(w["sport"], w["archetype"]) for w in ws}
    for k in sorted(keys):
        sub = [w for w in ws if (w["sport"], w["archetype"]) == k and w["exp_units"] is not None]
        a, b = collections.defaultdict(lambda: [0.0, 0.0]), collections.defaultdict(lambda: [0.0, 0.0])
        for w in sub:
            d = a if w["week"] % 2 else b
            d[w["bettor_uid"]][0] += w["customer_pnl"] - w["exp_units"] * w["stake"]
            d[w["bettor_uid"]][1] += w["stake"]
        uids = [u for u in a if u in b and a[u][1] and b[u][1]]
        if len(uids) < 20:
            continue
        x = [a[u][0] / a[u][1] for u in uids]
        y = [b[u][0] / b[u][1] for u in uids]
        out[f"{k[0]}|{k[1]}"] = {"bettors": len(uids), "corr": round(_corr(x, y), 3)}
    return out


def _corr(x, y):
    mx, my = st.mean(x), st.mean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx and sy else 0.0


# ------------------------------------------------------------------------------------------------ executable prices
class Prices:
    """Best observed executable price on a side at the latest snapshot at or before t (real books)."""

    def __init__(self, conn, close_mo):
        self.conn, self.mo = conn, close_mo
        self.hist, self.ml = {}, {}

    def _h(self, sport, gid, market):
        k = (sport, gid, market)
        if k not in self.hist:
            self.hist[k] = History(self.conn, sport, gid, market)
        return self.hist[k]

    def _ml(self, gid):
        if gid not in self.ml:
            rows = self.conn.execute("SELECT fetched_at, book, side, price FROM mkt_quote WHERE event_id=? AND market='h2h' "
                                     "AND fetched_at<commence ORDER BY fetched_at", (gid,)).fetchall()
            snaps = collections.OrderedDict()
            for fa, bk, sd, pr in rows:
                snaps.setdefault(fa, {}).setdefault(bk, {})[sd] = pr
            self.ml[gid] = (list(snaps), list(snaps.values()))
        return self.ml[gid]

    def best(self, sport, gid, market, side, t, max_age=6 * 3600):
        if market == "moneyline":
            times, vals = self._ml(gid)
            i = bisect.bisect_right(times, t)
            if not i or t - times[i - 1] > max_age:
                return None
            cands = [(q[side], bk) for bk, q in vals[i - 1].items() if side in q]
            if not cands:
                return None
            pr, bk = max(cands)
            return {"book": bk, "point": None, "price": float(pr)}
        h = self._h(sport, gid, market)
        ft, books = h.snap_at(t)
        if ft is None or t - ft > max_age:
            return None
        ck = (gid, market, side, ft)
        if ck in self.__dict__.setdefault("_bc", {}):
            return self._bc[ck]
        v = view(books)
        mu = v.get("pro_mu", v.get("consensus_mu"))
        if mu is None:
            return None
        bp = best_price(h, t, side, mu)
        self._bc[ck] = None if bp is None else {"book": bp["book"], "point": bp["point"], "price": float(bp["price"])}
        return self._bc[ck]

    def close_fair(self, sport, gid, market, side, point):
        """(p_win, p_push) of side at point under the market makers' close; None if unavailable."""
        if market == "moneyline":
            cq = close_quote(self.conn, gid)
            if not cq:
                return None
            ph = devig(cq[1], cq[2], "power")
            return (ph if side == "home" else 1 - ph), 0.0
        rec = self.mo.get((sport, gid, market))
        if not rec or not rec.get("close"):
            return None
        return normalizer(sport, market).prob_at(rec["close"]["pro_mu"], side, point)


def grade(market, side, point, price, final):
    hs, as_ = final
    if market == "spread":
        v = (hs - as_) + point if side == "home" else (as_ - hs) + point
    elif market == "total":
        v = (hs + as_) - point if side == "over" else point - (hs + as_)
    else:
        v = (hs - as_) if side == "home" else (as_ - hs)
    return profit_on_win(1.0, price) if v > 0 else -1.0 if v < 0 else 0.0


# ------------------------------------------------------------------------------------------------ Phase 3/4: walk-forward
def select(train, n_min, rule, k=15):
    """Freeze a bettor set from training wagers only. rule: (scope_fn, direction, size)."""
    summ = {}
    by = collections.defaultdict(list)
    for w in train:
        if rule["scope"](w):
            by[w["bettor_uid"]].append(w)
    rows = [summarize(xs, u) for u, xs in by.items() if len(xs) >= n_min]
    if len(rows) < 2 * k:
        return set()
    # ranking key (fixed per rule, never tuned): "z" = evidence of excess vs price expectation (default; the
    # empirical-Bayes shrunk estimate is identical in ORDER when tau^2 > 0 and degenerate -- all zero -- when the
    # data show no between-bettor skill variance, which is what 2026 shows), "pnl" = net $, "roi" = ROI
    key = rule.get("rank", "z")

    def score(r):
        if key == "pnl":
            return r["pnl"]
        if key == "roi":
            return r["roi"]
        return r["excess_z"] if r["excess_z"] is not None else 0.0
    ranked = sorted((score(r), r["bettor_uid"]) for r in rows)
    size = k if rule["size"] == "k" else max(k, len(ranked) // 10)
    return {u for _, u in (ranked[:size] if rule["direction"] == "worst" else ranked[-size:])}


def walk_forward(ws, prices, finals, rules, first_eval_week=3, n_min=5, k=15):
    """For each sport and evaluation week W (>= first_eval_week): select bettors on weeks < W only, then grade
    TAIL (same side) and FADE (opposite side) of their week-W wagers in scope, at best observed executable prices."""
    out = []
    for sport in ("nfl", "cfb"):
        sw = [w for w in ws if w["sport"] == sport]
        nweeks = max(w["week"] for w in sw) + 1
        for W in range(first_eval_week, nweeks):
            train = [w for w in sw if w["week"] < W]
            test = [w for w in sw if w["week"] == W]
            for name, rule in rules.items():
                members = select(train, rule.get("n_min", n_min), rule, rule.get("k", k))
                for w in test:
                    if w["bettor_uid"] not in members or not rule["scope"](w):
                        continue
                    out.append({"rule": name, "sport": sport, "week": W, "bettor_uid": w["bettor_uid"],
                                "gid": w["canonical_game_id"], "market": w["market"], "side": w["side"], "t": w["t"],
                                "stake": w["stake"], "bettor_units": w["units"], "group_size": len(members)})
    # grade at executable prices (each distinct selection once per rule/week/direction)
    for r in out:
        f = finals.get((r["sport"], r["gid"]))
        for d, side in (("tail", r["side"]), ("fade", SIDE_OPP[r["side"]])):
            bp = prices.best(r["sport"], r["gid"], r["market"], side, r["t"])
            if bp is None or f is None:
                r[d] = None
                continue
            pt = bp["point"]
            u = grade(r["market"], side, pt, bp["price"], f)
            cf = prices.close_fair(r["sport"], r["gid"], r["market"], side, pt)
            exp = None if cf is None else cf[0] * profit_on_win(1.0, bp["price"]) - (1 - cf[0] - cf[1])
            var = None
            if cf is not None:
                pw, pp = cf
                win = profit_on_win(1.0, bp["price"])
                var = pw * (win - exp) ** 2 + (1 - pw - pp) * (-1 - exp) ** 2 + pp * exp ** 2
            r[d] = {"side": side, "point": pt, "price": bp["price"], "units": u, "exp": exp, "var": var}
    return out


def evaluate(rows, direction, dedupe=True):
    """Unique selections (rule, sport, week, game-market, side) count once: correlated bettors are not independent."""
    items = {}
    for r in rows:
        g = r.get(direction)
        if not g:
            continue
        key = (r["sport"], r["week"], r["gid"], r["market"], g["side"]) if dedupe else id(r)
        if key not in items:
            items[key] = (r["gid"], g, 1)
        else:
            items[key] = (items[key][0], items[key][1], items[key][2] + 1)
    xs = list(items.values())
    n = len(xs)
    if not n:
        return {"n": 0}
    units = [g["units"] for _, g, _ in xs]
    w = sum(u > 0 for u in units)
    l_ = sum(u < 0 for u in units)
    roi = sum(units) / max(1, w + l_)
    ex = [(g["units"], g["exp"], g["var"]) for _, g, _ in xs if g["exp"] is not None]
    clv = st.mean(e for _, e, _ in ex) if ex else None
    z = (sum(u - e for u, e, _ in ex) / math.sqrt(sum(v for _, _, v in ex))) if ex and sum(v for _, _, v in ex) else None
    # game-clustered bootstrap CI for ROI
    by = collections.defaultdict(list)
    for gid, g, _ in xs:
        by[gid].append(g["units"])
    keys = list(by)
    rng = random.Random(5)
    bs = []
    for _ in range(500):
        s = [u for kk in (keys[rng.randrange(len(keys))] for _ in keys) for u in by[kk]]
        d = sum(1 for u in s if u != 0)
        bs.append(sum(s) / d if d else 0.0)
    bs.sort()
    p_one = 0.5 * math.erfc((z or 0) / math.sqrt(2)) if z is not None else None     # one-sided P(Z >= z)
    return {"n": n, "games": len(keys), "record": f"{w}-{l_}-{n - w - l_}", "roi": roi,
            "roi_ci": [bs[12], bs[487]], "clv_at_close": clv, "excess_z": z, "p_one_sided": p_one,
            "units": sum(units)}


def bh(pvals, q=0.10):
    """Benjamini-Hochberg: {name: significant} at FDR q."""
    items = sorted((p, n) for n, p in pvals.items() if p is not None)
    m = len(items)
    cut = 0
    for i, (p, _) in enumerate(items, 1):
        if p <= q * i / m:
            cut = i
    return {n: i < cut for i, (p, n) in enumerate(items)}
