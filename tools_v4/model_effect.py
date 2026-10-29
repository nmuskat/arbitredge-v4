"""
tools_v4/model_effect.py -- OFFLINE comparison of the active bettor model with an experimental candidate on
real weeks (no API calls, nothing persisted, the live ledger is only read).

Both models are replayed from scratch over the same weekly run (same games, same real prices, same random
numbers where the population allows), and their MODELED splits are summarised side by side.

  python tools_v4/model_effect.py --shards DIR --book-dir DIR --rankings-dir DIR --candidate m5_draft1
"""

import argparse
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsbook_v4 import ENGINE_VERSION, need_tracker as NT, params as P, params_calib, persist  # noqa: E402
from sportsbook_v4 import live_book as LB  # noqa: E402
from sportsbook_v4.engine import load_run  # noqa: E402
from sportsbook_v4.ledger_read import fetch_wagers  # noqa: E402
from sportsbook_v4.schema import ensure_schema  # noqa: E402

SHARP = NT.SHARP


def stats(wagers, games):
    """Distribution summaries of one model's spread wagers for one weekly run."""
    sp = [w for w in wagers if w["market"] == "spread"]
    H = math.fsum(w["stake"] for w in sp) or 1.0
    rec = [w for w in sp if w["archetype"] not in SHARP and w["accepted_home_spread"] != 0]
    fav = lambda w: (w["side"] == "home") == (w["accepted_home_spread"] < 0)  # noqa: E731
    buckets = {}
    for lo, hi in ((0.5, 3.5), (3.5, 7.5), (7.5, 14.5), (14.5, 99)):
        sub = [w for w in rec if lo <= abs(w["accepted_home_spread"]) < hi]
        buckets[f"{lo}-{hi}"] = {"n": len(sub), "fav_ticket_pct": round(100 * sum(map(fav, sub)) / len(sub), 1) if sub else None}
    per_bettor = {}
    for w in sp:
        per_bettor[w["bettor_index"]] = per_bettor.get(w["bettor_index"], 0.0) + w["stake"]
    v = sorted(per_bettor.values(), reverse=True)
    by_game = {}
    for w in sp:
        by_game.setdefault(w["canonical_game_id"], []).append(w)
    handles = {g: math.fsum(w["stake"] for w in by_game.get(g, [])) for g in games}
    pressure, conc, gaps, qual = [], [], [], 0
    for g, ws in by_game.items():
        p = NT.pressure(ws)
        q = NT.qualify(p, *NT.week_rank(handles, g))
        qual += q["qualified"]
        pub = q["public_side"]
        if pub:
            pressure.append(p["sides"][pub]["rec_handle_share"])
            gaps.append(abs(p["sides"][pub]["rec_handle_share"] - p["sides"][pub]["rec_ticket_share"]))
        conc.append(p["largest_bettor_share"])

    def q50(x):
        return round(sorted(x)[len(x) // 2], 3) if x else None
    return {"spread_wagers": len(sp), "spread_handle": round(H, 2),
            "high_stakes_handle_share": round(sum(w["stake"] for w in sp if w["archetype"] == "HIGH_STAKES_REC") / H, 3),
            "sharp_handle_share": round(sum(w["stake"] for w in sp if w["archetype"] in SHARP) / H, 3),
            "top10pct_bettors_handle_share": round(sum(v[:max(1, len(v) // 10)]) / H, 3) if v else None,
            "rec_fav_ticket_pct": round(100 * sum(map(fav, rec)) / len(rec), 1) if rec else None,
            "rec_fav_ticket_pct_by_spread": buckets,
            "games": len(by_game), "median_public_rec_handle_share": q50(pressure),
            "games_rec_handle_70pct_plus": sum(x >= 0.70 for x in pressure),
            "median_handle_vs_ticket_gap": q50(gaps), "median_largest_bettor_share": q50(conc),
            "games_qualifying_C1_filter_now": qual}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", required=True)
    ap.add_argument("--book-dir", required=True)
    ap.add_argument("--rankings-dir", default=None)
    ap.add_argument("--candidate", default="m5_draft1")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    from web_v4.live_site import rebuild_store
    live, _c, _n = rebuild_store(a.shards)
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    persist.load_dir(book, a.book_dir)
    report = {"label": "MODELED, offline comparison; not observed data", "candidate": a.candidate, "runs": {}}
    for rid, sport in book.execute("SELECT run_id, sport FROM v4_run WHERE engine_version=? AND params_hash=?",
                                   (ENGINE_VERSION, P.params_hash())).fetchall():
        wm = LB._watermark_iso(book, rid)
        games = [g.canonical_game_id for g in load_run(book, rid)["slate"].games]
        teams = sorted({t for g in load_run(book, rid)["slate"].games for t in (g.home_team_id, g.away_team_id)})
        base = fetch_wagers(book, rid, market=None)
        kw = params_calib.CANDIDATES[a.candidate](sport, teams)
        mem, mrid = LB.replay(book, live, rid, wm, wm, variant=(a.candidate, lambda: kw))
        cand = fetch_wagers(mem, mrid, market=None)
        report["runs"][sport] = {"run_id": rid[:12], "through": wm, "team_popularity_used": bool(kw["team_popularity"]),
                                 "current_m4": stats(base, games), "candidate": stats(cand, games)}
    out = json.dumps(report, indent=1)
    if a.out:
        open(a.out, "w").write(out)
    print(out)


if __name__ == "__main__":
    main()
