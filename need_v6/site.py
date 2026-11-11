"""
need_v6/site.py -- the v6 NEED block for the existing site build (web_v4/live_site.py).

Called ONLY when the build's environment has NEED_V6=on (default off): with the flag off no v6 code runs, the
snapshot has no need_v6 block and the page shows exactly what it showed before (the panel code is inert; tested). Never raises into the build (the caller wraps it). Per sport: the current betting week is re-simulated
deterministically from the collected odds up to the build time (idempotent: no stored mutable state, so no
duplicate wagers), the latest card per game-market is published, newly frozen pre-game records are written once,
and every saved frozen record is graded for the validation table (prospective from deployment).
"""

import time

from need_v6 import LABEL, VERSION
from need_v6 import freeze as FZ
from need_v6 import results_match as RM
from need_v6 import week as W
from need_v6.config import CONFIG, CONFIG_HASH
from sportsbook_v4 import live_book as LB
from sportsbook_v4.util import from_epoch, to_epoch


def build_block(conn, rankings_snapshots, now, results_dir=None, need_dir=None, need_out=None, bankrolls=None):
    block = {"status": "OK", "label": LABEL, "version": VERSION, "config_hash": CONFIG_HASH,
             "policy": CONFIG["official_policy"], "built_at": from_epoch(now), "sports": {}}
    for sport in ("nfl", "cfb"):
        L = LB.last_observation(conn, sport, now)
        if L is None:
            block["sports"][sport] = {"status": "NO_DATA", "cards": []}
            continue
        D = LB.complete_through(L, now)
        ws, we = LB.week_window(D)
        r = W.run(conn, sport, ws, we, rankings_snapshots, bankrolls=(bankrolls or {}).get(sport), until=D)
        cards = [c for c in r["cards_latest"].values() if c and to_epoch(c["kickoff"]) > now]
        cards.sort(key=lambda c: (c["kickoff"], c["home"], c["market"]))
        written = FZ.write_new(r["cards_frozen"], sport, need_dir, need_out, now) if need_out else []
        block["sports"][sport] = {"status": "OK" if r["games"] else "NO_GAMES", "window": r["window"],
                                  "data_through": from_epoch(D), "coverage": r["coverage"],
                                  "wagers": len(r["wagers"]), "cards": cards, "frozen_written_this_build": len(written),
                                  "bankroll_basis": "carried v6 bankrolls" if bankrolls else
                                  "opening bankrolls (no v6 bankroll history saved yet)"}
    # validation: every saved frozen record, graded at its frozen terms (prospective from deployment)
    recs = list(FZ.existing(need_dir).values()) + list(FZ.existing(need_out).values())
    finals = {}
    if results_dir and recs:
        from data_v4 import results as R
        docs = R.load(results_dir)
        games = [{"sport": x["sport"], "event_id": x["card"]["event_id"], "home": x["card"]["home"],
                  "away": x["card"]["away"], "kickoff": x["card"]["kickoff"]} for x in recs]
        from data_v4 import rankings
        snaps = [s for s in rankings_snapshots or [] if s.get("teams")]
        m = rankings.TeamMatcher(max(snaps, key=lambda s: s["fetched_at"])["teams"]) if snaps else None
        finals = {k: (v["home_score"], v["away_score"]) for k, v in RM.match(games, docs, m).items()}
    by_type = FZ.validation(recs, finals)
    late = by_type.pop("excluded_late_written", 0)
    block["validation"] = {"basis": "PROSPECTIVE frozen v6 records only (graded at the frozen number and price)",
                           "records": len(recs), "excluded_late_written": late, "by_type": by_type}
    return block


def step(snap, conn, rankings_snapshots, now, results_dir, need_dir, need_out):
    t0 = time.time()
    snap["need_v6"] = build_block(conn, rankings_snapshots, now, results_dir, need_dir, need_out)
    snap["need_v6"]["seconds"] = round(time.time() - t0, 1)
