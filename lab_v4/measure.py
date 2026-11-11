"""
lab_v4/measure.py -- everything measured per configuration x seed, using only information available at each decision
time for decisions, and results/closes only for grading.

Market side (shared by all configurations, computed once from the real quotes via intel_v7.history):
  for each game-market and horizon T-48/24/6/1 h: best observed executable price of each side at that time, and its
  outcome (intel_v7.evaluate.outcome): CLV vs the all-operator consensus close and vs the professional close, market
  movement toward the side, result and ROI at that price, and the fair (professional close) cover probability.
Book side (per run): realized hold, nominal margin, hold at the closing fair, archetype results, exposure management,
  and Financial NEED (existing sportsbook_v4.settlement code) at each horizon, from all wagers and separately from
  recreational-only and respected-only wagers.
"""
import collections

from intel_v7.classify import best_price
from intel_v7.evaluate import close_state, outcome
from intel_v7.history import SIDES, History, view
from lab_v4.config import HORIZONS_H, MATERIAL
from need_v6.market import normalizer
from sportsbook_v4 import settlement as ST
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch

MK = ("spread", "total")


def market_outcomes(live, game_list, finals):
    """{(sport, gid, market): {"close": {...}, "kick": k, "h": {h: {side: outcome | None, "view": {...}}}}}"""
    out = {}
    for sport, gid, kick in game_list:
        for m in MK:
            hist = History(live, sport, gid, m)
            if not hist.snaps or hist.kickoff is None:
                continue
            close = close_state(hist)
            rec = {"kick": hist.kickoff, "close": close, "h": {}}
            for h in HORIZONS_H:
                t = hist.kickoff - h * 3600
                ft, books = hist.snap_at(t)
                if ft is None or t - ft > 3 * 3600:
                    rec["h"][h] = None
                    continue
                dv = view(books)
                if "pro_mu" not in dv or close is None:
                    rec["h"][h] = {"view": _slim(dv), "sides": {}}
                    continue
                sides = {}
                for s in SIDES[m]:
                    bp = best_price(hist, t, s, dv["pro_mu"])
                    sides[s] = None if bp is None else {**outcome(hist, s, bp["point"], bp["price"], dv, close,
                                                                  finals.get((sport, gid))), "ev_now": bp["ev_vs_pro_fair"]}
                rec["h"][h] = {"view": _slim(dv), "sides": sides,
                               "rep_line": _rep_line(books, m)}
            out[(sport, gid, m)] = rec
    return out


def _slim(v):
    return {k: v.get(k) for k in ("pro_mu", "retail_mu", "consensus_mu", "pro_basis")}


