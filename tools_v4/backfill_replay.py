"""
tools_v4/backfill_replay.py -- replay the synthetic book on backfilled historical weeks (offline, no API calls).

Input: historical quote shards (release odds-hist, hquotes_*.jsonl.gz), AP-poll snapshots (release cfb-rankings)
and final scores (release v4-results). For every backfilled week of each sport it opens a weekly run with the SAME
engine, parameters and population as the live book, advances it to the end of the week, settles every wager at
its own line and price against the real final score, and records per game-market:
  * the sharp-vs-public card (sportsbook_v4.sharp_public) at PICK time (kickoff - 60 min) and at the close,
  * the consensus line at pick time and at the observed close (market_v4.asof),
  * the final score.
Bankrolls carry over week to week inside the backfill (each customer's settled P&L of earlier backfill weeks),
like the live book. Everything is MODELED; final scores and odds are real.

  python -m tools_v4.backfill_replay --hist DIR --rankings DIR --results DIR --out replay.json [--sport nfl|cfb]
"""

import argparse
import glob
import json
import os
import sqlite3
import time

from collect_v4 import backfill as B
from data_v4 import rankings
from data_v4 import results as R
from market_v4 import asof
from sportsbook_v4 import bettor_settlement as BS
from sportsbook_v4 import live_book as LB
from sportsbook_v4 import sharp_public
from sportsbook_v4.engine import advance
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import to_epoch

PICK_BEFORE_S = 3600
LABEL = "MODELED (synthetic customers on real historical odds; real final scores)"


def load_store(hist_dir):
    from analysis_v4.actions_analyze import rebuild
    paths = sorted(glob.glob(os.path.join(hist_dir, "**", "hquotes_*.jsonl.gz"), recursive=True))
    conn, counts = rebuild(paths, ":memory:")
    return conn, counts, len(paths)


def load_polls(rankings_dir):
    snaps = []
    for p in sorted(glob.glob(os.path.join(rankings_dir, "rankings_*.json"))):
        try:
            snaps.append(json.load(open(p)))
        except (OSError, ValueError):
            continue
    return snaps


def _line(conn, gid, market, at):
    st = asof.market_state(conn, gid, market, at)
    return st.get("line") if st.get("status") == "OK" else None


def _close_time(conn, gid, kick):
    r = conn.execute("SELECT MAX(fetched_at) FROM mkt_quote WHERE event_id=? AND fetched_at<?", (gid, kick)).fetchone()
    return r[0] if r and r[0] is not None else None


def replay_week(book_conn, live_conn, sport, ws, we, polls, docs, settlements):
    games, cov = LB.select_week(live_conn, sport, ws, we, we, polls if sport == "cfb" else None)
    out = {"sport": sport, "window": [B.iso(ws), B.iso(we)], "coverage": cov, "games": [], "wagers": 0}
    if not games:
        return out, []
    matcher = None
    if sport == "cfb":
        latest = max((s for s in polls if s.get("teams")), key=lambda s: s["fetched_at"], default=None)
        matcher = rankings.TeamMatcher(latest["teams"]) if latest else None
    rid = LB.open_run(book_conn, live_conn, sport, ws, we, games, LB.universe(sport, polls, games), 10_000, matcher,
                      standalone=LB.standalone_ids(LB.week_games(live_conn, sport, ws, we, we)), settlements=settlements)
    advance(book_conn, rid, B.iso(we), LB._market(live_conn, sport), NoFacts(), data_complete_through=B.iso(we))
    wagers = fetch_wagers(book_conn, rid, market=None)
    out["run_id"], out["wagers"] = rid, len(wagers)
    res = R.match([{"sport": sport, "event_id": g["event_id"], "home": g["home"], "away": g["away"],
                    "kickoff": B.iso(g["kickoff"])} for g in games], docs, matcher)
    cols = ("wager_id", "run_id", "population_id", "bettor_index", "archetype", "canonical_game_id", "sport",
            "market", "side", "accepted_line", "accepted_price", "stake")
    rows = [dict(zip(cols, r)) for r in book_conn.execute(f"SELECT {', '.join(cols)} FROM v4_wager WHERE run_id=?", (rid,))]
    new = BS.new_settlements(rows, res, settlements, B.iso(we))
    by_game = {}
    for w in wagers:
        by_game.setdefault(w["canonical_game_id"], []).append(w)
    for g in games:
        gid, kick = g["event_id"], g["kickoff"]
        ws_ = [w for w in by_game.get(gid, []) if to_epoch(w["accepted_at"]) < kick]
        pick_t = kick - PICK_BEFORE_S
        at_pick = [w for w in ws_ if to_epoch(w["accepted_at"]) <= pick_t]
        close_t = _close_time(live_conn, gid, kick)
        r = res.get((sport, gid))
        out["games"].append({
            "event_id": gid, "home": g["home"], "away": g["away"], "kickoff": B.iso(kick),
            "rankings": g.get("rankings"),
            "final": None if r is None else {"home": r["home_score"], "away": r["away_score"]},
            "pick": {"at": B.iso(pick_t), "card": sharp_public.game(at_pick),
                     "home_spread": _line(live_conn, gid, "spreads", pick_t), "total": _line(live_conn, gid, "totals", pick_t)},
            "close": {"at": None if close_t is None else B.iso(close_t), "card": sharp_public.game(ws_),
                      "home_spread": None if close_t is None else _line(live_conn, gid, "spreads", close_t),
                      "total": None if close_t is None else _line(live_conn, gid, "totals", close_t)}})
    return out, new


def run(hist_dir, rankings_dir, results_dir, sports=("nfl", "cfb")):
    live_conn, counts, n = load_store(hist_dir)
    polls = B.poll_snapshots(load_polls(rankings_dir))
    docs = R.load(results_dir)
    book = sqlite3.connect(":memory:")
    ensure_schema(book)
    report = {"label": LABEL, "built_at": B.iso(time.time()), "shards": n, "store": counts, "weeks": [],
              "settlements": []}
    for sport in sports:
        settlements = []
        for ws, we in B.windows(sport):
            have = live_conn.execute("SELECT COUNT(*) FROM mkt_fetch WHERE sport=? AND fetched_at>=? AND fetched_at<?",
                                     (B.SPORT_KEYS[sport], ws, we)).fetchone()[0]
            if not have:
                continue
            wk, new = replay_week(book, live_conn, sport, ws, we, polls, docs, settlements)
            settlements += new
            wk["snapshots"] = have
            report["weeks"].append(wk)
            report["settlements"] += new
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hist", required=True)
    ap.add_argument("--rankings", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sport", choices=("nfl", "cfb"))
    a = ap.parse_args(argv)
    rep = run(a.hist, a.rankings, a.results, sports=(a.sport,) if a.sport else ("nfl", "cfb"))
    with open(a.out, "w") as f:
        json.dump(rep, f, default=str)
    print(json.dumps({"weeks": [(w["sport"], w["window"][0], len(w["games"]), w["wagers"]) for w in rep["weeks"]],
                      "settlements": len(rep["settlements"])}, indent=1))


if __name__ == "__main__":
    main()
