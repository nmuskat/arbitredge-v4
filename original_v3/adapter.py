"""
original_v3/adapter.py -- load V4's collected data into the ORIGINAL ArbitrEdge (V3) database layout, so the original
modules (behavioral/*, book_need/*, features/*) run UNCHANGED. Research only; read-only on every V4 source.

Mapping (no invented data):
  games_canonical   <- every pre-kickoff event in the V4 quote store (team ids = feed team names; latest kickoff)
  market_snapshots  <- every V4 spread/total quote row, one row per (event, book, fetch): observed_at = fetch time,
                       book_last_update = the provider's market_last_update (the original's "effective time")
  game_results      <- final scores (V4 results / nflverse matched by intel_v7.evaluate.load_finals), retrieved_at =
                       kickoff + 4 h (a result is only "known" after the game), season_type 2, season_year 2026
  game_context      <- CFB AP poll ranks from the cfb-rankings snapshots at their release time (NFL: none).
                       TV network and team records: NOT AVAILABLE in V4 (left NULL -- the original's coverage logic
                       then excludes those components; nothing is guessed)
"""
import sqlite3
import sys
import types
from datetime import datetime, timezone

# The original archive's `db` package (db/migrate.py) is NOT in this repository. The modules used here import only
# its DEFAULT_DB_PATH constant (for their own CLIs, never on the replay path). A stand-in module supplies that one
# name; nothing else of `db` is used or recreated.
if "db.migrate" not in sys.modules:
    _db = types.ModuleType("db")
    _mig = types.ModuleType("db.migrate")
    _mig.DEFAULT_DB_PATH = None

    def _absent(*a, **k):
        raise RuntimeError("original db package is not in this repository (replay never migrates or connects)")
    _mig.run_migration = _absent
    _con = types.ModuleType("db.connection")
    _con.get_connection = _absent
    _db.migrate, _db.connection = _mig, _con
    sys.modules.update({"db": _db, "db.migrate": _mig, "db.connection": _con})

# features/build_engineered_features_v1 imports ONE pure helper (load_fbs_allowlist, a CSV reader) from a collector
# script whose import chain needs the original collector config (absent: it held API settings). The helper is
# copied verbatim (same CSV, same logic) into a stand-in module; no collector code is imported or recreated.
if "collectors.run_results_backfill_dryrun" not in sys.modules:
    import csv as _csv
    from pathlib import Path as _Path
    _dry = types.ModuleType("collectors.run_results_backfill_dryrun")
    _dry.FBS_MEMBERSHIP_PATH = _Path(__file__).resolve().parent.parent / "reference" / "fbs_membership.csv"

    def load_fbs_allowlist(season):
        allowlist = set()
        with open(_dry.FBS_MEMBERSHIP_PATH, newline="") as f:
            for row in _csv.DictReader(f):
                if int(row["season"]) == season and row["classification"] == "FBS":
                    allowlist.add(row["canonical_team_id"])
        return allowlist
    _dry.load_fbs_allowlist = load_fbs_allowlist
    import collectors as _c  # noqa: F401  (package __init__ only)
    sys.modules["collectors.run_results_backfill_dryrun"] = _dry

SPORT = {"americanfootball_nfl": "nfl", "americanfootball_ncaaf": "cfb"}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


SCHEMA = """
CREATE TABLE games_canonical (canonical_game_id INTEGER PRIMARY KEY, sport TEXT NOT NULL, home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL, commence_time TEXT NOT NULL, v4_event_id TEXT);
CREATE TABLE market_snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_game_id INTEGER NOT NULL,
    sport TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL, commence_time TEXT NOT NULL, book TEXT NOT NULL,
    observed_at TEXT NOT NULL, book_last_update TEXT, spread_home REAL, spread_away REAL, spread_price_home REAL,
    spread_price_away REAL, moneyline_home REAL, moneyline_away REAL, total_points REAL, total_price_over REAL,
    total_price_under REAL, parser_version TEXT, raw_response_id INTEGER);
CREATE INDEX ms_g ON market_snapshots (canonical_game_id, book, observed_at);
CREATE TABLE game_results (canonical_game_id INTEGER NOT NULL, final_score_home INTEGER, final_score_away INTEGER,
    winner_home INTEGER, season_year INTEGER, season_type INTEGER, is_final INTEGER NOT NULL, retrieved_at TEXT NOT NULL);
CREATE TABLE game_context (canonical_game_id INTEGER NOT NULL, observed_at TEXT NOT NULL, ranking_home INTEGER,
    ranking_away INTEGER, tv_network TEXT, record_home TEXT, record_away TEXT);
CREATE TABLE game_canonical_redirects (old_id INTEGER, new_id INTEGER);
"""


