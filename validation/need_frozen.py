"""
validation/need_frozen.py -- grade the GENUINE pre-kickoff Financial NEED records (production Need tracker, releases
v4-need-YYYY-Www) at real historical prices.  Research; nothing here changes production.

Per game: the LAST record taken before kickoff (sportsbook_v5.r2 only; other engine versions reported, not pooled).
The NEED side (spread) is graded:
  BOOK    at the synthetic book's own recorded offer (a real retail book's line and price at that moment)
  PIN     at Pinnacle's own line and price in the latest collected snapshot at or before the record time
Market expectation: Pinnacle's no-vig fair at the last snapshot before kickoff (push- and key-number aware);
CLV = EV of the graded price at that close fair.
"""
import collections
import glob
import gzip
import json
import math
import os

from intel_v7.history import History
from lab_v4.bettors import grade
from need_v6.market import normalizer
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch


def load(need_dir):
    best, other = {}, collections.Counter()
    for p in glob.glob(os.path.join(need_dir, "need_*.json.gz")):
        for r in json.load(gzip.open(p, "rt"))["records"]:
            if not (r.get("taken_at") and r.get("kickoff") and r["taken_at"] < r["kickoff"]):
                continue
            if r.get("engine_version") != "sportsbook_v5.r2":
                other[r.get("engine_version")] += 1
                continue
            k = (r["sport"], r["event_id"])
            if k not in best or r["taken_at"] > best[k]["taken_at"]:
                best[k] = r
    return best, other


def evaluate(records, live, finals):
    rows = []
    for (sport, gid), r in sorted(records.items(), key=lambda x: x[1]["kickoff"]):
        side = (r.get("need") or {}).get("side")
        if side not in ("home", "away"):
            continue
        h = History(live, sport, gid, "spread")
        kick = to_epoch(r["kickoff"])
        t = to_epoch(r["taken_at"])
        off = (r.get("need_pick") or {}).get("offer") or {}
        hs = off.get("home_spread")
        book = None
        if hs is not None:
            pt = hs if side == "home" else -hs
            pr = off.get("home_price") if side == "home" else off.get("away_price")
            if pr is not None:
                book = {"point": float(pt), "price": float(pr), "book": off.get("price_book")}
        ft, books = h.snap_at(t)
        pin = None
        if "pinnacle" in books:
            q = books["pinnacle"]
            pin = {"point": q["point"] if side == "home" else -q["point"],
                   "price": float(q["price_a"] if side == "home" else q["price_b"])}
        fc, bc = h.snap_at(kick - 1)
        close_mu = bc["pinnacle"]["mu"] if "pinnacle" in bc and bc["pinnacle"]["mu"] is not None else None
        fin = finals.get((sport, gid))
        nz = normalizer(sport, "spread")
        row = {"sport": sport, "game": f"{r['away']} @ {r['home']}", "event_id": gid, "kickoff": r["kickoff"],
               "taken_at": r["taken_at"], "minutes_before": r.get("minutes_before_kickoff"), "side": side,
               "swing": (r["need"] or {}).get("swing"), "qualified": bool((r.get("qualification") or {}).get("qualified")),
               "stability": (r.get("need_stability") or {}).get("label"), "final": fin}
        for name, x in (("book", book), ("pin", pin)):
            if x is None:
                row[name] = None
                continue
            u = grade("spread", side, x["point"], x["price"], fin) if fin else None
            e = None
            if close_mu is not None:
                pw, pp = nz.prob_at(close_mu, side, x["point"])
                e = {"clv": pw * profit_on_win(1, x["price"]) - (1 - pw - pp), "p_win": pw, "p_push": pp,
                     "var": None}
                m = e["clv"]
                win = profit_on_win(1, x["price"])
                e["var"] = pw * (win - m) ** 2 + (1 - pw - pp) * (-1 - m) ** 2 + pp * m ** 2
            row[name] = {**x, "units": u, **(e or {})}
        rows.append(row)
    return rows


def summarize(rows, key):
    xs = [r for r in rows if r.get(key) and r[key]["units"] is not None]
    n = len(xs)
    if not n:
        return {"n": 0}
    w = sum(r[key]["units"] > 0 for r in xs)
    l_ = sum(r[key]["units"] < 0 for r in xs)
    units = sum(r[key]["units"] for r in xs)
    ex = [r for r in xs if r[key].get("clv") is not None]
    clv = sum(r[key]["clv"] for r in ex) / len(ex) if ex else None
    z = (sum(r[key]["units"] - r[key]["clv"] for r in ex) / math.sqrt(sum(r[key]["var"] for r in ex))) if ex else None
    roi = units / max(1, w + l_)
    se = math.sqrt(sum((r[key]["units"] - roi) ** 2 for r in xs) / max(1, n - 1) / n)   # one row per game: independent
    return {"n": n, "record": f"{w}-{l_}-{n - w - l_}", "roi": roi, "roi_ci95": [roi - 1.96 * se, roi + 1.96 * se],
            "clv_at_close": clv, "wins_minus_expected_z": z}
