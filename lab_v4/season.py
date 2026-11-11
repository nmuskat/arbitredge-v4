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


def _populations(live, polls):
    """Both sports' persistent populations (read-only; same ids as the weekly runs use)."""
    out = {}
    for sport in ("nfl", "cfb"):
        book = sqlite3.connect(":memory:")
        ensure_schema(book)
        for ws, we in B.windows(sport, live_from=LIVE_TO):
            games, _ = select_games(live, sport, ws, we, polls)
            if games:
                pid = ensure_population(book, sport, LB.universe(sport, polls, games), bettor_count=10_000)
                out[sport] = load_population(book, pid)
                break
    return out


def two_sport_extras(pops, sport):
    """RESEARCH (two-sport persons): every game-day regular of the OTHER sport's population also bets this sport,
    as the same person (same uid, archetype, traits); index offset 100000; loyal fans get a team of this sport
    (deterministic from their id, weighted like the production population). Wallet: half the person's bankroll."""
    import random
    from lab_v4.behavior_v2 import VOLUME
    from sportsbook_v4.population import _weighted_pick, team_popularity
    from sportsbook_v4.util import seed_int
    other = "cfb" if sport == "nfl" else "nfl"
    teams = sorted({b["traits"].get("loyal_team") for b in pops[sport]["bettors"] if b["traits"].get("loyal_team")})
    extras = []
    for b in pops[other]["bettors"]:
        if not is_regular(b):
            continue
        tr = dict(b["traits"])
        if tr.get("loyal_team"):
            rng = random.Random(seed_int("v4vol_second_team", b["bettor_uid"], sport))
            tr["loyal_team"] = _weighted_pick(rng, [(t, team_popularity(t)) for t in teams]) if teams else None
        extras.append(dict(b, bettor_index=100000 + b["bettor_index"], traits=tr, bankroll=round(0.5 * b["bankroll"], 2),
                           home_sport=other))
    return extras


def is_regular(b):
    import random
    from lab_v4.behavior_v2 import VOLUME
    from sportsbook_v4.util import seed_int
    return b["archetype"] in VOLUME["regular_groups"] and \
        random.Random(seed_int("v4vol_regular", b["bettor_uid"])).random() < VOLUME["regular_share"]


def run_season(live, polls, finals, cfg, seed=0, sports=("nfl", "cfb"), rekey=None, record=False, facts=None):
    out = []
    pops = _populations(live, polls) if cfg.get("two_sport") else None
    for sport in sports:
        book = sqlite3.connect(":memory:")
        ensure_schema(book)
        snap = None
        extras = two_sport_extras(pops, sport) if pops else []
        for ws, we in B.windows(sport, live_from=LIVE_TO):
            games, _ = select_games(live, sport, ws, we, polls)
            if not games:
                continue
            first = snap is None
            run, pop, snap = open_week(book, live, sport, ws, we, polls, games, snap)
            if extras:
                pop = dict(pop, bettors=pop["bettors"] + extras)
                if first:
                    for b in pop["bettors"]:
                        if b["bettor_index"] >= 100000:
                            snap[b["bettor_index"]] = b["bankroll"]
                        elif is_regular(b):
                            snap[b["bettor_index"]] = round(0.5 * snap[b["bettor_index"]], 2)   # own-sport half
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
