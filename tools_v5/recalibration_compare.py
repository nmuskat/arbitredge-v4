"""
tools_v5/recalibration_compare.py -- compare engine releases on the same real data (no API calls; nothing published).

  python tools_v5/recalibration_compare.py --engine sportsbook_v5.r2 --book-dir DIR --shards DIR --as-of ISO --out x.json

The book directory must hold that engine's weekly runs (a build as of --as-of on the same shards). Per game:
  observed ledger    sharp share of handle (all markets and per market), spread Need side and liability (exposure
                     from accepted wagers), and the pre-registered cohorts evaluated on a snapshot of the ledger now
  est_at_kickoff     ESTIMATE of the sharp share at kickoff: each group's handle so far scaled by the share of its
                     timing profile (when in the week it bets) still to come before kickoff. Recreational money
                     arrives late in the week and sharp money early, so the observed share overstates the share at
                     kickoff. It ignores kickoff-proximity effects (recreational customers favour games close to
                     kickoff), so it is conservative. A frozen-market replay cannot be used: the book (correctly)
                     refuses to offer on stale odds.
"""

import argparse
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("engine", "shards", "book-dir", "as-of", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    import sportsbook_v4
    sportsbook_v4.ENGINE_VERSION = a.engine          # before any module binds it
    from sportsbook_v4 import live_book as LB, need_tracker as NT, persist
    from sportsbook_v4.engine import _density, load_run
    from sportsbook_v4.ledger_read import fetch_wagers
    from sportsbook_v4.market import representative_offer
    from sportsbook_v4.params import ARCHETYPES, TIMING_PROFILES
    from sportsbook_v4.schema import ensure_schema
    from sportsbook_v4.util import to_epoch
    from web_v4.live_site import rebuild_store

    live, _c, _n = rebuild_store(a.shards)
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    persist.load_dir(book, a.book_dir)
    now = to_epoch(a.as_of)
    out = {"engine": a.engine, "as_of": a.as_of, "label": "MODELED; est_at_kickoff is an estimate", "sports": {}}

    def mass(arch, t0, t1):
        p = TIMING_PROFILES[ARCHETYPES[arch]["timing"]]
        return math.fsum(_density(p, t) for t in range(int(t0), int(t1), 3600))

    for rid, sport in book.execute("SELECT run_id, sport FROM v4_run WHERE engine_version=?", (a.engine,)).fetchall():
        run = load_run(book, rid)
        ws0, wm = to_epoch(run["slate"].window_start), to_epoch(LB._watermark_iso(book, rid))
        ws = fetch_wagers(book, rid, market=None)
        mkt = LB._market(live, sport)
        per = LB._by_game(ws)
        wh = {g: math.fsum(w["stake"] for w in v if w["market"] == "spread") for g, v in per.items()}
        games, est_tot, est_sh = [], 0.0, 0.0
        for g in run["slate"].games:
            gid, kick = g.canonical_game_id, to_epoch(g.commence_time)
            gw = per.get(gid, [])
            if not gw:
                continue
            home, away = LB._names(live, gid)
            tot = math.fsum(w["stake"] for w in gw)
            sh = math.fsum(w["stake"] for w in gw if w["archetype"] in NT.SHARP)
            by_m = {}
            for m in ("spread", "total", "moneyline"):
                mw = [w for w in gw if w["market"] == m]
                mt = math.fsum(w["stake"] for w in mw)
                by_m[m] = round(100 * math.fsum(w["stake"] for w in mw if w["archetype"] in NT.SHARP) / mt, 1) if mt else None
            est = {}
            for w in gw:
                seen = min(wm, kick)
                f = 1.0 if seen >= kick else mass(w["archetype"], ws0, kick) / max(1e-9, mass(w["archetype"], ws0, seen))
                est[w["archetype"]] = est.get(w["archetype"], 0.0) + w["stake"] * f
            e_t = math.fsum(est.values())
            e_s = math.fsum(v for k, v in est.items() if k in NT.SHARP)
            est_tot += e_t
            est_sh += e_s
            spw = [w for w in gw if w["market"] == "spread"]
            nd = NT.need_of(spw)
            t0 = min(wm, kick - 60)
            snap = NT.snapshot(sport=sport, run={"run_id": rid, "engine_version": a.engine, "params_hash": run["params_hash"]},
                               game={"event_id": gid, "home": home, "away": away, "kickoff": g.commence_time},
                               spread_wagers=spw, week_handles=wh, offer=representative_offer(mkt.latest_quotes(gid, t0)),
                               reference={}, taken_at=t0, built_at=t0) if spw else None
            ev = ((snap or {}).get("cohorts") or {}).get("evaluation") or {}
            games.append({"event_id": gid, "game": f"{away} @ {home}", "kickoff": g.commence_time,
                          "complete": wm >= kick, "handle": round(tot, 2), "wagers": len(gw),
                          "sharp_share_pct": round(100 * sh / tot, 1), "sharp_share_by_market_pct": by_m,
                          "est_at_kickoff_sharp_share_pct": round(100 * e_s / e_t, 1) if e_t else None,
                          "need_side": nd.get("side"), "liability": nd.get("liability"),
                          "worst_case_pnl": nd.get("worst_case_pnl"),
                          "cohorts": {k: v["eligible"] for k, v in ev.items()},
                          "cohort_picks": {k: v.get("pick_side") for k, v in ev.items() if v["eligible"]}})
        T = math.fsum(w["stake"] for w in ws)
        S = math.fsum(w["stake"] for w in ws if w["archetype"] in NT.SHARP)

        def dist(key):
            v = sorted(g[key] for g in games if g[key] is not None)
            q = lambda p: v[min(len(v) - 1, int(p * len(v)))] if v else None
            return {"min": q(0), "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "max": v[-1] if v else None,
                    "games": len(v), "games_8_to_18": sum(8 <= x <= 18 for x in v)}
        out["sports"][sport] = {
            "watermark": LB._watermark_iso(book, rid), "handle": round(T, 2), "wagers": len(ws),
            "sharp_share_pct": round(100 * S / T, 1) if T else None,
            "est_at_kickoff_sharp_share_pct": round(100 * est_sh / est_tot, 1) if est_tot else None,
            "per_game": dist("sharp_share_pct"), "per_game_est_at_kickoff": dist("est_at_kickoff_sharp_share_pct"),
            "games": games}
    json.dump(out, open(a.out, "w"), indent=1)
    for sp, x in out["sports"].items():
        print(a.engine, sp, "observed", x["sharp_share_pct"], "est. at kickoff", x["est_at_kickoff_sharp_share_pct"],
              "| per game", x["per_game"], "| est", x["per_game_est_at_kickoff"])


if __name__ == "__main__":
    main()
