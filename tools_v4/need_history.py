"""
tools_v4/need_history.py -- EXPLORATORY historical sensitivity test of the Book Need Tracker (no API calls).

The current synthetic population (engine m4, same parameters as the live book) is replayed over past NFL
regular-season weeks. Each game shows customers ONE price all week: nflverse's closing spread and its
listed spread odds (-110 when missing). The book's Need and the pre-registered public-pressure filter are
measured at kickoff, then the Need side is graded against the real final score at that closing price.

WHAT THIS IS NOT: evidence that fading real public bettors is profitable. The bettors are simulated with
unvalidated assumptions, they see no line movement, and the price is a consensus close rather than an
executable quote. It only shows how often the side OUR MODEL's public overbets covered historically.

  python tools_v4/need_history.py --games data/raw/games.csv --seasons 2016-2025 --out docs_v4/need_history.json
"""

import argparse
import calendar
import csv
import datetime
import json
import math
import os
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_v4.results import NFL_ABBR  # noqa: E402
from sportsbook_v4 import need_tracker as NT  # noqa: E402
from sportsbook_v4.engine import GameSpec, SlateSpec, advance, create_run  # noqa: E402
from sportsbook_v4.facts import NoFacts  # noqa: E402
from sportsbook_v4.ledger_read import fetch_wagers  # noqa: E402
from sportsbook_v4.live_book import NFL_TEAMS, standalone_ids, team_id, week_window  # noqa: E402
from sportsbook_v4.market import FixtureMarket, Quote, implied_probability, profit_on_win  # noqa: E402
from sportsbook_v4.population import ensure_population  # noqa: E402
from sportsbook_v4.schema import ensure_schema  # noqa: E402
from sportsbook_v4.util import from_epoch  # noqa: E402

ET = ZoneInfo("America/New_York")
OLD = {"OAK": "Las Vegas Raiders", "SD": "Los Angeles Chargers", "STL": "Los Angeles Rams"}   # franchise names


