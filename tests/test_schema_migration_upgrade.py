"""
Tests for db/migrate.py's handling of the game_context.raw_response_id ->
context_raw_response_links schema change against an EXISTING database
created under the old schema.

Reproduces the exact sequence requested:
  1. Create a database using the previous schema (game_context has
     raw_response_id).
  2. Apply the new migration (db.migrate.run_migration).
  3. Inspect PRAGMA table_info(game_context).
  4. Confirm raw_response_id is gone.
  5. Confirm context_raw_response_links exists with expected FKs/triggers.
  6. Confirm all existing non-context data remains intact.
  7. Confirm the migration is idempotent when run again.

The legacy schema fixture below is derived directly from the CURRENT
schema.sql/triggers.sql via string substitution of the known, exact delta
(the game_context tail and the context_raw_response_links table/index/
trigger), not retyped from memory -- an assertion confirms the
substitution actually matched real file content, so this test cannot pass
silently against a fixture that has drifted from reality.

Run with: python tests/test_schema_migration_upgrade.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.migrate import run_migration, DB_DIR  # noqa: E402

TEST_DIR = Path(__file__).parent / "_schema_migration_test_workspace"

CURRENT_SCHEMA = (DB_DIR / "schema.sql").read_text()
CURRENT_TRIGGERS = (DB_DIR / "triggers.sql").read_text()

NEW_BLOCK = """    parser_version                                        TEXT,
    source_methodology_note                                 TEXT
    -- NOTE: no direct raw_response_id column here -- a single game_context
    -- observation can be assembled from MULTIPLE raw responses (e.g. CFB:
    -- scoreboard + rankings), so provenance links live in the normalized
    -- context_raw_response_links table below instead of a single FK.
);

