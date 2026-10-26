"""
sportsbook_v4/replay.py -- historical replay framework (Milestone 4, PREPARED ONLY).

Replays completed weeks through the V4 engine against an ISOLATED, READ-ONLY
COPY of historical market data, then measures each game's financial Need
against the real result and closing line.

SAFETY
  * open_historical_copy() refuses the production database path (and any
    file under the production db directory) and anything that is the same
    file as production; it opens the copy with mode=ro + query_only.
  * Nothing here writes to the copy. V4 output goes to a separate V4 DB.
  * The expected schema (REQUIRED_SCHEMA) is inferred from the source code
    that reads/writes these tables; validate_copy() checks it against the
    real copy and reports every mismatch instead of assuming.
  * A week is replayed only if the copy's market data extends past that
    week's last kickoff (data_complete_through), so no decision can be
    frozen on data the copy does not contain.

NOT YET RUN AGAINST REAL DATA (awaiting the copy and your authorisation).
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from sportsbook_v4.engine import GameSpec, SlateSpec, advance, create_run, load_run
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.grading import closing_line, need_outcome, results_from_db, select_final_result
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import SnapshotMarket
from sportsbook_v4.population import ensure_population
from sportsbook_v4.settlement import exposure
from sportsbook_v4.util import ISO_FMT, from_epoch, to_epoch

PRODUCTION_DB_PATHS = ("/opt/arbitredge/db/arbitredge.db",)
NATIONAL_NETWORKS = ("NBC", "ESPN", "ABC", "FOX", "CBS", "PRIME", "AMAZON")  # same list V3 uses

REQUIRED_SCHEMA = {
    "market_snapshots": {"snapshot_id", "canonical_game_id", "book", "observed_at", "book_last_update",
                         "spread_home", "spread_price_home", "spread_price_away"},
    "games_canonical": {"canonical_game_id", "sport", "home_team_id", "away_team_id", "commence_time"},
    "teams_canonical": {"canonical_team_id", "sport"},
    "game_results": {"canonical_game_id", "is_final", "final_score_home", "final_score_away", "retrieved_at"},
    "game_context": {"canonical_game_id", "observed_at", "ranking_home", "ranking_away", "tv_network"},
}
OPTIONAL_SCHEMA = {"game_canonical_redirects": {"duplicate_game_id", "survivor_game_id"}}


class ProductionAccessRefused(RuntimeError):
    pass


class IncompleteCopy(RuntimeError):
    pass


def _guard(path):
    real = os.path.realpath(path)
    for prod in PRODUCTION_DB_PATHS:
        prod_real = os.path.realpath(prod)
        if real == prod_real or real.startswith(os.path.dirname(prod_real) + os.sep):
            raise ProductionAccessRefused(f"{path} is (or sits beside) the production database; use an isolated copy")
        if os.path.exists(prod) and os.path.exists(path) and os.path.samefile(path, prod):
            raise ProductionAccessRefused(f"{path} is the same file as production")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)


def open_historical_copy(path):
    _guard(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def validate_copy(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rep = {"missing_tables": [], "missing_columns": {}, "optional_present": [], "counts": {}}
    for t, cols in {**REQUIRED_SCHEMA, **OPTIONAL_SCHEMA}.items():
        if t not in tables:
            if t in REQUIRED_SCHEMA:
                rep["missing_tables"].append(t)
            continue
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        if cols - have:
            rep["missing_columns"][t] = sorted(cols - have)
        elif t in OPTIONAL_SCHEMA:
            rep["optional_present"].append(t)
    rep["ok"] = not rep["missing_tables"] and not rep["missing_columns"]
    if not rep["ok"]:
        return rep
    rep["counts"]["market_snapshots_by_sport"] = [
        {"sport": s, "snapshots": n, "games": g, "books": b, "first": f, "last": la}
        for s, n, g, b, f, la in conn.execute(
            "SELECT gc.sport, COUNT(*), COUNT(DISTINCT ms.canonical_game_id), COUNT(DISTINCT ms.book), "
            "MIN(ms.observed_at), MAX(ms.observed_at) FROM market_snapshots ms "
            "JOIN games_canonical gc ON gc.canonical_game_id = ms.canonical_game_id GROUP BY gc.sport")]
    rep["counts"]["games_with_final_result"] = conn.execute(
        "SELECT COUNT(DISTINCT canonical_game_id) FROM game_results WHERE is_final = 1").fetchone()[0]
    rep["data_complete_through"] = data_complete_through(conn)
    return rep


def data_complete_through(conn):
    row = conn.execute("SELECT MAX(COALESCE(book_last_update, observed_at)), MAX(observed_at) FROM market_snapshots").fetchone()
    vals = [v for v in row if v]
    return from_epoch(max(to_epoch(v) for v in vals)) if vals else None


def results_complete_through(conn):
    row = conn.execute("SELECT MAX(retrieved_at) FROM game_results").fetchone()
    return from_epoch(to_epoch(row[0])) if row and row[0] else None


def market_source_id(conn, path):
    n, mx, last = conn.execute("SELECT COUNT(*), MAX(snapshot_id), MAX(observed_at) FROM market_snapshots").fetchone()
    return f"copy:{os.path.basename(path)}:n{n}:max{mx}:{last}"


# --------------------------------------------------------------- slates

def _first_quote_epoch(conn, gid):
    row = conn.execute("SELECT MIN(COALESCE(book_last_update, observed_at)) FROM market_snapshots "
                       "WHERE canonical_game_id = ? AND spread_home IS NOT NULL", (gid,)).fetchone()
    return to_epoch(row[0]) if row and row[0] else None


def _context_as_of(conn, gid, as_of, redirects):
    # ids may be INTEGER in the copy and str in V4 -- compare as text
    ids = [str(gid)] + [str(d) for d, s in redirects if str(s) == str(gid)]
    q = ",".join("?" * len(ids))
    return conn.execute(
        f"SELECT ranking_home, ranking_away, tv_network FROM game_context WHERE canonical_game_id IN ({q}) "
        f"AND observed_at <= ? ORDER BY observed_at DESC LIMIT 1", ids + [as_of]).fetchone()


def _week_key(dt, sport):
    # NFL: Thursday 00:00 UTC weeks (same rule as book_need.public_pressure._find_nfl_week_games).
    # CFB: Monday 00:00 UTC weeks (covers Tue-Sat slates; documented assumption).
    anchor = 3 if sport == "nfl" else 0
    start = (dt - timedelta(days=(dt.weekday() - anchor) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start


def build_week_slates(conn, sport, start_date, end_date, max_open_days=10, cfb_top25_only=True):
    """
    Group completed games into betting weeks and define each week's window:
      window_start = max(first quote among the week's games, first kickoff - max_open_days)
      window_end   = last kickoff
    CFB eligibility (if cfb_top25_only) mirrors V3's rule but POINT-IN-TIME at
    window_start: latest game_context row (incl. canonical redirects) observed
    at or before window_start must show an AP rank 1-25 for either team.
    Returns (slates, exclusions).
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    redirects = (conn.execute("SELECT duplicate_game_id, survivor_game_id FROM game_canonical_redirects").fetchall()
                 if "game_canonical_redirects" in tables else [])
    games = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = ? AND commence_time >= ? AND commence_time <= ? ORDER BY commence_time",
        (sport, f"{start_date}T00:00:00Z", f"{end_date}T23:59:59Z")).fetchall()
    weeks, exclusions = {}, []
    for gid, h, a, ko in games:
        weeks.setdefault(_week_key(datetime.fromisoformat(ko.replace("Z", "+00:00")).astimezone(timezone.utc), sport),
                         []).append((str(gid), h, a, ko))
    slates = []
    for wk, rows in sorted(weeks.items()):
        firsts = {g: _first_quote_epoch(conn, g) for g, *_ in rows}
        quoted = [r for r in rows if firsts[r[0]] is not None]
        exclusions += [{"game": r[0], "reason": "no_quotes"} for r in rows if firsts[r[0]] is None]
        if not quoted:
            continue
        first_ko = min(to_epoch(r[3]) for r in quoted)
        ws = max(min(firsts[r[0]] for r in quoted), first_ko - max_open_days * 86400)
        ws_iso = from_epoch(ws)
        specs = []
        for gid, h, a, ko in quoted:
            ctx = _context_as_of(conn, gid, ws_iso, redirects)
            if sport == "cfb" and cfb_top25_only:
                ranked = ctx is not None and any(isinstance(r, int) and 1 <= r <= 25 for r in ctx[:2])
                if not ranked:
                    exclusions.append({"game": gid, "reason": "not_top25_at_window_start" if ctx else "no_context_at_window_start"})
                    continue
            ko_e = to_epoch(ko)
            standalone = not any(abs(to_epoch(o[3]) - ko_e) < 2 * 3600 for o in quoted if o[0] != gid)
            network = str((ctx or (None, None, None))[2] or "").upper()
            specs.append(GameSpec(gid, h, a, ko, standalone=standalone,
                                  national_tv=bool(network) and any(n in network for n in NATIONAL_NETWORKS)))
        if specs:
            we = max(to_epoch(g.commence_time) for g in specs)
            slates.append(SlateSpec(sport, f"{sport}_week_{wk.strftime('%Y-%m-%d')}", ws_iso, from_epoch(we), tuple(specs)))
    return slates, exclusions


