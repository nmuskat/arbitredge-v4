"""
lab_v4/season.py -- run one configuration x seed over every 2026 week (backfill + live), chronologically per sport,
with week-to-week bankrolls carried from THIS configuration's own settled wagers (production rule: opening bankroll
+ settled P&L, never below zero). Results are used only to settle after each week is fully simulated.
"""
import sqlite3

from collect_v4 import backfill as B
from lab_v4.runner import run_week
from sportsbook_v4 import live_book as LB
from sportsbook_v4 import settlement as ST
from sportsbook_v4.engine import SlateSpec, GameSpec, create_run, load_run
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.population import ensure_population, load_population
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import from_epoch
from tools_v4.backfill_replay import select_games

LIVE_TO = "2026-11-10T12:00:00Z"


def open_week(book, live, sport, ws, we, polls, games, snap=None):
    """Same slate / population / rng identity as LB.open_run; bankroll snapshot supplied by the caller."""
    from data_v4 import rankings
    matcher = None
    if sport == "cfb" and polls:
        latest = max((s for s in polls if s.get("teams")), key=lambda s: s["fetched_at"], default=None)
        matcher = rankings.TeamMatcher(latest["teams"]) if latest else None
    pid = ensure_population(book, sport, LB.universe(sport, polls, games), bettor_count=10_000)
    standalone = LB.standalone_ids(LB.week_games(live, sport, ws, we, we))
    slate = SlateSpec(sport, LB.slate_id(sport, ws), from_epoch(ws), from_epoch(we),
                      tuple(GameSpec(g["event_id"], LB._team_key(sport, g["home"], matcher),
                                     LB._team_key(sport, g["away"], matcher), from_epoch(g["kickoff"]),
                                     *LB.classify(sport, g["kickoff"], g["event_id"] in standalone, ws)) for g in games))
    pop = load_population(book, pid)
    snap = snap or {b["bettor_index"]: b["bankroll"] for b in pop["bettors"]}
    rid = create_run(book, pid, slate, LB._market(live, sport), bankroll_snapshot=[[i, v] for i, v in sorted(snap.items())])
    return load_run(book, rid), pop, snap


def settle(w, final):
    hs, as_ = final
    r = ST.wager_outcome(w, hs, as_)
    return r, (0.0 if r == "push" else profit_on_win(w["stake"], w["accepted_price"]) if r == "win" else -w["stake"])


def run_season(live, polls, finals, cfg, seed=0, sports=("nfl", "cfb"), rekey=None, record=False, facts=None):
    out = []
    for sport in sports:
        book = sqlite3.connect(":memory:")
        ensure_schema(book)
        snap = None
        for ws, we in B.windows(sport, live_from=LIVE_TO):
            games, _ = select_games(live, sport, ws, we, polls)
            if not games:
                continue
            run, pop, snap = open_week(book, live, sport, ws, we, polls, games, snap)
            wagers, info = run_week(run, pop, snap, LB._market(live, sport), cfg, seed, rekey=rekey, record=record,
                                   facts_src=(facts or {}).get(sport))
            pnl = {}
            for w in wagers:
                f = finals.get((sport, w["canonical_game_id"]))
                if f:
                    w["result"], p = settle(w, f)
                    w["customer_pnl"] = p
                    pnl[w["bettor_index"]] = pnl.get(w["bettor_index"], 0.0) + p
                else:
                    w["result"], w["customer_pnl"] = None, None
            snap = {i: max(0.0, round(v + pnl.get(i, 0.0), 2)) for i, v in snap.items()}
            out.append({"sport": sport, "window": [ws, we], "games": [{"event_id": g["event_id"], "home": g["home"],
                        "away": g["away"], "kickoff": g["kickoff"]} for g in games],
                        "wagers": wagers, "info": info})
    return out
