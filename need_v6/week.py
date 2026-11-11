"""
need_v6/week.py -- run one sport-week of the v6 sportsbook on the collected odds.

Games: the live book's selection rules (NFL: every game with collected quotes; CFB: AP Top-25 under the poll that
applied, via the live rule, or the per-week poll rule for replayed history). Customers: the EXISTING population
(same ids, profiles, archetypes, individual traits) with each customer's week-open bankroll; inactive or busted
customers are skipped exactly as in the live V5 engine.
"""

import sqlite3

from need_v6 import SIM_SEED
from need_v6 import engine as V6
from need_v6 import need as NEED
from sportsbook_v4 import customers_v5
from sportsbook_v4 import live_book as LB
from sportsbook_v4.engine import GameSpec
from sportsbook_v4.population import ensure_population, load_population
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import from_epoch, sha256_hex


def games_for(conn, sport, ws, we, L, polls, replay=False):
    if replay and sport == "cfb":
        from tools_v4.backfill_replay import select_games
        games, cov = select_games(conn, sport, ws, we, polls)
    else:
        games, cov = LB.select_week(conn, sport, ws, we, L, polls if sport == "cfb" else None)
    matcher = None
    if sport == "cfb" and polls:
        from data_v4 import rankings
        latest = max((s for s in polls if s.get("teams")), key=lambda s: s["fetched_at"], default=None)
        matcher = rankings.TeamMatcher(latest["teams"]) if latest else None
    standalone = LB.standalone_ids(LB.week_games(conn, sport, ws, we, L))
    specs, names = [], {}
    for g in games:
        st_flag, ntv = LB.classify(sport, g["kickoff"], g["event_id"] in standalone, ws)
        specs.append(GameSpec(g["event_id"], LB._team_key(sport, g["home"], matcher), LB._team_key(sport, g["away"], matcher),
                              from_epoch(g["kickoff"]), st_flag, ntv))
        names[g["event_id"]] = {"home": g["home"], "away": g["away"], "over": "Over", "under": "Under"}
    return specs, names, cov, matcher


def population(sport, games, polls):
    mem = sqlite3.connect(":memory:")
    ensure_schema(mem)
    # the same team universe as the live book -> the same population id, identities and profiles
    pid = ensure_population(mem, sport, LB.universe(sport, polls, []))
    return pid, load_population(mem, pid)


def run(conn, sport, ws, we, polls, bankrolls=None, until=None, replay=False):
    """bankrolls: {bettor_index: week-open bankroll} (None = opening bankrolls). Returns the engine output plus
    population id, names and coverage."""
    L = until if until is not None else we
    games, names, cov, matcher = games_for(conn, sport, ws, we, L, polls, replay=replay)
    if not games:
        return {"sport": sport, "window": [from_epoch(ws), from_epoch(we)], "coverage": cov, "games": [],
                "wagers": [], "cards_latest": {}, "cards_frozen": {}, "decisions": {}, "passes": {}}
    pid, pop = population(sport, games, polls)
    custs = []
    for b in pop["bettors"]:
        bank = (bankrolls or {}).get(b["bettor_index"], b["bankroll"])
        c, arch, active, _ind = customers_v5.effective(b, bank, ws)
        if active:
            custs.append((c, arch))
    rng_key = sha256_hex(SIM_SEED, pid, sport, ws, we)
    gmap = {g.canonical_game_id: g for g in games}

    def card_fn(k, t, gm, desk, exposure, wagers, game):
        return NEED.card(k, t, gm, desk, exposure, wagers, game, names[k[0]])

    out = V6.simulate(conn, sport, games, custs, ws, we, rng_key, until=until, card_fn=card_fn)
    out.update({"sport": sport, "window": [from_epoch(ws), from_epoch(we)], "coverage": cov, "population_id": pid,
                "customers_active": len(custs), "games": [{"event_id": g.canonical_game_id, **names[g.canonical_game_id],
                                                             "kickoff": g.commence_time} for g in games]})
    out["_gmap"] = gmap
    return out
