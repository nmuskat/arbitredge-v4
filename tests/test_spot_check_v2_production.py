"""
Tests for diagnostics/spot_check_v2_production.py

Run with: python tests/test_spot_check_v2_production.py
"""

import io
import shutil
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnostics.spot_check_v2_production import (  # noqa: E402
    get_regular_season_sample_games, get_permanently_missed_aug13_14_games, main, TALLY,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_spot_check_v2_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _future_ts(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_regular_season_sample_excludes_preseason_tagged_games():
    conn = fresh_conn()
    preseason_ct = "2026-08-16T00:00:00Z"
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_sea','nfl_dal', ?)", (preseason_ct,)
    )
    preseason_gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?, 'nfl_preseason', 'SEA','DAL', ?, 'draftkings', "
        "'2026-08-15T20:00:00Z', -3.0)", (preseason_gid, preseason_ct)
    )

    regular_ct = _future_ts(25)
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_kc','nfl_lac', ?)", (regular_ct,)
    )
    regular_gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()

    samples = get_regular_season_sample_games(conn, "nfl", limit=10)
    sample_ids = {s[0] for s in samples}

    assert preseason_gid not in sample_ids
    assert regular_gid in sample_ids
    conn.close()
    print("PASS: regular-season sampling correctly excludes preseason-tagged games "
          "(the exact bug caught during this script's own testing)")


def test_missed_games_query_only_finds_zero_observation_games():
    conn = fresh_conn()
    missed_ct = "2026-08-13T23:00:00Z"
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_car','nfl_buf', ?)", (missed_ct,)
    )
    missed_gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    has_data_ct = "2026-08-13T20:00:00Z"
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_kc','nfl_lac', ?)", (has_data_ct,)
    )
    has_data_gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?, 'nfl', 'KC','LAC', ?, 'draftkings', "
        "'2026-08-12T12:00:00Z', -3.0)", (has_data_gid, has_data_ct)
    )
    conn.commit()

    missed = get_permanently_missed_aug13_14_games(conn)
    missed_ids = {m[0] for m in missed}

    assert missed_gid in missed_ids
    assert has_data_gid not in missed_ids
    conn.close()
    print("PASS: the missed-games query correctly finds only genuine zero-observation games in the window")


def test_full_script_runs_clean_and_idempotent_across_reruns():
    conn = fresh_conn()
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_sea','nfl_dal','2026-08-16T00:00:00Z')"
    )
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for book, spread in [("draftkings", -2.5), ("fanduel", -3.0)]:
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
            "book, observed_at, spread_home) VALUES (?, 'nfl_preseason', 'SEA','DAL', "
            "'2026-08-16T00:00:00Z', ?, '2026-08-15T20:00:00Z', ?)",
            (gid, book, spread),
        )
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_car','nfl_buf','2026-08-13T23:00:00Z')"
    )
    conn.commit()
    conn.close()

    import diagnostics.spot_check_v2_production as m
    original_db_path = m.DEFAULT_DB_PATH
    m.DEFAULT_DB_PATH = TEST_DIR / "arbitredge.db"
    try:
        for tally_key in TALLY:
            TALLY[tally_key] = 0
        out1 = io.StringIO()
        with redirect_stdout(out1):
            main()
        summary1 = dict(TALLY)

        for tally_key in TALLY:
            TALLY[tally_key] = 0
        out2 = io.StringIO()
        with redirect_stdout(out2):
            main()
        summary2 = dict(TALLY)
    finally:
        m.DEFAULT_DB_PATH = original_db_path

    assert summary1["FAIL"] == 0, f"first run had real failures: {summary1}"
    assert summary2["FAIL"] == 0, f"second run had real failures: {summary2}"
    assert summary1 == summary2, f"reruns must produce identical categorized results: {summary1} vs {summary2}"

    conn = sqlite3.connect(str(TEST_DIR / "arbitredge.db"))
    v2_count = conn.execute("SELECT COUNT(*) FROM engineered_features_v2").fetchone()[0]
    conn.close()
    assert v2_count == 1, f"expected exactly 1 persisted V2 row after two full script runs, got {v2_count}"
    print("PASS: the full script runs clean (zero FAIL) and is truly idempotent across reruns -- "
          "identical categorized results, no duplicate persisted rows")


if __name__ == "__main__":
    test_regular_season_sample_excludes_preseason_tagged_games()
    test_missed_games_query_only_finds_zero_observation_games()
    test_full_script_runs_clean_and_idempotent_across_reruns()
    print("\nAll spot_check_v2_production tests passed.")
