"""
need_v6/replay.py -- replay the 2026 season through the v6 sportsbook and grade the frozen NEEDs.  DEVELOPMENT.

  python -m need_v6.replay --market-db market.db --rankings DIR --results DIR --out replay_v6.json

* Weeks run chronologically per sport; each customer's bankroll carries over from the v6 book's OWN settled wagers
  (opening bankroll in the first week). The live v5 ledger is never read or written.
* Every decision uses only data available at that moment (engine.py); results are used only here, for settlement and
  grading, after the week's simulation is complete.
* Frozen card = last desk decision at or before kickoff - 30 min. Each NEED type is graded at the exact frozen number
  and price; CLV = EV of that frozen price at the market makers' no-vig fair at the last observation before kickoff.
* All 2026 games are DEVELOPMENT data (already examined during research). Nothing here is prospective validation.
"""

import argparse
import collections
import json
import sqlite3
import time

from collect_v4 import backfill as B
from data_v4 import rankings
from data_v4 import results as R
from need_v6 import VERSION
from need_v6 import results_match as RM
from need_v6 import week as W
from need_v6.config import CONFIG_HASH
from need_v6.market import GameMarket
from sportsbook_v4 import settlement as ST
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch

LIVE_TO = "2026-11-10T12:00:00Z"


def wager_result(w, hs, as_):
    if w["market"] == "spread":
        return ST.wager_result(w["side"], w["accepted_line"], hs - as_)
    return ST.total_result(w["side"], w["accepted_line"], hs + as_)


def grade(market, side, point, price, hs, as_):
    if side is None or point is None:
        return None
    r = ST.wager_result(side, point, hs - as_) if market == "spread" else ST.total_result(side, point, hs + as_)
    pnl = 0.0 if r == "push" else (profit_on_win(1.0, price) if r == "win" else -1.0) if price else None
    return {"result": r, "units": pnl}


def clv(conn, sport, event_id, market, kickoff, side, point, price):
    if side is None or point is None or not price:
        return None
    gm = GameMarket(conn, sport, event_id, market, kickoff)
    ms = gm.state(kickoff - 1)
    if ms.get("status") != "OK":
        return None
    pw, pp = gm.fair_prob(ms["fair_mu"], side, point)
    return round(pw * profit_on_win(1.0, price) - (1 - pw - pp), 5)


def run(market_db, rankings_dir, results_dir, sports=("nfl", "cfb")):
    from tools_v4.backfill_replay import load_polls
    conn = sqlite3.connect(market_db)
    polls = B.poll_snapshots(load_polls(rankings_dir))
    docs = R.load(results_dir)
    latest = max((s for s in polls if s.get("teams")), key=lambda s: s["fetched_at"], default=None)
    matcher = rankings.TeamMatcher(latest["teams"]) if latest else None
    out = {"version": VERSION, "config_hash": CONFIG_HASH, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "period": "DEVELOPMENT (2026 games already examined; not prospective)", "weeks": [], "frozen": []}
    for sport in sports:
        bank = None
        for ws, we in B.windows(sport, live_from=LIVE_TO):
            t0 = time.time()
            r = W.run(conn, sport, ws, we, polls, bankrolls=bank, replay=True)
            if not r["games"]:
                out["weeks"].append({"sport": sport, "window": r["window"], "games": 0, "coverage": r["coverage"]})
                continue
            res = RM.match([{"sport": sport, "event_id": g["event_id"], "home": g["home"], "away": g["away"],
                             "kickoff": g["kickoff"]} for g in r["games"]], docs, matcher)
            # settle every v6 wager; carry bankrolls forward
            pop_bank = {}
            from sportsbook_v4.population import load_population  # noqa: F401
            pnl = collections.defaultdict(float); unsettled = 0; book = 0.0; handle = 0.0
            for w in r["wagers"]:
                fr = res.get((sport, w["event_id"]))
                handle += w["stake"]
                if not fr:
                    unsettled += 1
                    continue
                x = wager_result(w, fr["home_score"], fr["away_score"])
                p = 0.0 if x == "push" else (profit_on_win(w["stake"], w["accepted_price"]) if x == "win" else -w["stake"])
                pnl[w["bettor_index"]] += p
                book -= p
            _, pop = W.population(sport, [], polls)
            start = bank or {b["bettor_index"]: b["bankroll"] for b in pop["bettors"]}
            bank = {i: max(0.0, round(v + pnl.get(i, 0.0), 2)) for i, v in start.items()}
            acts = collections.Counter(d["action"] for log in r["decisions"].values() for d in log)
            out["weeks"].append({"sport": sport, "window": r["window"], "games": len(r["games"]),
                                 "wagers": len(r["wagers"]), "handle": round(handle, 2), "book_pnl": round(book, 2),
                                 "unsettled_wagers": unsettled, "passes": r["passes"], "desk_actions": dict(acts),
                                 "seconds": round(time.time() - t0, 1),
                                 "bankroll_total_start": round(sum(start.values()), 2),
                                 "bankroll_total_end": round(sum(bank.values()), 2),
                                 "customer_pnl": round(sum(pnl.values()), 2)})
            for k, c in r["cards_frozen"].items():
                if not c:
                    continue
                fr = res.get((sport, k[0]))
                kick = to_epoch(c["kickoff"])
                rec = {"sport": sport, "week_start": r["window"][0], "card": c, "final": None if not fr else
                       {"home": fr["home_score"], "away": fr["away_score"]}, "grades": {}}
                for typ, blk in (("official", c["arbitredge_need"]), ("financial", c["financial_need"]),
                                 ("desk", c["trading_desk_need"]), ("tolerance", c.get("exposure_tolerance") or {})):
                    side = blk.get("side")
                    if side is None:
                        rec["grades"][typ] = None
                        continue
                    off = c["book"]["offers"].get(side) or {}
                    point = off.get("point")
                    if point is None and c["book"]["posted_line"] is not None:
                        point = c["book"]["posted_line"] if (c["market"] == "total" or side == "home") else -c["book"]["posted_line"]
                    price = off.get("price") or -110.0
                    g = grade(c["market"], side, point, price, fr["home_score"], fr["away_score"]) if fr else None
                    rec["grades"][typ] = {"side": side, "point": point, "price": price,
                                          "fair_cover_prob": (c.get("fair_cover_prob") or {}).get(side),
                                          "result": g["result"] if g else "NO_RESULT", "units": g["units"] if g else None,
                                          "clv": clv(conn, sport, k[0], c["market"], kick, side, point, price)}
                out["frozen"].append(rec)
    return out


