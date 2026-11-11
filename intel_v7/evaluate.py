"""
intel_v7/evaluate.py -- historical ENGINEERING evaluation of V7 on the 2026 collected odds.  DEVELOPMENT DATA.

  python -m intel_v7.evaluate --market-db market.db --rankings DIR --results DIR --out v7_eval.json [--md report.md]

Every 2026 game was already examined in earlier research, so nothing here is out-of-sample proof. Thresholds come
from config.py (fixed before this evaluation) and are NOT tuned here; sensitivity variants are reported, never used to
choose a rule.

Decision = the final pregame snapshot (latest fetch at or before kickoff - 30 min); T-6h and T-24h are reported as
sensitivity. Convergence history is walk-forward (only games kicked off before each decision).
For each directional card and each baseline, at the BEST OBSERVED EXECUTABLE price of the decision fetch:
  * subsequent movement: retail consensus and professional fair move toward the side (points) by the close
  * CLV vs the PROFESSIONAL close (circular for a Pinnacle-derived signal: reported, not primary)
  * CLV vs the ALL-OPERATOR CONSENSUS close (each operator once)
  * outcome: W-L-P and ROI at that price; wins minus the wins expected at the professional close (information test)
Baselines (same decision fetch, same execution rule):
  SHOP     every game-market: the side whose best price has the highest EV vs the professional fair
  MIRROR   every material divergence: the side opposite to professional pricing (the retail lean)
  BOTH     both sides of every game-market (average): pure execution + vig
  PIN-PRICE  the V7 signal side at Pinnacle's own price (no line shopping)
"""
import argparse
import collections
import json
import math
import random
import sqlite3

from intel_v7 import VERSION
from intel_v7.classify import best_price, card
from intel_v7.config import CONFIG, CONFIG_HASH
from intel_v7.convergence import Convergence
from intel_v7.history import SIDES, History, games, view
from need_v6.market import normalizer
from sportsbook_v4 import settlement as ST
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import from_epoch

HORIZONS = {"final": CONFIG["final_snapshot_before_kick_s"], "T-6h": 6 * 3600, "T-24h": 24 * 3600}


def load_finals(rankings_dir, results_dir, gl):
    from collect_v4 import backfill as B
    from data_v4 import rankings
    from data_v4 import results as R
    from need_v6 import results_match as RM
    from tools_v4.backfill_replay import load_polls
    polls = B.poll_snapshots(load_polls(rankings_dir)) if rankings_dir else []
    latest = max((s for s in polls if s.get("teams")), key=lambda s: s["fetched_at"], default=None)
    m = rankings.TeamMatcher(latest["teams"]) if latest else None
    docs = R.load(results_dir) if results_dir else []
    res = RM.match([{"sport": s, "event_id": e, "home": h, "away": a, "kickoff": from_epoch(k)} for s, e, k, h, a in gl],
                   docs, m)
    return {(s, e): (v["home_score"], v["away_score"]) for (s, e), v in res.items()}


def thin(hist, k):
    """Simulate a collector k times less frequent: keep every k-th fetch (counted back from the last)."""
    if k > 1:
        n = len(hist.snaps)
        hist.snaps = [s for i, s in enumerate(hist.snaps) if (n - 1 - i) % k == 0]
        hist.times = [t for t, _ in hist.snaps]
    return hist


def close_state(hist):
    """Professional fair (same basis as available) and consensus fair at the last fetch before kickoff (<= 3 h)."""
    fc, bc = hist.snap_at(hist.kickoff - 1)
    if fc is None or hist.kickoff - fc > 3 * 3600:
        return None
    vc = view(bc)
    if "pro_mu" not in vc or vc.get("consensus_mu") is None:
        return None
    return {"at": fc, "pro_mu": vc["pro_mu"], "pro_by_op": vc["pro"], "consensus_mu": vc["consensus_mu"],
            "retail_mu": vc.get("retail_mu")}


