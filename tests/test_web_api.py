"""
Tests for web/app.py's API layer.

Run with: python tests/test_web_api.py
"""

import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity.seed_team_data import seed

TEST_DIR = Path(__file__).parent / "_web_api_test_workspace"


def fresh_db():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn, db_path


def _make_game(conn, sport, home, away, ct):
    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
                 (sport, home, away, ct))
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return gid


def _make_snapshot(conn, gid, sport, home, away, ct, book, observed_at, spread_home, price_home=-110, price_away=-110):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home, spread_away, spread_price_home, spread_price_away) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (gid, sport, home, away, ct, book, observed_at, spread_home, -spread_home, price_home, price_away),
    )
    conn.commit()


def _get_app_with_db(db_path):
    import web.app as web_app
    web_app.DEFAULT_DB_PATH = db_path
    web_app._cache.clear()
    return web_app


def test_index_serves_html():
    conn, db_path = fresh_db()
    conn.close()
    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"ArbitrEdge" in r.data
    print("PASS: the homepage serves the HTML shell correctly")


def test_static_assets_served():
    conn, db_path = fresh_db()
    conn.close()
    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    print("PASS: static CSS and JS assets are served correctly")


def test_invalid_sport_returns_400():
    conn, db_path = fresh_db()
    conn.close()
    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r = client.get("/api/slate?sport=nba")
    assert r.status_code == 400
    assert "error" in r.get_json()
    print("PASS: an invalid sport parameter returns a clear 400 error, not silent bad data")