# --------------------------------------------------------------- replay

def replay_slate(v4_conn, copy_path, slate, bettors=10_000, facts=None, grade_as_of=None):
    """
    Run one historical week through V4 and evaluate each game's Need.
    Returns {"run_id", "games": {gid: {...}}}.
    """
    copy_conn = open_historical_copy(copy_path)
    try:
        dct = data_complete_through(copy_conn)
        if dct is None or to_epoch(dct) < to_epoch(slate.window_end):
            raise IncompleteCopy(f"copy market data ends {dct}, before {slate.slate_id} window_end {slate.window_end}")
        teams = [r[0] for r in copy_conn.execute("SELECT canonical_team_id FROM teams_canonical WHERE sport = ?",
                                                 (slate.sport,))]
        _guard(copy_path)
        market = SnapshotMarket(copy_path, source_id=market_source_id(copy_conn, copy_path))
        pid = ensure_population(v4_conn, slate.sport, teams, bettor_count=bettors)
        run_id = create_run(v4_conn, pid, slate, market)
        advance(v4_conn, run_id, slate.window_end, market, facts or NoFacts(), data_complete_through=dct)
        # Results arrive after the market closes: the grading cutoff is the latest result retrieval
        # in the copy (or an explicit cutoff), never the market-data horizon.
        grade_as_of = grade_as_of or results_complete_through(copy_conn) or dct
        results = results_from_db(copy_conn, [g.canonical_game_id for g in slate.games])
        out = {}
        for g in load_run(v4_conn, run_id)["slate"].games:
            wagers = fetch_wagers(v4_conn, run_id, g.canonical_game_id)
            e = exposure(wagers)
            result, flags = select_final_result(results.get(g.canonical_game_id, []), g.commence_time, grade_as_of)
            close = closing_line(market, g.canonical_game_id, g.commence_time)
            out[g.canonical_game_id] = {
                "tickets": e["tickets"], "handle": e["handle"], "financial_need_side": e.get("financial_need_side"),
                "pnl_swing": e.get("pnl_swing"), "result_flags": flags,
                "closing_home_spread": close["home_spread"] if close else None,
                "need_outcome": need_outcome(e.get("financial_need_side"), wagers, result, close),
            }
        market.close()
        return {"run_id": run_id, "slate_id": slate.slate_id, "replayed_at": datetime.now(timezone.utc).strftime(ISO_FMT),
                "games": out}
    finally:
        copy_conn.close()
