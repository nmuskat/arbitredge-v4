"""
sportsbook_v4/schema.py -- V4 tables. Applied ONLY to a V4 database file.

All tables are append-only (BEFORE UPDATE / BEFORE DELETE triggers raise).
Nothing here touches, alters, or depends on any V3/production table, so
applying it to a fresh file has no migration implications for production.
ensure_schema() refuses to run against a database that already contains
the production `market_snapshots` table unless explicitly allowed, as a
guard against pointing V4 at the production database by mistake.
"""

SCHEMA_VERSION = 2  # m2: v4_run.rng_key (common random numbers); nothing deployed, no migration

DDL = """
CREATE TABLE IF NOT EXISTS v4_schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v4_population (
    population_id   TEXT PRIMARY KEY,
    sport           TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
    params_version  TEXT NOT NULL,
    params_hash     TEXT NOT NULL,
    bettor_count    INTEGER NOT NULL CHECK (bettor_count > 0),
    team_universe   TEXT NOT NULL,          -- JSON list used for loyalty draws
    population_hash TEXT NOT NULL,          -- hash over every bettor row
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v4_bettor (
    population_id   TEXT NOT NULL REFERENCES v4_population(population_id),
    bettor_index    INTEGER NOT NULL CHECK (bettor_index >= 0),
    bettor_uid      TEXT NOT NULL UNIQUE,
    archetype       TEXT NOT NULL,
    bankroll        REAL NOT NULL CHECK (bankroll > 0),
    traits_json     TEXT NOT NULL,
    PRIMARY KEY (population_id, bettor_index)
);

CREATE TABLE IF NOT EXISTS v4_run (
    run_id            TEXT PRIMARY KEY,
    population_id     TEXT NOT NULL REFERENCES v4_population(population_id),
    sport             TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
    slate_id          TEXT NOT NULL,
    window_start      TEXT NOT NULL,
    window_end        TEXT NOT NULL,
    games_json        TEXT NOT NULL,
    engine_version    TEXT NOT NULL,
    params_hash       TEXT NOT NULL,
    market_source_id  TEXT NOT NULL,
    rng_key           TEXT NOT NULL,          -- seeds arrivals/decisions; excludes params + market source
    created_at        TEXT NOT NULL
);

-- One row per committed clock advance. (run_id, seq) is contiguous and
-- interval_start of seq n+1 == interval_end of seq n (enforced in code
-- inside the same transaction that writes the wagers).
CREATE TABLE IF NOT EXISTS v4_interval (
    run_id          TEXT NOT NULL REFERENCES v4_run(run_id),
    seq             INTEGER NOT NULL CHECK (seq >= 0),
    interval_start  TEXT NOT NULL,
    interval_end    TEXT NOT NULL,
    arrivals        INTEGER NOT NULL,
    accepted        INTEGER NOT NULL,
    passes_json     TEXT NOT NULL,
    wagers_hash     TEXT NOT NULL,
    data_complete_through TEXT NOT NULL,
    committed_at    TEXT NOT NULL,
    PRIMARY KEY (run_id, seq),
    UNIQUE (run_id, interval_start)
);

CREATE TABLE IF NOT EXISTS v4_wager (
    wager_id            TEXT PRIMARY KEY,   -- sha256(run_id|bettor_uid|arrival_seq)
    run_id              TEXT NOT NULL REFERENCES v4_run(run_id),
    interval_seq        INTEGER NOT NULL,
    population_id       TEXT NOT NULL,
    bettor_index        INTEGER NOT NULL,
    bettor_uid          TEXT NOT NULL,
    archetype           TEXT NOT NULL,
    arrival_seq         INTEGER NOT NULL CHECK (arrival_seq >= 0),
    canonical_game_id   TEXT NOT NULL,
    sport               TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
    market              TEXT NOT NULL CHECK (market = 'spread'),
    side                TEXT NOT NULL CHECK (side IN ('home','away')),
    team_id             TEXT NOT NULL,
    accepted_line       REAL NOT NULL,      -- signed for the side taken
    accepted_home_spread REAL NOT NULL,
    accepted_price      REAL NOT NULL CHECK (accepted_price >= 100 OR accepted_price <= -100),
    stake               REAL NOT NULL CHECK (stake > 0),
    accepted_at         TEXT NOT NULL,
    offer_as_of         TEXT NOT NULL,      -- market cutoff the offered line was built from
    quote_refs_json     TEXT NOT NULL,      -- books + snapshot ids behind the offered number
    decision_json       TEXT NOT NULL,      -- probabilities / edge used (audit)
    engine_version      TEXT NOT NULL,
    params_hash         TEXT NOT NULL,
    UNIQUE (run_id, bettor_uid, arrival_seq),
    CHECK ((side = 'home' AND accepted_line = accepted_home_spread)
        OR (side = 'away' AND accepted_line = -accepted_home_spread))
);
CREATE INDEX IF NOT EXISTS idx_v4_wager_game_time ON v4_wager (run_id, canonical_game_id, accepted_at);
CREATE INDEX IF NOT EXISTS idx_v4_wager_bettor ON v4_wager (run_id, bettor_index);

CREATE TABLE IF NOT EXISTS v4_prospective_decision (
    decision_id        TEXT PRIMARY KEY,
    canonical_game_id  TEXT NOT NULL,
    sport              TEXT NOT NULL,
    decision_point     TEXT NOT NULL,
    policy_version     TEXT NOT NULL,
    decided_at         TEXT NOT NULL,
    kickoff            TEXT NOT NULL,
    run_id             TEXT NOT NULL,
    ledger_watermark   TEXT NOT NULL,
    financial_need_side TEXT,
    official_play      TEXT NOT NULL CHECK (official_play IN ('PLAY','PASS')),
    play_side          TEXT,
    play_line          REAL,
    play_price         REAL,
    assessment_json    TEXT NOT NULL,
    engine_version     TEXT NOT NULL,
    params_hash        TEXT NOT NULL,
    record_hash        TEXT NOT NULL,
    UNIQUE (canonical_game_id, policy_version, decision_point),
    CHECK (decided_at < kickoff)
);
"""

_APPEND_ONLY_TABLES = (
    "v4_population", "v4_bettor", "v4_run", "v4_interval", "v4_wager", "v4_prospective_decision",
)


def _trigger_sql(table):
    return (
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;\n"
        f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;\n"
    )


class ProductionDatabaseGuard(RuntimeError):
    pass


def ensure_schema(conn, allow_shared_database=False):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "market_snapshots" in tables and not allow_shared_database:
        raise ProductionDatabaseGuard(
            "refusing to create V4 tables in a database that contains market_snapshots "
            "(looks like the production/market DB). Use a separate V4 database file."
        )
    conn.executescript(DDL + "".join(_trigger_sql(t) for t in _APPEND_ONLY_TABLES))
    conn.execute(
        "INSERT OR IGNORE INTO v4_schema_meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
