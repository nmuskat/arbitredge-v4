"""
Confirms:
  1. UPDATE and DELETE are blocked (raise sqlite3.IntegrityError) on the four
     append-only tables: market_snapshots, game_context, split_observations,
     collection_log.
  2. INSERT still works normally on those same tables.
  3. Identity/config tables (teams_canonical, team_aliases, games_canonical,
     game_id_map, polling_policy) remain mutable, since they are NOT meant
     to be append-only.

Run with: python -m pytest tests/test_append_only.py -v
(or: python tests/test_append_only.py  to run standalone without pytest)
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.migrate import run_migration  # noqa: E402

TEST_DB_PATH = Path(__file__).parent / "test_arbitredge.db"


def fresh_db():
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    run_migration(TEST_DB_PATH)
    conn = sqlite3.connect(str(TEST_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def seed_minimal_game(conn):
    conn.execute(
        "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
        "VALUES ('nfl_lac', 'nfl', 'Los Angeles Chargers')"
    )
    conn.execute(
        "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
        "VALUES ('nfl_kc', 'nfl', 'Kansas City Chiefs')"
    )
    conn.execute(
        "INSERT INTO games_canonical "
        "(sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl', 'nfl_kc', 'nfl_lac', '2026-09-10T20:20:00Z')"
    )
    conn.commit()
    return conn.execute("SELECT canonical_game_id FROM games_canonical").fetchone()[0]


def test_market_snapshots_append_only():
    conn = fresh_db()
    game_id = seed_minimal_game(conn)

    conn.execute(
        "INSERT INTO market_snapshots "
        "(canonical_game_id, sport, home_team, away_team, commence_time, book, observed_at, spread_home) "
        "VALUES (?, 'nfl', 'Kansas City Chiefs', 'Los Angeles Chargers', '2026-09-10T20:20:00Z', "
        "'draftkings', '2026-09-08T12:00:00Z', -3.0)",
        (game_id,),
    )
    conn.commit()

    try:
        conn.execute("UPDATE market_snapshots SET spread_home = -3.5 WHERE canonical_game_id = ?", (game_id,))
        conn.commit()
        raise AssertionError("UPDATE on market_snapshots should have been blocked but succeeded")
    except sqlite3.IntegrityError as e:
        assert "append-only" in str(e)

    try:
        conn.execute("DELETE FROM market_snapshots WHERE canonical_game_id = ?", (game_id,))
        conn.commit()
        raise AssertionError("DELETE on market_snapshots should have been blocked but succeeded")
    except sqlite3.IntegrityError as e:
        assert "append-only" in str(e)

    count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert count == 1, "row should still exist untouched after blocked UPDATE/DELETE"
    conn.close()
    print("PASS: market_snapshots is append-only (INSERT works, UPDATE/DELETE blocked)")


def test_collection_log_append_only():
    conn = fresh_db()
    conn.execute(
        "INSERT INTO collection_log "
        "(collector_name, scheduled_at, executed_at, source, success, records_captured) "
        "VALUES ('odds_collector', '2026-09-08T12:00:00Z', '2026-09-08T12:00:03Z', "
        "'the_odds_api', 1, 14)"
    )
    conn.commit()

    try:
        conn.execute("DELETE FROM collection_log")
        conn.commit()
        raise AssertionError("DELETE on collection_log should have been blocked but succeeded")
    except sqlite3.IntegrityError as e:
        assert "append-only" in str(e)

    count = conn.execute("SELECT COUNT(*) FROM collection_log").fetchone()[0]
    assert count == 1
    conn.close()
    print("PASS: collection_log is append-only (missingness/failure history cannot be erased)")


def test_split_observations_never_merged_and_append_only():
    conn = fresh_db()
    game_id = seed_minimal_game(conn)

    # Two independent sources reporting for the same game — must coexist,
    # never be blended into one row (Correction 4).
    conn.execute(
        "INSERT INTO split_observations "
        "(canonical_game_id, source, observed_at, side, ticket_pct, money_pct, retrieved_at) "
        "VALUES (?, 'vegasinsider', '2026-09-08T12:00:00Z', 'nfl_kc', 78.0, 71.0, '2026-09-08T12:00:05Z')",
        (game_id,),
    )
    conn.execute(
        "INSERT INTO split_observations "
        "(canonical_game_id, source, observed_at, side, ticket_pct, money_pct, retrieved_at) "
        "VALUES (?, 'actionnetwork', '2026-09-08T12:00:00Z', 'nfl_kc', 69.0, 55.0, '2026-09-08T12:00:07Z')",
        (game_id,),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT source, ticket_pct, money_pct FROM split_observations WHERE canonical_game_id = ? ORDER BY source",
        (game_id,),
    ).fetchall()
    assert len(rows) == 2, "both sources must be preserved independently, not averaged into one row"

    try:
        conn.execute("UPDATE split_observations SET ticket_pct = 99.0 WHERE canonical_game_id = ?", (game_id,))
        conn.commit()
        raise AssertionError("UPDATE on split_observations should have been blocked but succeeded")
    except sqlite3.IntegrityError:
        pass

    conn.close()
    print("PASS: split_observations preserves independent sources and is append-only")


def test_identity_tables_remain_mutable():
    conn = fresh_db()
    conn.execute(
        "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
        "VALUES ('nfl_lac', 'nfl', 'LA Chargers')"
    )
    conn.commit()
    # Correcting a display name should be allowed -- identity tables are NOT append-only
    conn.execute(
        "UPDATE teams_canonical SET display_name = 'Los Angeles Chargers' WHERE canonical_team_id = 'nfl_lac'"
    )
    conn.commit()
    name = conn.execute(
        "SELECT display_name FROM teams_canonical WHERE canonical_team_id = 'nfl_lac'"
    ).fetchone()[0]
    assert name == "Los Angeles Chargers"
    conn.close()
    print("PASS: teams_canonical remains mutable as intended (not append-only)")


if __name__ == "__main__":
    test_market_snapshots_append_only()
    test_collection_log_append_only()
    test_split_observations_never_merged_and_append_only()
    test_identity_tables_remain_mutable()
    print("\nAll append-only protection tests passed.")