def _rep_line(books, m):
    pts = sorted(q["point"] for q in books.values() if q["point"] is not None)
    return pts[len(pts) // 2] if pts else None


# ------------------------------------------------------------------------------------------------ book side
def need_at(wagers, market):
    """(side, swing, handle, signed) with signed = P&L if side A covers all - P&L if side B covers all."""
    ws = [w for w in wagers if w["market"] == market]
    if not ws:
        return None, 0.0, 0.0, 0.0
    if market == "spread":
        ex = ST.exposure(ws)
        a, b = ex.get("pnl_if_home_covers_all"), ex.get("pnl_if_away_covers_all")
    else:
        ex = ST.total_exposure(ws)
        a, b = ex.get("pnl_if_over_all"), ex.get("pnl_if_under_all")
    return ex.get("financial_need_side"), ex.get("pnl_swing") or 0.0, ex.get("handle") or 0.0, (a or 0.0) - (b or 0.0)


def summarize_run(weeks, mo):
    """Per-run summary + compact NEED records."""
    agg = collections.defaultdict(lambda: {"handle": 0.0, "book_pnl": 0.0, "settled_handle": 0.0, "wagers": 0,
                                           "nominal_margin_x_stake": 0.0, "nominal_stake": 0.0,
                                           "ev_close_x_stake": 0.0, "ev_stake": 0.0})
    arch = collections.defaultdict(lambda: {"wagers": 0, "handle": 0.0, "pnl": 0.0, "settled": 0.0,
                                            "ev_close_x_stake": 0.0, "ev_stake": 0.0})
    needs, mgmt = [], collections.Counter()
    for wk in weeks:
        sport = wk["sport"]
        mgmt["reference_mode_wagers"] += wk["info"]["reference_mode_wagers"]
        mgmt["wagers"] += len(wk["wagers"])
        for r in wk["info"]["risk_log"]:
            mgmt["risk_" + r["action"]] += 1
        for k, v in wk["info"]["passes"].items():
            mgmt["pass_" + k] += v
        by_gm = collections.defaultdict(list)
        for w in wk["wagers"]:
            by_gm[(w["canonical_game_id"], w["market"])].append(w)
        for (gid, m), ws in by_gm.items():
            close = (mo.get((sport, gid, m)) or {}).get("close") if m in MK else None
            nz = normalizer(sport, m) if (close and m in MK) else None
            for w in ws:
                ev = None
                if nz is not None:
                    pw, pp = nz.prob_at(close["pro_mu"], w["side"], w["accepted_line"])
                    ev = pw * profit_on_win(1.0, w["accepted_price"]) - (1 - pw - pp)
                for key, a in ((("all", "all"), agg), ((sport, "all"), agg), ((sport, m), agg)):
                    x = a[key]
                    x["handle"] += w["stake"]
                    x["wagers"] += 1
                    if w["customer_pnl"] is not None:
                        x["book_pnl"] -= w["customer_pnl"]
                        x["settled_handle"] += w["stake"]
                    if w.get("overround") is not None:
                        x["nominal_margin_x_stake"] += w["overround"] * w["stake"]
                        x["nominal_stake"] += w["stake"]
                    if ev is not None:
                        x["ev_close_x_stake"] += ev * w["stake"]
                        x["ev_stake"] += w["stake"]
                y = arch[(sport, w["archetype"])]
                y["wagers"] += 1
                y["handle"] += w["stake"]
                if w["customer_pnl"] is not None:
                    y["pnl"] += w["customer_pnl"]
                    y["settled"] += w["stake"]
                if ev is not None:
                    y["ev_close_x_stake"] += ev * w["stake"]
                    y["ev_stake"] += w["stake"]
        for g in wk["games"]:
            gid = g["event_id"]
            for m in MK:
                rec = mo.get((sport, gid, m))
                if not rec:
                    continue
                ws = sorted(by_gm.get((gid, m), []), key=lambda w: w["t"])
                row = {"sport": sport, "gid": gid, "market": m, "h": {}}
                for h in HORIZONS_H:
                    t = rec["kick"] - h * 3600
                    sub = [w for w in ws if w["t"] <= t]
                    side, swing, handle, signed = need_at(sub, m)
                    rs = need_at([w for w in sub if not w["respected"]], m)
                    ss = need_at([w for w in sub if w["respected"]], m)
                    row["h"][h] = {"side": side, "swing": round(swing, 2), "handle": round(handle, 2),
                                   "signed": round(signed, 2), "material": bool(side) and swing >= max(
                                       MATERIAL["abs"], MATERIAL["share"] * handle),
                                   "rec_side": rs[0], "rec_signed": round(rs[3], 2), "sharp_side": ss[0],
                                   "sharp_signed": round(ss[3], 2), "sharp_handle": round(ss[2], 2)}
                needs.append(row)
    def fin(x):
        return {"handle": round(x["handle"], 2), "wagers": x["wagers"], "book_pnl": round(x["book_pnl"], 2),
                "realized_hold": round(x["book_pnl"] / x["settled_handle"], 5) if x["settled_handle"] else None,
                "nominal_margin": round(x["nominal_margin_x_stake"] / x["nominal_stake"], 5) if x["nominal_stake"] else None,
                "hold_at_close_fair": round(-x["ev_close_x_stake"] / x["ev_stake"], 5) if x["ev_stake"] else None}
    return {"hold": {f"{k[0]}|{k[1]}": fin(v) for k, v in agg.items()},
            "archetypes": {f"{k[0]}|{k[1]}": {"wagers": v["wagers"], "handle": round(v["handle"], 2),
                                              "pnl": round(v["pnl"], 2),
                                              "roi": round(v["pnl"] / v["settled"], 5) if v["settled"] else None,
                                              "clv_at_close_fair": round(v["ev_close_x_stake"] / v["ev_stake"], 5) if v["ev_stake"] else None}
                           for k, v in arch.items()},
            "management": dict(mgmt), "needs": needs}