def outcome(hist, side, point, price, dec_view, close, final):
    nz = normalizer(hist.sport, hist.market)
    d = 1 if side in ("home", "over") else -1
    ev = lambda mu: (lambda pw, pp: pw * profit_on_win(1.0, price) - (1 - pw - pp))(*nz.prob_at(mu, side, point))  # noqa: E731
    pwc, ppc = nz.prob_at(close["pro_mu"], side, point)
    r = {"side": side, "point": point, "price": price, "clv_pro_close": ev(close["pro_mu"]),
         "clv_consensus_close": ev(close["consensus_mu"]), "p_fair_close": pwc / max(1e-9, 1 - ppc),
         "retail_move_toward": None if close.get("retail_mu") is None or dec_view.get("retail_mu") is None
         else d * (close["retail_mu"] - dec_view["retail_mu"]),
         "pro_move_toward": None if dec_view.get("pro_basis") not in close["pro_by_op"] else
         d * (close["pro_by_op"][dec_view["pro_basis"]] - dec_view["pro"][dec_view["pro_basis"]]),
         "result": None, "units": None}
    if final:
        hs, as_ = final
        res = ST.wager_result(side, point, hs - as_) if hist.market == "spread" else ST.total_result(side, point, hs + as_)
        r["result"] = res
        r["units"] = 0.0 if res == "push" else profit_on_win(1.0, price) if res == "win" else -1.0
    return r


def run(conn, finals, sports=("nfl", "cfb"), horizon="final", thin_k=1, data_end=None, overrides=None):
    saved = {k: CONFIG[k] for k in (overrides or {})}
    CONFIG.update(overrides or {})
    try:
        return _run(conn, finals, sports, horizon, thin_k, data_end)
    finally:
        CONFIG.update(saved)


def _run(conn, finals, sports, horizon, thin_k, data_end):
    data_end = data_end or conn.execute("SELECT MAX(fetched_at) FROM mkt_quote").fetchone()[0]
    rows = []
    for sport in sports:
        conv = Convergence()
        for e, k, h, a in games(conn, sport, end=data_end):
            for m in ("spread", "total"):
                hist = thin(History(conn, sport, e, m), thin_k)
                if not hist.snaps or hist.kickoff is None:
                    continue
                t = hist.kickoff - HORIZONS[horizon]
                cd = card(hist, t, conv)
                close = close_state(hist)
                ft, books = hist.snap_at(t)
                dv = view(books)
                row = {"sport": sport, "event_id": e, "market": m, "kickoff": hist.kickoff, "card": _slim(cd),
                       "has_close": close is not None, "final": finals.get((sport, e)), "bets": {}}
                if close and "pro_mu" in dv and cd["quote_freshness"]["status"] == "FRESH":
                    fin = finals.get((sport, e))
                    bps = {s: best_price(hist, t, s, dv["pro_mu"]) for s in SIDES[m]}
                    if cd["side"] and bps.get(cd["side"]):
                        bp = bps[cd["side"]]
                        row["bets"]["v7"] = outcome(hist, cd["side"], bp["point"], bp["price"], dv, close, fin)
                        pin = books.get("pinnacle")
                        if pin and pin["mu"] is not None:
                            pt = pin["point"] if (m == "total" or cd["side"] == "home") else -pin["point"]
                            pr = pin["price_a"] if cd["side"] in ("home", "over") else pin["price_b"]
                            row["bets"]["pin_price"] = outcome(hist, cd["side"], pt, pr, dv, close, fin)
                    if all(bps.values()):
                        s = max(SIDES[m], key=lambda x: bps[x]["ev_vs_pro_fair"])
                        row["bets"]["shop"] = outcome(hist, s, bps[s]["point"], bps[s]["price"], dv, close, fin)
                        for x in SIDES[m]:
                            row["bets"]["both_" + x] = outcome(hist, x, bps[x]["point"], bps[x]["price"], dv, close, fin)
                        if cd["divergence"].get("status") == "MATERIAL":
                            o = [x for x in SIDES[m] if x != cd["divergence"]["side"]][0]
                            row["bets"]["mirror"] = outcome(hist, o, bps[o]["point"], bps[o]["price"], dv, close, fin)
                rows.append(row)
                conv.add_history(hist)
    return rows


