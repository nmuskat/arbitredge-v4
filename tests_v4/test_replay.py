"""Milestone 4 (prepared): historical replay against an isolated read-only copy.

The copy here is SYNTHETIC and production-SHAPED: column names are inferred
from the code that reads/writes these tables and must be re-checked against
the real `.schema` output before any real replay.
"""

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

import sportsbook_v4.replay as rp
from sportsbook_v4.grading import closing_line
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import SnapshotMarket
from sportsbook_v4.settlement import book_pnl_total
from sportsbook_v4.util import parse_ts
from tests_v4 import fixtures as fx

SCORES = {"g_tnf": (27, 24), "g_sun1": (31, 17), "g_sun2": (20, 23), "g_sun3": (17, 17),
          "g_sun4": (24, 21), "g_snf": (28, 20), "g_mnf": (13, 24)}


def make_copy(path, truncate_after=None):
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE games_canonical (canonical_game_id INTEGER PRIMARY KEY, sport TEXT, home_team_id TEXT,
        away_team_id TEXT, commence_time TEXT, odds_api_game_id TEXT);
    CREATE TABLE teams_canonical (canonical_team_id TEXT PRIMARY KEY, sport TEXT, display_name TEXT);
    CREATE TABLE market_snapshots (snapshot_id INTEGER PRIMARY KEY, canonical_game_id INTEGER, sport TEXT, book TEXT,
        observed_at TEXT, book_last_update TEXT, spread_home REAL, spread_away REAL,
        spread_price_home REAL, spread_price_away REAL);
    CREATE TABLE game_results (result_id INTEGER PRIMARY KEY, canonical_game_id INTEGER, observed_at TEXT,
        game_status TEXT, is_final INTEGER, final_score_home INTEGER, final_score_away INTEGER,
        season_type INTEGER, retrieved_at TEXT);
    CREATE TABLE game_context (context_id INTEGER PRIMARY KEY, canonical_game_id INTEGER, observed_at TEXT,
        ranking_home INTEGER, ranking_away INTEGER, tv_network TEXT);
    CREATE TABLE game_canonical_redirects (duplicate_game_id INTEGER, survivor_game_id INTEGER);
    """)
    for t in fx.NFL_TEAMS:
        c.execute("INSERT INTO teams_canonical VALUES (?, 'nfl', ?)", (t, t))
    ids = {}
    for i, (gid, h, a, ko, st, tv, path_) in enumerate(fx.NFL_WEEK, start=100):
        ids[gid] = i
        c.execute("INSERT INTO games_canonical VALUES (?, 'nfl', ?, ?, ?, NULL)", (i, h, a, ko))
        c.execute("INSERT INTO game_context (canonical_game_id, observed_at, tv_network) VALUES (?, ?, ?)",
                  (i, "2026-10-25T12:00:00Z", "NBC" if tv else "CBS-regional"))
        for q in fx.build_quotes([(gid, h, a, ko, st, tv, path_)]).quotes[gid]:
            when = q.effective_epoch
            if truncate_after is not None and when > truncate_after:
                continue
            ts = fx.parse_ts(fx.WINDOW_START).fromtimestamp(when, tz=parse_ts(fx.WINDOW_START).tzinfo)
            iso = ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            c.execute("INSERT INTO market_snapshots (canonical_game_id, sport, book, observed_at, book_last_update, "
                      "spread_home, spread_away, spread_price_home, spread_price_away) VALUES (?,?,?,?,?,?,?,?,?)",
                      (i, "nfl", q.book, iso, iso, q.home_spread, -q.home_spread, q.home_price, q.away_price))
        hs, as_ = SCORES[gid]
        c.execute("INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, final_score_home, "
                  "final_score_away, season_type, retrieved_at) VALUES (?,?,?,?,?,?,?,?)",
                  (i, "2026-11-03T08:00:00Z", "STATUS_FINAL", 1, hs, as_, 2, "2026-11-03T08:00:00Z"))
    if truncate_after is None:
        # a post-kickoff in-game poll: must be ignored for decisions and for the close
        c.execute("INSERT INTO market_snapshots (canonical_game_id, sport, book, observed_at, book_last_update, "
                  "spread_home, spread_away, spread_price_home, spread_price_away) VALUES (?,?,?,?,?,?,?,?,?)",
                  (ids["g_mnf"], "nfl", "draftkings", "2026-11-03T02:00:00Z", None, 10.5, -10.5, -110, -110))
    # CFB: game 1 ranked before window start; game 2 ranked only AFTER window start;
    # game 3's ranking lives on a duplicate id that redirects to it.
    for gid, rank_obs, rank in ((201, "2026-10-26T00:00:00Z", 5), (202, "2026-10-29T00:00:00Z", 12), (299, "2026-10-26T00:00:00Z", 20)):
        if gid != 299:
            c.execute("INSERT INTO games_canonical VALUES (?, 'cfb', ?, ?, '2026-10-31T16:00:00Z', NULL)",
                      (gid, f"cfb_h{gid}", f"cfb_a{gid}"))
        c.execute("INSERT INTO game_context (canonical_game_id, observed_at, ranking_home, tv_network) VALUES (?,?,?,?)",
                  (gid, rank_obs, rank, "ESPN"))
    c.execute("INSERT INTO games_canonical VALUES (203, 'cfb', 'cfb_h203', 'cfb_a203', '2026-10-31T19:30:00Z', NULL)")
    c.execute("INSERT INTO game_canonical_redirects VALUES (299, 203)")
    for gid in (201, 202, 203):
        c.execute("INSERT INTO market_snapshots (canonical_game_id, sport, book, observed_at, book_last_update, spread_home, "
                  "spread_away, spread_price_home, spread_price_away) VALUES (?, 'cfb', 'draftkings', "
                  "'2026-10-26T12:00:00Z', NULL, -7.5, 7.5, -110, -110)", (gid,))
    c.commit()
    c.close()
    return ids


@pytest.fixture()
def copy_path(tmp_path):
    p = tmp_path / "historical_copy.db"
    make_copy(p)
    return p


def _digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_refuses_production_paths(tmp_path, monkeypatch):
    prod_dir = tmp_path / "opt" / "arbitredge" / "db"
    prod_dir.mkdir(parents=True)
    prod = prod_dir / "arbitredge.db"
    make_copy(prod)
    beside = prod_dir / "copy_next_to_prod.db"
    make_copy(beside)
    link = tmp_path / "innocent_name.db"
    os.symlink(prod, link)
    monkeypatch.setattr(rp, "PRODUCTION_DB_PATHS", (str(prod),))
    for p in (prod, beside, link):
        with pytest.raises(rp.ProductionAccessRefused):
            rp.open_historical_copy(p)
    with pytest.raises(rp.ProductionAccessRefused):
        rp.replay_slate(None, link, None)


def test_default_guard_covers_the_real_production_path():
    assert "/opt/arbitredge/db/arbitredge.db" in rp.PRODUCTION_DB_PATHS
    with pytest.raises(rp.ProductionAccessRefused):
        rp.open_historical_copy("/opt/arbitredge/db/arbitredge.db")


def test_copy_is_opened_read_only(copy_path):
    c = rp.open_historical_copy(copy_path)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM market_snapshots")
    c.close()


def test_validate_copy_reports_schema_mismatch(copy_path, tmp_path):
    rep = rp.validate_copy(rp.open_historical_copy(copy_path))
    assert rep["ok"] and "game_canonical_redirects" in rep["optional_present"]
    assert rep["data_complete_through"] == "2026-11-03T02:00:00.000000Z"
    broken = tmp_path / "broken.db"
    c = sqlite3.connect(broken)
    c.execute("CREATE TABLE market_snapshots (snapshot_id INTEGER, canonical_game_id INTEGER, book TEXT)")
    c.commit()
    c.close()
    rep = rp.validate_copy(rp.open_historical_copy(broken))
    assert not rep["ok"] and "games_canonical" in rep["missing_tables"]
    assert "spread_home" in rep["missing_columns"]["market_snapshots"]


def test_week_slates_are_point_in_time(copy_path):
    conn = rp.open_historical_copy(copy_path)
    slates, excl = rp.build_week_slates(conn, "nfl", "2026-10-26", "2026-11-03")
    assert len(slates) == 1
    s = slates[0]
    assert s.window_start == "2026-10-26T00:00:00.000000Z" and s.window_end == "2026-11-03T01:15:00.000000Z"
    flags = {g.canonical_game_id: (g.standalone, g.national_tv) for g in s.games}
    assert flags["100"] == (True, True)        # TNF
    assert flags["101"][0] is False             # 1pm Sunday window
    cfb, cexcl = rp.build_week_slates(conn, "cfb", "2026-10-26", "2026-11-01")
    eligible = {g.canonical_game_id for g in cfb[0].games}
    assert eligible == {"201", "203"}          # 202's ranking appeared only after window start; 203 via redirect
    assert {"game": "202", "reason": "no_context_at_window_start"} in cexcl


def test_end_to_end_replay_on_copy_without_writing_to_it(copy_path, tmp_path):
    before = _digest(copy_path)
    conn = rp.open_historical_copy(copy_path)
    slate = rp.build_week_slates(conn, "nfl", "2026-10-26", "2026-11-03")[0][0]
    conn.close()
    v4 = fx.new_v4_db(str(tmp_path / "v4.db"))
    out = rp.replay_slate(v4, copy_path, slate, bettors=2000)
    assert _digest(copy_path) == before
    assert set(out["games"]) == {g.canonical_game_id for g in slate.games}
    market = SnapshotMarket(copy_path, source_id="check")
    for g in slate.games:
        r = out["games"][g.canonical_game_id]
        assert r["tickets"] > 0
        no = r["need_outcome"]
        assert no["status"] == "EVALUATED"
        wagers = fetch_wagers(v4, out["run_id"], g.canonical_game_id)
        hs, as_ = SCORES[{v: k for k, v in {gid: str(i) for i, (gid, *_x) in enumerate(fx.NFL_WEEK, start=100)}.items()}[g.canonical_game_id]]
        assert no["realized_book_pnl"] == pytest.approx(book_pnl_total(wagers, hs - as_), abs=0.01)
        assert all(parse_ts(w["accepted_at"]) < parse_ts(g.commence_time) for w in wagers)
    # The close for MNF ignores the in-game 10.5 poll.
    mnf = next(g for g in slate.games if g.canonical_game_id == "106")
    assert closing_line(market, "106", mnf.commence_time)["home_spread"] == 2.5
    # Replaying again is idempotent (same run, no new wagers).
    n = v4.execute("SELECT COUNT(*) FROM v4_wager").fetchone()[0]
    rp.replay_slate(v4, copy_path, slate, bettors=2000)
    assert v4.execute("SELECT COUNT(*) FROM v4_wager").fetchone()[0] == n


def test_incomplete_copy_is_refused(tmp_path):
    p = tmp_path / "partial.db"
    make_copy(p, truncate_after=fx.to_epoch("2026-10-30T00:00:00Z"))
    conn = rp.open_historical_copy(p)
    slate = rp.build_week_slates(conn, "nfl", "2026-10-26", "2026-11-03")[0][0]
    conn.close()
    with pytest.raises(rp.IncompleteCopy):
        rp.replay_slate(fx.new_v4_db(), p, slate, bettors=200)