def build(v4_db, finals, polls, out=":memory:"):
    """v4_db: path of the V4 quote store; finals: {(sport, event_id): (home, away)}; polls: poll snapshots
    [{fetched_at, teams:[{school, rank}]}] (collect_v4.backfill.poll_snapshots)."""
    src = sqlite3.connect(f"file:{v4_db}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    dst.executescript(SCHEMA)
    ev = src.execute("SELECT event_id, sport, home, away, commence, MAX(fetched_at) FROM mkt_quote "
                     "WHERE market IN ('spreads','totals') AND fetched_at < commence GROUP BY event_id").fetchall()
    ids = {}
    for i, (eid, sp, h, a, ko, _) in enumerate(sorted(ev, key=lambda r: (r[4], r[0])), 1):
        ids[eid] = (i, SPORT[sp], h, a, ko)
        dst.execute("INSERT INTO games_canonical VALUES (?,?,?,?,?,?)", (i, SPORT[sp], h, a, iso(ko), eid))
    rows = {}
    for eid, bk, mk, sd, pt, pr, lu, fa, cm in src.execute(
            "SELECT event_id, book, market, side, point, price, market_last_update, fetched_at, commence FROM mkt_quote "
            "WHERE market IN ('spreads','totals')"):
        if eid not in ids or fa >= ids[eid][4]:
            continue                                         # never a quote at/after the (latest) kickoff
        r = rows.setdefault((eid, bk, fa), {"lu": lu})
        r[(mk, sd)] = (pt, pr)
    for (eid, bk, fa), r in rows.items():
        gid, sp, h, a, ko = ids[eid]
        hs, as_ = r.get(("spreads", "home")), r.get(("spreads", "away"))
        ov, un = r.get(("totals", "over")), r.get(("totals", "under"))
        dst.execute("INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, book, "
                    "observed_at, book_last_update, spread_home, spread_away, spread_price_home, spread_price_away, "
                    "total_points, total_price_over, total_price_under, parser_version) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (gid, sp, h, a, iso(ko), bk, iso(fa), iso(min(r["lu"], fa)) if r["lu"] else None,
                     hs[0] if hs else None, as_[0] if as_ else None, hs[1] if hs else None, as_[1] if as_ else None,
                     ov[0] if ov else None, ov[1] if ov else None, un[1] if un else None, "v4_adapter"))
    for (sp, eid), (h, a) in finals.items():
        if eid in ids and h is not None and a is not None:
            gid, _, _, _, ko = ids[eid]
            dst.execute("INSERT INTO game_results VALUES (?,?,?,?,?,?,?,?)",
                        (gid, h, a, int(h > a), 2026, 2, 1, iso(ko + 4 * 3600)))
    if polls:
        from data_v4 import rankings
        for snap in polls:
            if not snap.get("teams"):
                continue
            m = rankings.TeamMatcher(snap["teams"])
            rk = {r["school"]: r["rank"] for r in (snap["polls"][0].get("ranks") or [])}
            for eid, (gid, sp, h, a, ko) in ids.items():
                if sp != "cfb" or snap["fetched_at"] >= ko:
                    continue
                rh, ra = rk.get(m.school_for(h)), rk.get(m.school_for(a))
                if rh or ra:
                    dst.execute("INSERT INTO game_context VALUES (?,?,?,?,?,?,?)",
                                (gid, iso(snap["fetched_at"]), rh, ra, None, None, None))
    dst.commit()
    return dst, ids
