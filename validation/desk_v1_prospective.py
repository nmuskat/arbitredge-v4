"""
validation/desk_v1_prospective.py -- write-once PROSPECTIVE DESK-v1 records for games that have not kicked off.

  python -m validation.desk_v1_prospective --raw DIR --live-db store.db --rankings DIR --params FILE --now ISO --out DIR

Every eligible game-market gets a record (LEAN or NO POSITION with reason) with the market snapshot used, the frozen
parameter checksum and a record checksum. Existing records are never rewritten. DEVIATION (documented): the
specified decision time is kickoff - 90 min, but this research branch has no job running at that time, so records
are made from the LATEST collected snapshot at record time (always before kickoff); the snapshot time is stored.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import time

from desk_v1 import model as M
from desk_v1.spec import SPEC, SPEC_HASH
from intel_v7.history import History, view
from sportsbook_v4 import live_book as LB
from sportsbook_v4.util import from_epoch, to_epoch
from validation.desk_v1_eval2026 import desk_decision, match_game, ratings_by_week, school_map


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("raw", "live-db", "rankings", "params", "now", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    now = to_epoch(a.now)
    P = json.load(open(a.params))
    live = sqlite3.connect(a.live_db)
    from collect_v4 import backfill as B
    from tools_v4.backfill_replay import load_polls
    from desk_v1 import data as DT
    polls = B.poll_snapshots(load_polls(a.rankings))
    matcher = school_map(a.rankings)
    os.makedirs(a.out, exist_ok=True)
    written = []
    for sport in ("nfl", "cfb"):
        ws, we = LB.week_window(now)
        gl, cov = LB.select_week(live, sport, ws, we, now, polls if sport == "cfb" else None)
        rb, _ = ratings_by_week(sport, a.raw, P["sports"][sport])
        sched = DT.games(sport, a.raw, 2026) if sport == "nfl" else DT.cfb_schedule(a.raw, 2026)   # upcoming games
        for g in gl:
            if g["kickoff"] <= now:
                continue
            sr = match_game(sport, {"home": g["home"], "away": g["away"], "kickoff": g["kickoff"]}, sched, matcher)
            for m in ("spread", "total"):
                name = f"desk_v1_{sport}_{g['event_id']}_{m}.json"
                path = os.path.join(a.out, name)
                if os.path.exists(path):
                    continue
                h = History(live, sport, g["event_id"], m)
                ft, books = h.snap_at(now)
                rec = {"format": "desk_v1_prospective_v1", "spec_sha256": SPEC_HASH, "params_sha256": P["params_sha256"],
                       "sport": sport, "market": m, "event_id": g["event_id"], "game": f"{g['away']} @ {g['home']}",
                       "kickoff": from_epoch(g["kickoff"]), "recorded_at": from_epoch(now),
                       "decision_time_note": "latest collected snapshot at record time (spec: kickoff - 90 min; "
                                             "no T-90 job on this research branch)",
                       "snapshot_at": from_epoch(ft) if ft else None}
                v = view(books) if ft else {}
                if not ft or now - ft > SPEC["market_max_age_hours"] * 3600 or "pro_mu" not in v:
                    rec.update(decision="PASS", reason="no market")
                elif sr is None:
                    rec.update(decision="PASS", reason="game not found in play-by-play schedule")
                else:
                    Pm = P["sports"][sport][m]
                    wk = int(sr.week)
                    r, pace, league = rb.get(wk, rb[max(rb)])[m]
                    feat = M.team_features(r, pace, league, SPEC["pace_shrink_to_league"], sr.home, sr.away, bool(sr.neutral))
                    fb = M.football_lines(feat, Pm["alpha"], Pm["beta"])[0 if m == "spread" else 1]
                    pg = min(pace.get(sr.home, (0, 0))[0], pace.get(sr.away, (0, 0))[0])
                    nz = M.normalizer(sport, m, Pm["sigma"])
                    rec.update(mu_mkt=v["pro_mu"], market_basis=v["pro_basis"],
                               football_only_mu_diagnostic=fb, prior_games=pg,
                               **desk_decision(Pm["w"], m, v["pro_mu"], fb, nz, books, pg, "w = 0 (frozen)"))
                body = json.dumps(rec, sort_keys=True, default=float)
                rec["record_sha256"] = hashlib.sha256(body.encode()).hexdigest()
                with open(path, "x") as f:                       # write-once
                    json.dump(rec, f, sort_keys=True, indent=1, default=float)
                written.append(name)
    print(len(written), "records written")


if __name__ == "__main__":
    main()