-- Normalized provenance links: one game_context observation can reference
-- multiple raw responses, each tagged with its role in producing that
-- observation (e.g. 'scoreboard', 'rankings'). Append-only, like every
-- other raw/provenance table in this project.
CREATE TABLE IF NOT EXISTS context_raw_response_links (
    link_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id        INTEGER NOT NULL REFERENCES game_context(context_id),
    raw_response_id   INTEGER NOT NULL REFERENCES raw_responses(raw_response_id),
    role              TEXT NOT NULL,   -- e.g. 'scoreboard', 'rankings'
    UNIQUE (context_id, raw_response_id, role)
);"""

LEGACY_BLOCK = """    parser_version                                        TEXT,
    source_methodology_note                                 TEXT,
    raw_response_id                                           INTEGER REFERENCES raw_responses(raw_response_id)
);"""

NEW_INDEX_LINE = "CREATE INDEX IF NOT EXISTS idx_context_raw_response_links ON context_raw_response_links(context_id);\n"

NEW_TRIGGER_BLOCK = """-- ---------- context_raw_response_links ----------
CREATE TRIGGER IF NOT EXISTS trg_context_raw_response_links_no_update
BEFORE UPDATE ON context_raw_response_links
BEGIN
    SELECT RAISE(ABORT, 'context_raw_response_links is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_context_raw_response_links_no_delete
BEFORE DELETE ON context_raw_response_links
BEGIN
    SELECT RAISE(ABORT, 'context_raw_response_links is append-only: DELETE is not permitted');
END;

-- ---------- game_results ----------
CREATE TRIGGER IF NOT EXISTS trg_game_results_no_update
BEFORE UPDATE ON game_results
BEGIN
    SELECT RAISE(ABORT, 'game_results is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_game_results_no_delete
BEFORE DELETE ON game_results
BEGIN
    SELECT RAISE(ABORT, 'game_results is append-only: DELETE is not permitted');
END;

-- ---------- game_results_raw_response_links ----------
CREATE TRIGGER IF NOT EXISTS trg_game_results_raw_response_links_no_update
BEFORE UPDATE ON game_results_raw_response_links
BEGIN
    SELECT RAISE(ABORT, 'game_results_raw_response_links is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_game_results_raw_response_links_no_delete
BEFORE DELETE ON game_results_raw_response_links
BEGIN
    SELECT RAISE(ABORT, 'game_results_raw_response_links is append-only: DELETE is not permitted');
END;

-- ---------- engineered_features ----------
CREATE TRIGGER IF NOT EXISTS trg_engineered_features_no_update
BEFORE UPDATE ON engineered_features
BEGIN
    SELECT RAISE(ABORT, 'engineered_features is append-only: UPDATE is not permitted -- bump feature_set_version instead');
END;

CREATE TRIGGER IF NOT EXISTS trg_engineered_features_no_delete
BEFORE DELETE ON engineered_features
BEGIN
    SELECT RAISE(ABORT, 'engineered_features is append-only: DELETE is not permitted');
END;

-- ---------- validation_holdout_membership ----------
CREATE TRIGGER IF NOT EXISTS trg_validation_holdout_membership_no_update
BEFORE UPDATE ON validation_holdout_membership
BEGIN
    SELECT RAISE(ABORT, 'validation_holdout_membership is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_validation_holdout_membership_no_delete
BEFORE DELETE ON validation_holdout_membership
BEGIN
    SELECT RAISE(ABORT, 'validation_holdout_membership is append-only: DELETE is not permitted');
END;

-- ---------- validation_holdout_access_log ----------
CREATE TRIGGER IF NOT EXISTS trg_validation_holdout_access_log_no_update
BEFORE UPDATE ON validation_holdout_access_log
BEGIN
    SELECT RAISE(ABORT, 'validation_holdout_access_log is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_validation_holdout_access_log_no_delete
BEFORE DELETE ON validation_holdout_access_log
BEGIN
    SELECT RAISE(ABORT, 'validation_holdout_access_log is append-only: DELETE is not permitted');
END;

-- ---------- validation_experiments ----------
CREATE TRIGGER IF NOT EXISTS trg_validation_experiments_no_update
BEFORE UPDATE ON validation_experiments
BEGIN
    SELECT RAISE(ABORT, 'validation_experiments is append-only: UPDATE is not permitted');
END;

CREATE TRIGGER IF NOT EXISTS trg_validation_experiments_no_delete
BEFORE DELETE ON validation_experiments
BEGIN
    SELECT RAISE(ABORT, 'validation_experiments is append-only: DELETE is not permitted');
END;

-- ---------- game_context ----------"""

LEGACY_TRIGGER_TAIL = "-- ---------- game_context ----------"


def _build_legacy_schema() -> str:
    assert NEW_BLOCK in CURRENT_SCHEMA, (
        "Fixture mismatch: expected new-shape block not found verbatim in current "
        "schema.sql -- schema.sql changed since this fixture was written."
    )
    legacy = CURRENT_SCHEMA.replace(NEW_BLOCK, LEGACY_BLOCK)
    assert NEW_INDEX_LINE in legacy
    legacy = legacy.replace(NEW_INDEX_LINE, "")
    return legacy


def _build_legacy_triggers() -> str:
    assert NEW_TRIGGER_BLOCK in CURRENT_TRIGGERS, (
        "Fixture mismatch: expected context_raw_response_links trigger block not "
        "found verbatim in current triggers.sql."
    )
    return CURRENT_TRIGGERS.replace(NEW_TRIGGER_BLOCK, LEGACY_TRIGGER_TAIL)


def _fresh_workspace() -> Path:
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    return TEST_DIR / "arbitredge.db"


def test_full_upgrade_sequence_from_legacy_schema():
    db_path = _fresh_workspace()
    legacy_schema = _build_legacy_schema()
    legacy_triggers = _build_legacy_triggers()

    # ---- Step 1: create a database using the PREVIOUS schema ----
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(legacy_schema)
    conn.executescript(legacy_triggers)

    legacy_cols = [r[1] for r in conn.execute("PRAGMA table_info(game_context)").fetchall()]
    assert "raw_response_id" in legacy_cols, "test fixture setup failed: legacy column missing"
    has_new_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='context_raw_response_links'"
    ).fetchone()
    assert has_new_table is None, "test fixture setup failed: new table should not exist yet"

    conn.execute(
        "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
        "VALUES ('nfl_kc', 'nfl', 'Kansas City Chiefs')"
    )
    conn.execute(
        "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
        "VALUES ('nfl_lac', 'nfl', 'Los Angeles Chargers')"
    )
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl', 'nfl_kc', 'nfl_lac', '2026-09-10T20:20:00Z')"
    )
    canonical_game_id = conn.execute("SELECT canonical_game_id FROM games_canonical").fetchone()[0]
    conn.execute(
        "INSERT INTO raw_responses (collector_name, source, request_url, retrieved_at, http_status, response_body) "
        "VALUES ('odds_collector', 'the_odds_api', 'https://example.com/odds', '2026-09-08T12:00:00Z', 200, '{}')"
    )
    conn.execute(
        "INSERT INTO market_snapshots "
        "(canonical_game_id, sport, home_team, away_team, commence_time, book, observed_at, spread_home) "
        "VALUES (?, 'nfl', 'Kansas City Chiefs', 'Los Angeles Chargers', '2026-09-10T20:20:00Z', "
        "'draftkings', '2026-09-08T12:00:00Z', -3.0)",
        (canonical_game_id,),
    )
    conn.execute(
        "INSERT INTO collection_log (collector_name, scheduled_at, executed_at, source, success, records_captured) "
        "VALUES ('odds_collector', '2026-09-08T12:00:00Z', '2026-09-08T12:00:01Z', 'the_odds_api', 1, 1)"
    )
    raw_response_id = conn.execute("SELECT raw_response_id FROM raw_responses").fetchone()[0]
    conn.execute(
        "INSERT INTO game_context "
        "(canonical_game_id, observed_at, ranking_home, ranking_away, record_home, record_away, "
        "tv_network, is_primetime, kickoff_window, source, retrieved_at, parser_version, raw_response_id) "
        "VALUES (?, '2026-09-08T12:00:00Z', NULL, NULL, '3-1', '2-2', 'CBS', NULL, 'evening', "
        "'espn', '2026-09-08T12:00:00Z', 'context_collector_v1', ?)",
        (canonical_game_id, raw_response_id),
    )
    conn.commit()
    conn.close()
    print("Step 1 OK: legacy database created with pre-existing data across multiple tables, "
          "including one legacy-shape game_context row.")

    # ---- Step 2: apply the NEW migration ----
    run_migration(db_path)
    print("Step 2 OK: run_migration executed without error.")

    # ---- Step 3 & 4 ----
    conn = sqlite3.connect(str(db_path))
    new_cols = [r[1] for r in conn.execute("PRAGMA table_info(game_context)").fetchall()]
    print(f"Step 3: PRAGMA table_info(game_context) columns = {new_cols}")
    assert "raw_response_id" not in new_cols, "raw_response_id must be gone after migration"
    expected_cols = {
        "context_id", "canonical_game_id", "observed_at", "ranking_home", "ranking_away",
        "record_home", "record_away", "tv_network", "is_primetime", "kickoff_window",
        "is_rivalry", "playoff_relevance", "google_trends_home", "google_trends_away",
        "trends_observed_at", "source", "source_url", "retrieved_at", "source_reported_at",
        "raw_value", "parser_version", "source_methodology_note",
    }
    assert set(new_cols) == expected_cols, f"unexpected column set: {set(new_cols)}"
    print("Step 4 OK: raw_response_id confirmed absent; new column set matches expected shape exactly.")

    # ---- Step 5 ----
    link_table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='context_raw_response_links'"
    ).fetchone()
    assert link_table_sql is not None, "context_raw_response_links table must exist"
    assert "REFERENCES game_context(context_id)" in link_table_sql[0]
    assert "REFERENCES raw_responses(raw_response_id)" in link_table_sql[0]

    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_context_raw_response_links%'"
    ).fetchall()
    trigger_names = {t[0] for t in triggers}
    assert trigger_names == {
        "trg_context_raw_response_links_no_update", "trg_context_raw_response_links_no_delete",
    }
    conn.execute(
        "INSERT INTO context_raw_response_links (context_id, raw_response_id, role) VALUES (1, 1, 'scoreboard')"
    )
    conn.commit()
    try:
        conn.execute("DELETE FROM context_raw_response_links")
        conn.commit()
        raise AssertionError("DELETE on context_raw_response_links should have been blocked")
    except sqlite3.IntegrityError:
        pass
    print("Step 5 OK: context_raw_response_links exists with correct FKs, and its "
          "append-only triggers are present and actually functional.")

    # ---- Step 6 ----
    team = conn.execute(
        "SELECT display_name FROM teams_canonical WHERE canonical_team_id = 'nfl_kc'"
    ).fetchone()
    assert team == ("Kansas City Chiefs",)
    game = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert game == 1
    snapshot = conn.execute("SELECT book, spread_home FROM market_snapshots").fetchone()
    assert snapshot == ("draftkings", -3.0)
    log = conn.execute("SELECT success, records_captured FROM collection_log").fetchone()
    assert log == (1, 1)
    raw_resp = conn.execute("SELECT source FROM raw_responses").fetchone()
    assert raw_resp == ("the_odds_api",)
    ctx_row = conn.execute(
        "SELECT record_home, record_away, tv_network, kickoff_window FROM game_context"
    ).fetchone()
    assert ctx_row == ("3-1", "2-2", "CBS", "evening")
    print("Step 6 OK: all pre-existing data across teams_canonical, games_canonical, "
          "market_snapshots, collection_log, raw_responses, and game_context itself "
          "survived the migration intact.")

    conn.close()

    # ---- Step 7 ----
    run_migration(db_path)
    conn = sqlite3.connect(str(db_path))
    cols_again = [r[1] for r in conn.execute("PRAGMA table_info(game_context)").fetchall()]
    assert set(cols_again) == expected_cols, "second migration run must not change the schema further"
    ctx_row_count = conn.execute("SELECT COUNT(*) FROM game_context").fetchone()[0]
    assert ctx_row_count == 1, "second migration run must not duplicate or lose data"
    backup_table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%legacy_backup%'"
    ).fetchone()
    assert backup_table_exists is None, "no leftover backup table should remain"
    conn.close()
    print("Step 7 OK: running the migration again against an already-migrated database "
          "is a clean no-op -- same schema, same row count, no leftover backup table.")


def test_brand_new_database_unaffected():
    db_path = _fresh_workspace()
    run_migration(db_path)
    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(game_context)").fetchall()]
    assert "raw_response_id" not in cols
    link_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='context_raw_response_links'"
    ).fetchone()
    assert link_table is not None
    conn.close()
    print("PASS: a brand-new database migrates directly to the new shape, no legacy-migration path triggered")


def test_market_snapshots_legacy_constraint_migration_preserves_data():
    """
    Reproduces the exact real production scenario: a market_snapshots
    table created under the OLD CHECK (sport IN ('nfl','cfb')) constraint,
    already holding real data, migrated forward after schema.sql was
    updated to add 'nfl_preseason'. CREATE TABLE IF NOT EXISTS alone is a
    no-op against this -- proves the versioned rebuild in migrate.py
    actually fixes the live constraint, preserves every existing row, and
    that the append-only triggers survive the rename/rebuild.
    """
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "legacy_market_snapshots.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE games_canonical (
            canonical_game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
            home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL, commence_time TEXT NOT NULL
        );
        CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_game_id INTEGER NOT NULL,
            sport TEXT NOT NULL CHECK (sport IN ('nfl', 'cfb')),
            home_team TEXT NOT NULL, away_team TEXT NOT NULL, commence_time TEXT NOT NULL,
            book TEXT NOT NULL, observed_at TEXT NOT NULL, book_last_update TEXT,
            spread_home REAL, spread_away REAL, spread_price_home REAL, spread_price_away REAL,
            moneyline_home REAL, moneyline_away REAL, total_points REAL,
            total_price_over REAL, total_price_under REAL, parser_version TEXT, raw_response_id INTEGER
        );
        CREATE TRIGGER trg_market_snapshots_no_update BEFORE UPDATE ON market_snapshots
        BEGIN SELECT RAISE(ABORT, 'append-only'); END;
        CREATE TRIGGER trg_market_snapshots_no_delete BEFORE DELETE ON market_snapshots
        BEGIN SELECT RAISE(ABORT, 'append-only'); END;
    """)
    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
                  "VALUES ('nfl','nfl_kc','nfl_lac','2026-09-10T20:00:00Z')")
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (1, 'nfl', 'KC', 'LAC', '2026-09-10T20:00:00Z', "
        "'draftkings', '2026-09-09T20:00:00Z', -3.0)"
    )
    conn.commit()
    conn.close()

    from db.migrate import run_migration
    run_migration(db_path)

    conn = sqlite3.connect(str(db_path))
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='market_snapshots'").fetchone()[0]
    assert "nfl_preseason" in ddl, "the live constraint must be rebuilt to include nfl_preseason"

    preserved = conn.execute("SELECT sport, book, spread_home FROM market_snapshots").fetchall()
    assert preserved == [("nfl", "draftkings", -3.0)], "existing real data must be preserved exactly"

    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (1, 'nfl_preseason', 'KC', 'LAC', "
        "'2026-08-15T20:00:00Z', 'draftkings', '2026-08-14T20:00:00Z', -3.0)"
    )  # must not raise

    try:
        conn.execute("DELETE FROM market_snapshots WHERE snapshot_id = 1")
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "append-only DELETE protection must survive the migration"

    conn.close()
    print("PASS: a legacy-constraint market_snapshots table with real data is correctly rebuilt -- "
          "new constraint active, all existing rows preserved, append-only triggers intact")


def test_v2_tables_created_correctly_against_legacy_production_style_database():
    """
    A production-style database predating V2 entirely (manually constructed,
    same technique as the market_snapshots legacy-constraint test above --
    _build_legacy_schema() is the wrong fixture here since it's derived
    from the CURRENT schema.sql and already contains V2's tables) with
    real pre-existing data must gain both new V2 tables, correctly shaped
    with working append-only triggers, via a normal migration run --
    without disturbing any existing table's data.
    """
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "pre_v2_production.db"

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE teams_canonical (
            canonical_team_id TEXT PRIMARY KEY, sport TEXT NOT NULL, display_name TEXT NOT NULL
        );
        CREATE TABLE games_canonical (
            canonical_game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL, home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL, commence_time TEXT NOT NULL
        );
        CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_game_id INTEGER NOT NULL, sport TEXT NOT NULL,
            home_team TEXT NOT NULL, away_team TEXT NOT NULL, commence_time TEXT NOT NULL,
            book TEXT NOT NULL, observed_at TEXT NOT NULL, book_last_update TEXT,
            spread_home REAL, spread_away REAL, spread_price_home REAL, spread_price_away REAL,
            moneyline_home REAL, moneyline_away REAL, total_points REAL,
            total_price_over REAL, total_price_under REAL, parser_version TEXT, raw_response_id INTEGER
        );
        CREATE TRIGGER trg_market_snapshots_no_update BEFORE UPDATE ON market_snapshots
        BEGIN SELECT RAISE(ABORT, 'append-only'); END;
        CREATE TRIGGER trg_market_snapshots_no_delete BEFORE DELETE ON market_snapshots
        BEGIN SELECT RAISE(ABORT, 'append-only'); END;
    """)
    conn.execute("INSERT INTO teams_canonical VALUES ('nfl_kc', 'nfl', 'Kansas City Chiefs')")
    conn.execute("INSERT INTO teams_canonical VALUES ('nfl_lac', 'nfl', 'Los Angeles Chargers')")
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl', 'nfl_kc', 'nfl_lac', '2026-09-10T20:20:00Z')"
    )
    canonical_game_id = conn.execute("SELECT canonical_game_id FROM games_canonical").fetchone()[0]
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?, 'nfl', 'Kansas City Chiefs', 'Los Angeles Chargers', "
        "'2026-09-10T20:20:00Z', 'draftkings', '2026-09-08T12:00:00Z', -3.0)",
        (canonical_game_id,),
    )
    conn.commit()
    conn.close()

    from db.migrate import run_migration
    run_migration(db_path)

    conn = sqlite3.connect(str(db_path))
    for table in ("market_state_by_cutoff", "engineered_features_v2"):
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        assert exists is not None, f"{table} must exist after migration"

    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND "
        "(name LIKE '%market_state_by_cutoff%' OR name LIKE '%engineered_features_v2%')"
    ).fetchall()
    assert len(triggers) == 4, "both new tables need their full pair of append-only triggers"

    post_migration_snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert post_migration_snapshot_count == 1, "pre-existing market_snapshots data must be completely undisturbed"

    from features.market_state_as_of import persist_market_state_as_of
    result = persist_market_state_as_of(conn, canonical_game_id, "2026-09-09T00:00:00Z", "draftkings")
    assert result == "new"

    conn.close()
    print("PASS: V2 tables are correctly created against a legacy/production-style database with real "
          "pre-existing data, fully functional, with pre-existing data completely undisturbed")


if __name__ == "__main__":
    test_full_upgrade_sequence_from_legacy_schema()
    test_brand_new_database_unaffected()
    test_market_snapshots_legacy_constraint_migration_preserves_data()
    test_v2_tables_created_correctly_against_legacy_production_style_database()
    print("\nAll schema migration upgrade tests passed.")