def summarize(rep):
    s = {}
    for typ in ("official", "financial", "desk", "tolerance"):
        for sp in ("nfl", "cfb", "all"):
            for m in ("spread", "total", "all"):
                rows = [f for f in rep["frozen"] if (sp == "all" or f["sport"] == sp) and (m == "all" or f["card"]["market"] == m)]
                gs = [f["grades"][typ] for f in rows if f["grades"].get(typ)]
                gr = [g for g in gs if g["result"] in ("win", "loss", "push")]
                w = sum(g["result"] == "win" for g in gr); l_ = sum(g["result"] == "loss" for g in gr)
                clvs = [g["clv"] for g in gs if g.get("clv") is not None]
                risk = sum(1 for g in gr if g["result"] != "push")
                expw = sum(g["fair_cover_prob"] for g in gr if g["result"] != "push" and g.get("fair_cover_prob") is not None)
                s[f"{typ}|{sp}|{m}"] = {"game_markets": len(rows), "selections": len(gs), "none": len(rows) - len(gs),
                                        "record": f"{w}-{l_}-{len(gr) - w - l_}",
                                        "win_pct": round(w / (w + l_), 3) if w + l_ else None,
                                        "roi_at_frozen_price": round(sum(g["units"] for g in gr) / risk, 4) if risk else None,
                                        "mean_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
                                        "no_result": len(gs) - len(gr),
                                        # the information test: actual wins vs wins the market makers' fair expected
                                        "wins_minus_fair_expected": round(w - expw, 2) if risk else None}
    both = [f for f in rep["frozen"] if f["grades"].get("financial") and f["grades"].get("desk")]
    s["agreement"] = {"both_present": len(both),
                      "same_side": sum(f["grades"]["financial"]["side"] == f["grades"]["desk"]["side"] for f in both),
                      "financial_only": sum(1 for f in rep["frozen"] if f["grades"].get("financial") and not f["grades"].get("desk")),
                      "desk_only": sum(1 for f in rep["frozen"] if f["grades"].get("desk") and not f["grades"].get("financial"))}
    sw = [f["card"]["financial_need"]["swing"] for f in rep["frozen"] if f["card"]["financial_need"].get("swing")]
    s["avg_financial_swing"] = round(sum(sw) / len(sw), 2) if sw else None
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--rankings", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rep = run(a.market_db, a.rankings, a.results)
    rep["summary"] = summarize(rep)
    json.dump(rep, open(a.out, "w"), default=str)
    print(json.dumps({"weeks": [(w["sport"], w["window"][0][:10], w["games"], w.get("wagers"), w.get("book_pnl"),
                                 w.get("seconds")) for w in rep["weeks"]], "summary": rep["summary"]}, indent=1))


if __name__ == "__main__":
    main()