def _slim(cd):
    keep = ("version", "config_hash", "as_of", "sport", "event_id", "market", "game", "kickoff", "professional_pricing_side",
            "side", "market_need_signal", "headline_type", "classification", "evidence_strength", "evidence",
            "conflicting_evidence", "signal_or_pass", "pass_reason", "best_observed_executable_price")
    out = {k: cd[k] for k in keep}
    out["divergence_status"] = cd["divergence"].get("status")
    out["gap_pts"] = cd["divergence"].get("gap_pts")
    out["resistance_status"] = cd["active_resistance"].get("status")
    out["key_status"] = cd["key_number_analysis"].get("status")
    out["convergence_status"] = cd["market_convergence"].get("status")
    out["freshness"] = cd["quote_freshness"].get("status")
    return out


# ------------------------------------------------------------------------------------------------ statistics
def stats(bets):
    """bets: [(game_key, outcome)]. Bootstrap 95% CIs over games."""
    n = len(bets)
    out = {"n": n}
    if not n:
        return out
    dec = [(g, b) for g, b in bets if b["result"] in ("win", "loss")]
    w = sum(b["result"] == "win" for _, b in dec)
    p = sum(b["result"] == "push" for _, b in bets)
    out["record"] = f"{w}-{len(dec) - w}-{p}"
    out["graded"] = len(dec) + p

    def mean(k, xs):
        v = [b[k] for _, b in xs if b.get(k) is not None]
        return sum(v) / len(v) if v else None
    fns = {"roi": lambda xs: (lambda d: sum(b["units"] for _, b in d) / len(d) if d else None)(
               [(g, b) for g, b in xs if b["result"] in ("win", "loss")]),
           "clv_pro_close": lambda xs: mean("clv_pro_close", xs),
           "clv_consensus_close": lambda xs: mean("clv_consensus_close", xs),
           "retail_move_toward": lambda xs: mean("retail_move_toward", xs),
           "pro_move_toward": lambda xs: mean("pro_move_toward", xs)}
    by = collections.defaultdict(list)
    for g, b in bets:
        by[g].append((g, b))
    keys = sorted(by)
    rng = random.Random(7)
    boots = collections.defaultdict(list)
    for _ in range(400):
        s = [x for kk in (keys[rng.randrange(len(keys))] for _ in keys) for x in by[kk]]
        for name, f in fns.items():
            v = f(s)
            if v is not None:
                boots[name].append(v)
    for name, f in fns.items():
        v = f(bets)
        bs = sorted(boots[name])
        out[name] = None if v is None else round(v, 4)
        out[name + "_ci"] = [round(bs[int(.025 * len(bs))], 4), round(bs[int(.975 * len(bs)) - 1], 4)] if bs else None
    exp = sum(b["p_fair_close"] for _, b in dec)
    var = sum(b["p_fair_close"] * (1 - b["p_fair_close"]) for _, b in dec)
    out["wins_minus_expected"] = round(w - exp, 2) if dec else None
    out["z"] = round((w - exp) / math.sqrt(var), 2) if var else None
    return out


def summarize(rows):
    S = {}

    def add(name, pred, kind="v7"):
        S[name] = stats([((r["sport"], r["event_id"]), r["bets"][kind]) for r in rows if kind in r["bets"] and pred(r)])
    sig = lambda r: r["card"]["signal_or_pass"] == "SIGNAL"            # noqa: E731
    add("V7 SIGNAL (all)", sig)
    for sp in ("nfl", "cfb"):
        for m in ("spread", "total"):
            add(f"V7 SIGNAL {sp} {m}", lambda r, sp=sp, m=m: sig(r) and r["sport"] == sp and r["market"] == m)
    add("V7 SIGNAL at Pinnacle's own price", sig, "pin_price")
    add("Observed disagreement (lean, PASS)", lambda r: r["card"]["side"] and not sig(r))
    for stg in ("WEAK", "MODERATE", "STRONG"):
        add(f"lean strength {stg}", lambda r, stg=stg: r["card"]["side"] and r["card"]["evidence_strength"] == stg)
    for bs in ("ACTIVE_RESISTANCE", "RESISTANCE_UNCORROBORATED", "PRO_LED_MOVE"):
        add(f"lean with B {bs}", lambda r, bs=bs: r["card"]["side"] and r["card"]["resistance_status"] == bs)
    add("lean with C KEY_DEFENSE", lambda r: r["card"]["side"] and r["card"]["key_status"] == "KEY_DEFENSE")
    add("lean with A corroborated by both pro operators", lambda r: r["card"]["side"] and any(
        e["module"] == "A divergence" and e["points"] == 2 for e in r["card"]["evidence"]))
    add("BASELINE SHOP (every game-market)", lambda r: True, "shop")
    add("BASELINE MIRROR (retail lean on material divergence)", lambda r: True, "mirror")
    both = [((r["sport"], r["event_id"]), r["bets"][k]) for r in rows for k in list(r["bets"]) if k.startswith("both_")]
    S["BASELINE BOTH SIDES (average)"] = stats(both)
    return S


