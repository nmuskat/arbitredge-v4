"""
sportsbook_v4/behaviour_metrics.py -- is the synthetic sportsbook behaving plausibly? (MODELED; descriptive only)

Measured from the run's own population and accepted wagers, never tuned toward any target:
participation (active / sitting out / busted), wagers per active customer, stake distribution by group,
bankroll turnover (stake / week-open bankroll), handle concentration, market mix, and why arrivals did
not bet (price too expensive = price sensitivity, budget, no edge, ...).
"""

import json
import math

from sportsbook_v4 import ENGINE_VERSION


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    return round(sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))], 2)


def run_metrics(book_conn, run_id, wagers):
    run = book_conn.execute("SELECT population_id, window_start, engine_version FROM v4_run WHERE run_id=?",
                            (run_id,)).fetchone()
    pid, ws_iso, ev = run
    from sportsbook_v4.population import load_population
    from sportsbook_v4.util import to_epoch
    pop = load_population(book_conn, pid)["bettors"]
    snap = dict(book_conn.execute("SELECT bettor_index, bankroll FROM v5_bankroll_snapshot WHERE run_id=?",
                                  (run_id,)).fetchall())
    out = {"engine_version": ev, "customers": len(pop)}
    if ev.startswith("sportsbook_v5"):
        from sportsbook_v4 import customers_v5 as C
        act, busted = 0, 0
        by_group = {}
        for b in pop:
            _e, _a, active, ind = C.effective(b, snap.get(b["bettor_index"], b["bankroll"]), to_epoch(ws_iso))
            act += active
            busted += ind["busted"]
            g = by_group.setdefault(b["archetype"], [0, 0])
            g[0] += 1
            g[1] += active
        out["participation"] = {"active_this_week": act, "sitting_out": len(pop) - act - busted, "busted": busted,
                                "active_share_by_group": {k: round(v[1] / v[0], 3) for k, v in sorted(by_group.items())}}
    per = {}
    for w in wagers:
        per.setdefault(w["bettor_index"], []).append(w)
    counts = sorted(len(v) for v in per.values())
    out["wagers"] = len(wagers)
    out["customers_with_wagers"] = len(per)
    out["wagers_per_betting_customer"] = {"median": _pct(counts, 0.5), "p90": _pct(counts, 0.9),
                                          "max": counts[-1] if counts else None}
    groups = {}
    for w in wagers:
        groups.setdefault(w["archetype"], []).append(w["stake"])
    out["stake_by_group"] = {g: {"n": len(v), "median": _pct(sorted(v), 0.5), "p90": _pct(sorted(v), 0.9),
                                 "max": max(v)} for g, v in sorted(groups.items())}
    handle = math.fsum(w["stake"] for w in wagers) or 1.0
    by_bettor = sorted((math.fsum(x["stake"] for x in v) for v in per.values()), reverse=True)
    out["handle"] = round(handle, 2)
    out["concentration"] = {"top_1pct_customers_share": round(sum(by_bettor[:max(1, len(by_bettor) // 100)]) / handle, 3),
                            "top_10pct_customers_share": round(sum(by_bettor[:max(1, len(by_bettor) // 10)]) / handle, 3)}
    turn = []
    for i, v in per.items():
        br = snap.get(i) or next((b["bankroll"] for b in pop if b["bettor_index"] == i), None)
        if br:
            turn.append(math.fsum(x["stake"] for x in v) / br)
    out["weekly_turnover_of_bankroll"] = {"median": _pct(sorted(turn), 0.5), "p90": _pct(sorted(turn), 0.9)}
    out["market_mix"] = {m: round(sum(1 for w in wagers if w["market"] == m) / max(1, len(wagers)), 3)
                         for m in ("spread", "total", "moneyline")}
    passes = {}
    for (pj,) in book_conn.execute("SELECT passes_json FROM v4_interval WHERE run_id=?", (run_id,)):
        for k, v in json.loads(pj).items():
            passes[k] = passes.get(k, 0) + v
    arrivals = sum(r[0] for r in book_conn.execute("SELECT arrivals FROM v4_interval WHERE run_id=?", (run_id,)))
    out["arrivals"] = arrivals
    out["did_not_bet"] = {k: v for k, v in sorted(passes.items())}
    out["price_sensitivity_pass_share"] = round(passes.get("price_too_expensive", 0) / arrivals, 3) if arrivals else None
    out["label"] = "MODELED: descriptive behaviour of simulated customers (not observed data, not a target)"
    out["active_engine"] = ENGINE_VERSION
    return out