def kickoff_epoch(day, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return int(datetime.datetime(*map(int, day.split("-")), h, m, tzinfo=ET).timestamp())


def load_games(path, seasons):
    out = []
    for r in csv.DictReader(open(path)):
        if int(r["season"]) not in seasons or r["game_type"] != "REG" or r["home_score"] in ("", "NA") \
                or r["spread_line"] in ("", "NA") or not r["gametime"]:
            continue
        home = NFL_ABBR.get(r["home_team"]) or OLD.get(r["home_team"])
        away = NFL_ABBR.get(r["away_team"]) or OLD.get(r["away_team"])
        if not home or not away:
            continue
        odds = lambda k: int(float(r[k])) if r.get(k) not in (None, "", "NA") else -110     # noqa: E731
        out.append({"event_id": r["game_id"], "season": int(r["season"]), "week": int(r["week"]),
                    "home": home, "away": away, "kickoff": kickoff_epoch(r["gameday"], r["gametime"]),
                    "home_spread": -float(r["spread_line"]), "home_price": odds("home_spread_odds"),
                    "away_price": odds("away_spread_odds"),
                    "home_score": int(r["home_score"]), "away_score": int(r["away_score"])})
    return out


def run_week(games, bettors):
    ws, we = week_window(min(g["kickoff"] for g in games) - 1)
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    pid = ensure_population(conn, "nfl", sorted(team_id("nfl", n) for n in NFL_TEAMS), bettor_count=bettors)
    alone = standalone_ids([{"event_id": g["event_id"], "kickoff": g["kickoff"]} for g in games])
    gs = [g for g in games if ws < g["kickoff"] <= we]
    slate = SlateSpec("nfl", f"hist_{gs[0]['season']}_{gs[0]['week']}", from_epoch(ws), from_epoch(we),
                      tuple(GameSpec(g["event_id"], team_id("nfl", g["home"]), team_id("nfl", g["away"]),
                                     from_epoch(g["kickoff"]), standalone=g["event_id"] in alone) for g in gs))
    market = FixtureMarket({g["event_id"]: [Quote(book="nflverse_close", effective_epoch=ws, home_spread=g["home_spread"],
                                                  home_price=g["home_price"], away_price=g["away_price"],
                                                  ref=f"nflverse:{g['event_id']}")] for g in gs},
                           source_id="nflverse_close_constant")
    rid = create_run(conn, pid, slate, market)
    advance(conn, rid, from_epoch(we), market, NoFacts(), data_complete_through=from_epoch(we))
    wagers = fetch_wagers(conn, rid, market="spread")
    by = {}
    for w in wagers:
        by.setdefault(w["canonical_game_id"], []).append(w)
    handles = {g["event_id"]: math.fsum(w["stake"] for w in by.get(g["event_id"], [])) for g in gs}
    rows = []
    for g in gs:
        ws_ = by.get(g["event_id"], [])
        if not ws_:
            continue
        p = NT.pressure(ws_)
        rank, n = NT.week_rank(handles, g["event_id"])
        q = NT.qualify(p, rank, n)
        nd = NT.need_of(ws_)
        if not nd["side"]:
            continue
        side = nd["side"]
        line = g["home_spread"] if side == "home" else -g["home_spread"]
        price = g["home_price"] if side == "home" else g["away_price"]
        margin = (g["home_score"] - g["away_score"]) * (1 if side == "home" else -1) + line
        ats = "W" if margin > 0 else "P" if margin == 0 else "L"
        rows.append({"season": g["season"], "week": g["week"], "event_id": g["event_id"], "need_side": side,
                     "need_is_underdog": line > 0, "need_is_home": side == "home", "line": line, "price": price,
                     "ats": ats, "units": profit_on_win(1.0, price) if ats == "W" else -1.0 if ats == "L" else 0.0,
                     "qualified": q["qualified"], "alignment": NT.alignment(side, q["fade_side"]),
                     "largest_bettor_share": p["largest_bettor_share"], "wagers": p["wagers"],
                     "week_handle_percentile": round(1 - (rank - 1) / n, 4),
                     "rec_public_share": p["sides"][q["public_side"]]["rec_handle_share"] if q["public_side"] else None,
                     "failed": [k for k, c in q["checks"].items() if not c["pass"]]})
    conn.close()
    return rows


def summary(rows):
    s = NT.summarize([dict(r, status="GRADED") for r in rows])
    s["need_is_underdog_share"] = round(sum(r["need_is_underdog"] for r in rows) / len(rows), 4) if rows else None
    s["need_is_home_share"] = round(sum(r["need_is_home"] for r in rows) / len(rows), 4) if rows else None
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--seasons", default="2016-2025")
    ap.add_argument("--bettors", type=int, default=10_000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.seasons.split("-"))
    games = load_games(a.games, range(lo, hi + 1))
    weeks = {}
    for g in games:
        weeks.setdefault((g["season"], g["week"]), []).append(g)
    rows, t0 = [], time.time()
    for i, k in enumerate(sorted(weeks)):
        rows += run_week(weeks[k], a.bettors)
        if i % 20 == 0:
            print(f"{k}: {len(rows)} games so far, {time.time() - t0:.0f} s", file=sys.stderr)
    research = [r for r in rows if r["qualified"] and r["alignment"] == "ALIGNED"]
    out = {"label": "EXPLORATORY SYNTHETIC BACKTEST: not evidence about real public bettors",
           "method": __doc__.strip().split("\n\n")[1], "seasons": [lo, hi], "bettors": a.bettors,
           "thresholds": NT.QUALIFY, "games": len(rows),
           "all_need": summary(rows), "research_subset": summary(research),
           "qualified_disagreements": summary([r for r in rows if r["qualified"] and r["alignment"] == "DISAGREE"]),
           "by_season": {s: summary([r for r in rows if r["season"] == s]) for s in range(lo, hi + 1)},
           "research_by_season": {s: summary([r for r in research if r["season"] == s]) for s in range(lo, hi + 1)},
           "filter_failures": {k: sum(k in r["failed"] for r in rows) for k in NT.QUALIFY and
                               ["weekly_handle_top_25pct", "min_100_spread_wagers", "no_bettor_over_25pct",
                                "rec_handle_70pct_one_side", "rec_ticket_majority_same_side"]},
           "rows": rows}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("games", "all_need", "research_subset")}, indent=1, default=str))


if __name__ == "__main__":
    main()