def counts(rows):
    c = collections.Counter()
    out = {}
    for r in rows:
        k = (r["sport"], r["market"])
        c[k + ("eligible",)] += 1
        c[k + (r["card"]["classification"],)] += 1
        c[k + (r["card"]["signal_or_pass"],)] += 1
        c[k + ("headline:" + r["card"]["headline_type"],)] += 1
    for (sp, m, what), n in sorted(c.items()):
        out.setdefault(f"{sp} {m}", {})[what] = n
    tot = collections.Counter()
    for r in rows:
        tot[r["card"]["classification"]] += 1
        tot["eligible"] += 1
        tot[r["card"]["signal_or_pass"]] += 1
        tot["resistance:" + r["card"]["resistance_status"]] += 1
        tot["key:" + r["card"]["key_status"]] += 1
        tot["divergence:" + str(r["card"]["divergence_status"])] += 1
    out["all"] = dict(tot)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-db", required=True)
    ap.add_argument("--rankings")
    ap.add_argument("--results")
    ap.add_argument("--out", required=True)
    ap.add_argument("--quick", action="store_true", help="primary run only (no sensitivity variants)")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.market_db)
    end = conn.execute("SELECT MAX(fetched_at) FROM mkt_quote").fetchone()[0]
    gl = [(s, e, k, h, aw) for s in ("nfl", "cfb") for e, k, h, aw in games(conn, s, end=end)]
    finals = load_finals(a.rankings, a.results, gl)
    rep = {"version": VERSION, "config_hash": CONFIG_HASH, "period": "DEVELOPMENT (2026 games already examined)",
           "data_end": from_epoch(end), "finals_matched": len(finals)}
    rows = run(conn, finals, data_end=end)
    rep["primary"] = {"counts": counts(rows), "summary": summarize(rows)}
    rep["rows"] = rows
    if not a.quick:
        variants = {"horizon T-6h": dict(horizon="T-6h"), "horizon T-24h": dict(horizon="T-24h"),
                    "cadence 1/2": dict(thin_k=2), "cadence 1/3": dict(thin_k=3),
                    "freshness: decision fetch <= 1 h": dict(overrides={"max_decision_fetch_age_s": 3600}),
                    "Pinnacle only (BetOnline group ignored)": dict(overrides={"pro_operators": ["pinnacle"]}),
                    "gap threshold 0.75": dict(overrides={"gap_material_pts": 0.75}),
                    "gap threshold 1.0": dict(overrides={"gap_material_pts": 1.0})}
        rep["sensitivity"] = {}
        for name, kw in variants.items():
            rr = run(conn, finals, data_end=end, **kw)
            rep["sensitivity"][name] = {"counts": counts(rr)["all"], "summary": {k: v for k, v in summarize(rr).items()
                                         if k in ("V7 SIGNAL (all)", "Observed disagreement (lean, PASS)",
                                                  "BASELINE SHOP (every game-market)",
                                                  "BASELINE MIRROR (retail lean on material divergence)")}}
    json.dump(rep, open(a.out, "w"), default=str)
    print(json.dumps({"counts": rep["primary"]["counts"]["all"], "signal": rep["primary"]["summary"]["V7 SIGNAL (all)"]}, indent=1))


if __name__ == "__main__":
    main()