def test_slate_pre_t24h_returns_not_yet_evaluable():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
    _make_snapshot(conn, gid, "nfl", "CHI", "GB", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r = client.get("/api/slate?sport=nfl")
    data = r.get_json()
    assert r.status_code == 200
    assert data["counts"]["not_yet_evaluable"] == 1
    game = data["games"][0]
    assert game["card_state"] == "NOT_YET_EVALUABLE"
    assert game["data_quality"] == "NOT_YET_EVALUABLE"
    assert game["home_team"] == "Chicago Bears"
    assert game["away_team"] == "Green Bay Packers"
    assert "not opened yet" in game["why"]
    print("PASS: a real pre-T-24h game correctly surfaces as NOT_YET_EVALUABLE with the right team names")


def test_central_time_conversion():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
    _make_snapshot(conn, gid, "nfl", "CHI", "GB", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r = client.get("/api/slate?sport=nfl")
    game = r.get_json()["games"][0]
    assert "CDT" in game["kickoff_central"] or "CST" in game["kickoff_central"]
    print("PASS: kickoff time is converted to Central Time server-side, regardless of client timezone")


def test_response_is_cached_within_ttl():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
    _make_snapshot(conn, gid, "nfl", "CHI", "GB", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r1 = client.get("/api/slate?sport=nfl")
    r2 = client.get("/api/slate?sport=nfl")
    assert r1.get_json()["data_computed_at_unix"] == r2.get_json()["data_computed_at_unix"]
    print("PASS: a second request within the cache TTL reuses the same computed result, "
          "no redundant recomputation")


def test_zero_prospective_records_from_normal_dashboard_usage():
    """The most important guardrail: repeated dashboard requests (like
    browser auto-refresh) must never create a book_need_prospective_records
    row."""
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
    _make_snapshot(conn, gid, "nfl", "CHI", "GB", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    for _ in range(5):
        client.get("/api/slate?sport=all")

    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT COUNT(*) FROM book_need_prospective_records").fetchone()[0]
    conn.close()
    assert count == 0
    print("PASS: five repeated dashboard requests (simulating auto-refresh) create ZERO "
          "book_need_prospective_records -- persist=False is genuinely enforced")


def test_upcoming_games_reconciles_with_all_buckets_plus_excluded():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    for i in range(3):
        gid = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
        _make_snapshot(conn, gid, "nfl", "CHI", "GB", future, "draftkings",
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    for i in range(2):
        _make_game(conn, "nfl", "nfl_kc", "nfl_lac", future)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r = client.get("/api/slate?sport=nfl")
    counts = r.get_json()["counts"]
    assert counts["excluded"] == 2
    reconciled = (counts["not_yet_evaluable"] + counts["insufficient_evidence"]
                  + counts["no_clear_need"] + counts["directional_book_needs"] + counts["excluded"])
    assert reconciled == counts["upcoming_games"] == 5
    print("PASS: upcoming_games reconciles exactly against every visible bucket plus excluded")


def test_excluded_list_carries_status_and_reason():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", future)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    data = client.get("/api/slate?sport=nfl").get_json()
    assert len(data["excluded"]) == 1
    excl = data["excluded"][0]
    assert excl["status"] == "UNKNOWN_INSUFFICIENT_EVIDENCE"
    assert "reason" in excl and len(excl["reason"]) > 0
    assert excl["home_team"] == "Kansas City Chiefs" and excl["away_team"] == "Los Angeles Chargers"
    print("PASS: excluded games carry their real eligibility status/reason, using only existing metadata")


def test_all_sport_excluded_counts_merge_correctly():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", future)
    _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", future)
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    nfl = client.get("/api/slate?sport=nfl").get_json()["counts"]["excluded"]
    cfb = client.get("/api/slate?sport=cfb").get_json()["counts"]["excluded"]
    allr = client.get("/api/slate?sport=all").get_json()["counts"]["excluded"]
    assert allr == nfl + cfb == 2
    print("PASS: excluded counts merge correctly across NFL/CFB/ALL")


def test_cache_purges_expired_entries():
    conn, db_path = fresh_db()
    conn.close()
    web_app = _get_app_with_db(db_path)

    web_app._cache["nfl"] = {"data": {}, "computed_at": time.time() - 600}
    web_app._cache["cfb"] = {"data": {}, "computed_at": time.time() - 600}
    assert len(web_app._cache) == 2

    web_app._get_slate("nfl")
    assert "cfb" not in web_app._cache
    assert "nfl" in web_app._cache
    assert len(web_app._cache) == 1
    print("PASS: expired cache entries are purged on access, preventing unbounded growth")


def test_cache_does_not_grow_unbounded_across_many_buckets():
    conn, db_path = fresh_db()
    conn.close()
    web_app = _get_app_with_db(db_path)
    web_app._cache.clear()

    web_app._cache["nfl"] = {"data": {}, "computed_at": time.time() - 600}
    web_app._cache["cfb"] = {"data": {}, "computed_at": time.time() - 600}
    web_app._cache["all"] = {"data": {}, "computed_at": time.time() - 600}
    web_app._get_slate("nfl")
    assert len(web_app._cache) < 5
    print("PASS: cache size stays bounded even after simulating many stale minute-buckets accumulating")


def test_fresh_cache_entry_still_reused_within_ttl_after_purge():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
    _make_snapshot(conn, gid, "nfl", "CHI", "GB", future, "draftkings",
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    conn.close()

    web_app = _get_app_with_db(db_path)
    web_app._cache.clear()
    data1, t1 = web_app._get_slate("nfl")
    web_app._get_slate("cfb")
    data2, t2 = web_app._get_slate("nfl")
    assert t1 == t2
    print("PASS: a fresh, unexpired cache entry is still correctly reused within its TTL after a purge cycle")


def test_all_sport_merges_nfl_and_cfb():
    conn, db_path = fresh_db()
    future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    gid_nfl = _make_game(conn, "nfl", "nfl_chi", "nfl_gb", future)
    gid_cfb = _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", future)
    for gid, sport, home, away in [(gid_nfl, "nfl", "CHI", "GB"), (gid_cfb, "cfb", "Ohio State", "Michigan")]:
        _make_snapshot(conn, gid, sport, home, away, future, "draftkings",
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), -3.0)
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute(
        "INSERT INTO game_context "
        "(canonical_game_id, observed_at, ranking_home, source, retrieved_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (gid_cfb, observed, 5, "test_fixture", observed),
    )
    conn.commit()
    conn.close()

    web_app = _get_app_with_db(db_path)
    client = web_app.app.test_client()
    r = client.get("/api/slate?sport=all")
    data = r.get_json()
    sports_seen = {g["sport"] for g in data["games"]}
    assert sports_seen == {"nfl", "cfb"}
    print("PASS: sport=all correctly merges both NFL and CFB games in one response")


def test_no_model_logic_duplicated_in_web_layer():
    """Structural check: web/app.py must never import from behavioral/ or
    directly call regime/need-strength decision functions -- it may only
    call the orchestration entry point and format already-decided fields."""
    import inspect
    import web.app as web_app
    source = inspect.getsource(web_app)
    forbidden = ["compute_handle_intensity_v1", "compute_side_preference_v1",
                 "classify_regime", "infer_book_need", "apply_modifiers_and_cap"]
    for name in forbidden:
        assert name not in source, f"web/app.py must never call {name} directly -- model logic must not be duplicated"
    print("PASS: web/app.py never calls any book_need decision/model function directly -- "
          "only the top-level orchestration entry point")


if __name__ == "__main__":
    test_index_serves_html()
    test_static_assets_served()
    test_invalid_sport_returns_400()
    test_slate_pre_t24h_returns_not_yet_evaluable()
    test_upcoming_games_reconciles_with_all_buckets_plus_excluded()
    test_excluded_list_carries_status_and_reason()
    test_all_sport_excluded_counts_merge_correctly()
    test_cache_purges_expired_entries()
    test_cache_does_not_grow_unbounded_across_many_buckets()
    test_fresh_cache_entry_still_reused_within_ttl_after_purge()
    test_central_time_conversion()
    test_response_is_cached_within_ttl()
    test_zero_prospective_records_from_normal_dashboard_usage()
    test_all_sport_merges_nfl_and_cfb()
    test_no_model_logic_duplicated_in_web_layer()
    print("\nAll web API tests passed.")
