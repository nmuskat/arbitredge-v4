"""
tests/test_population_persistence.py

Phase 1. Standalone-runnable: python tests/test_population_persistence.py

Builds its own minimal throwaway sqlite database (games_canonical +
market_snapshots, matching the real production schema's column names)
rather than depending on db.migrate, so this test has no dependency on
anything outside this repository snapshot.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.population import (
    ensure_population, get_profiles, run_simulation, ensure_schema, DEFAULT_BETTOR_COUNT,
)

TEST_DB_PATH = Path(__file__).parent / "_population_test_workspace" / "test_v3.db"


def fresh_db():
    TEST_DB_PATH.parent.mkdir(exist_ok=True)
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    conn = sqlite3.connect(str(TEST_DB_PATH))
    conn.executescript(
        """
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
        """
    )
    conn.commit()
    return conn


def seed_game(conn, sport, home, away, commence_time, book_lines):
    """book_lines: list of (book, observed_at, spread_home, price_home, price_away)."""
    cur = conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES (?, ?, ?, ?)",
        (sport, home, away, commence_time),
    )
    gid = cur.lastrowid
    for book, observed_at, spread_home, price_home, price_away in book_lines:
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, "
            "commence_time, book, observed_at, spread_home, spread_away, spread_price_home, "
            "spread_price_away) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (gid, sport, home, away, commence_time, book, observed_at,
             spread_home, -spread_home if spread_home is not None else None,
             price_home, price_away),
        )
    conn.commit()
    return gid


RAW_FACTS = {
    "home_wins_last_3": 2, "away_wins_last_3": 1,
    "home_point_margin_avg_last_3": 4.0, "away_point_margin_avg_last_3": -2.0,
    "home_current_win_streak": 2, "away_current_win_streak": 0,
    "home_days_since_last_game": 7, "away_days_since_last_game": 7,
    "competing_games_same_window_same_sport": 2,
    "is_standalone_same_sport": 0,
    "tv_network": "CBS",
    "ranking_home": None, "ranking_away": None,
}


class TestEnsureSchemaAppendOnly(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        ensure_schema(self.conn)

    def test_tables_created(self):
        tables = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("synthetic_bettor_identity", tables)
        self.assertIn("synthetic_bettor_profile", tables)
        self.assertIn("synthetic_wager", tables)

    def test_identity_is_append_only(self):
        self.conn.execute(
            "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
            "archetype_distribution_version, created_at) "
            "VALUES ('nfl', 0, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', "
            "'2026-01-01T00:00:00Z')"
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE synthetic_bettor_identity SET archetype='MARQUEE_GAME' WHERE bettor_index=0"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM synthetic_bettor_identity WHERE bettor_index=0")

    def test_profile_is_append_only(self):
        self.conn.execute(
            "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
            "archetype_distribution_version, created_at) "
            "VALUES ('nfl', 0, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', "
            "'2026-01-01T00:00:00Z')"
        )
        bettor_id = self.conn.execute(
            "SELECT bettor_id FROM synthetic_bettor_identity WHERE bettor_index=0"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO synthetic_bettor_profile (bettor_id, population_version, profile_version, "
            "favorite_bias, underdog_contrarian_bias, home_team_bias, "
            "recency_sensitivity, rest_sensitivity, standalone_primetime_bias, ranked_team_attraction, "
            "large_spread_tolerance, stake_tier_shift, bet_frequency_shift, created_at) VALUES "
            "(?, 'nfl_2026_v1', 'v1', 0.5,0.5,0.5,0.5,0.5,0.5,0.0,0.0,0.0,0.0,'2026-01-01T00:00:00Z')",
            (bettor_id,),
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "UPDATE synthetic_bettor_profile SET favorite_bias=0.9 WHERE bettor_id=?", (bettor_id,)
            )


class TestSyntheticWagerSchemaIsTotalsCompatible(unittest.TestCase):
    """
    R3 regression test (correction #2): synthetic_wager's side/market
    CHECK must accept both spread sides (home/away) and future totals
    sides (over/under), and reject a mismatched market/side pair, so
    Phase 2 totals does not require a table redesign.
    """

    def setUp(self):
        self.conn = fresh_db()
        ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
            "archetype_distribution_version, created_at) "
            "VALUES ('nfl', 0, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', "
            "'2026-01-01T00:00:00Z')"
        )
        self.bettor_id = self.conn.execute(
            "SELECT bettor_id FROM synthetic_bettor_identity WHERE bettor_index=0"
        ).fetchone()[0]
        self.conn.commit()

    def _insert_wager(self, market, side):
        # wager_ordinal (Phase 2A): part of this wager's deterministic
        # identity (bettor_id, simulation_run_id, wager_ordinal) -- see
        # simulation/population.py's WAGER_IDENTITY_INDEX_SQL. A fixed 0
        # is fine here since each test inserts at most one wager for
        # this bettor_id/simulation_run_id pair.
        self.conn.execute(
            "INSERT INTO synthetic_wager (bettor_id, population_version, canonical_game_id, sport, "
            "market, side, stake, accepted_line, reference_books_used, as_of_decision, accepted_at, "
            "simulation_run_id, wager_ordinal) VALUES (?, 'nfl_2026_v1', '1', 'nfl', ?, ?, 100.0, -3.0, '[]', "
            "'2026-09-09T12:00:00Z', '2026-09-09T12:00:00Z', 'run1', 0)",
            (self.bettor_id, market, side),
        )
        self.conn.commit()

    def test_spread_home_is_valid(self):
        self._insert_wager("spread", "home")  # must not raise

    def test_spread_away_is_valid(self):
        self._insert_wager("spread", "away")  # must not raise

    def test_total_over_is_valid(self):
        self._insert_wager("total", "over")  # must not raise

    def test_total_under_is_valid(self):
        self._insert_wager("total", "under")  # must not raise

    def test_spread_over_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_wager("spread", "over")

    def test_spread_under_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_wager("spread", "under")

    def test_total_home_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_wager("total", "home")

    def test_total_away_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_wager("total", "away")

    def test_unknown_market_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_wager("moneyline", "home")

    def test_unknown_side_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_wager("spread", "push")


class TestArchetypeIdentityImmutableAcrossDistributionChange(unittest.TestCase):
    """
    R3 regression test (correction #1): once a bettor's identity row
    exists, its archetype must survive both a new population_version
    AND activation of a materially different archetype distribution
    version. Exercises the real ensure_population()/DB lifecycle, not
    just the pure functions in bettor_profile.py.
    """

    def setUp(self):
        self.conn = fresh_db()

    def test_identity_and_archetype_survive_a_distribution_change(self):
        from simulation.archetype_config import (
            ACTIVE_ARCHETYPE_DISTRIBUTION_VERSION,
            ARCHETYPE_DISTRIBUTION_V2_TEST_ONLY_NOT_ACTIVE,
        )

        # 1. Create a population under the (default-active) V1 distribution.
        ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=300,
                           archetype_distribution_version=ACTIVE_ARCHETYPE_DISTRIBUTION_VERSION)
        original = {
            row[0]: row[1] for row in self.conn.execute(
                "SELECT bettor_index, archetype FROM synthetic_bettor_identity WHERE sport='nfl'"
            ).fetchall()
        }
        original_bettor_ids = {
            row[0]: row[1] for row in self.conn.execute(
                "SELECT bettor_index, bettor_id FROM synthetic_bettor_identity WHERE sport='nfl'"
            ).fetchall()
        }
        self.assertEqual(len(original), 300)

        # 2. A materially different V2 distribution exists/could be made
        # active -- introduce a new population_version and request it,
        # for the SAME bettor_index range (some are "existing", none
        # are new, since bettor_count is unchanged).
        self.assertNotEqual(
            ARCHETYPE_DISTRIBUTION_V2_TEST_ONLY_NOT_ACTIVE, original,
        )
        created = ensure_population(
            self.conn, "nfl_2026_v2", "nfl", bettor_count=300,
            archetype_distribution_version="archetype_distribution_v2_test_only_not_active",
        )

        # 3. No NEW identity rows were created -- every bettor_index
        # already had an identity row, so the "only used for brand-new
        # identities" rule means zero archetype draws happened under V2.
        self.assertEqual(created, 0)

        # 4. Every existing bettor_id retains EXACTLY the original
        # archetype, and no identity row was modified (same bettor_id,
        # same archetype, same row count).
        after = {
            row[0]: row[1] for row in self.conn.execute(
                "SELECT bettor_index, archetype FROM synthetic_bettor_identity WHERE sport='nfl'"
            ).fetchall()
        }
        after_bettor_ids = {
            row[0]: row[1] for row in self.conn.execute(
                "SELECT bettor_index, bettor_id FROM synthetic_bettor_identity WHERE sport='nfl'"
            ).fetchall()
        }
        self.assertEqual(original, after, "an existing bettor's archetype changed after a distribution change")
        self.assertEqual(original_bettor_ids, after_bettor_ids, "an identity row was modified/replaced")

        row_count = self.conn.execute(
            "SELECT COUNT(*) FROM synthetic_bettor_identity WHERE sport='nfl'"
        ).fetchone()[0]
        self.assertEqual(row_count, 300, "identity table grew -- a row was duplicated or re-inserted")

        # 5. New profile traits (for nfl_2026_v2) were generated using
        # each bettor's STORED (original) archetype -- spot-check one.
        v2_profiles = get_profiles(self.conn, "nfl_2026_v2", "nfl")
        v2_by_index = {p["bettor_index"]: p for p in v2_profiles}
        for idx, stored_archetype in original.items():
            self.assertEqual(v2_by_index[idx]["archetype"], stored_archetype)

    def test_truly_new_bettors_added_after_a_distribution_change_use_the_new_distribution(self):
        """Only brand-new identities are affected by a distribution change."""
        ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=100)
        created_round_2 = ensure_population(
            self.conn, "nfl_2026_v1", "nfl", bettor_count=150,
            archetype_distribution_version="archetype_distribution_v2_test_only_not_active",
        )
        self.assertEqual(created_round_2, 50)  # bettor_index 100..149 are brand new

        rows = dict(self.conn.execute(
            "SELECT bettor_index, archetype_distribution_version FROM synthetic_bettor_identity WHERE sport='nfl'"
        ).fetchall())
        for idx in range(100):
            self.assertEqual(rows[idx], "archetype_distribution_v1_unvalidated_prior")
        for idx in range(100, 150):
            self.assertEqual(rows[idx], "archetype_distribution_v2_test_only_not_active")


class TestEnsurePopulationIdempotent(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def test_creates_requested_count(self):
        created = ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=50)
        self.assertEqual(created, 50)
        profiles = get_profiles(self.conn, "nfl_2026_v1", "nfl")
        self.assertEqual(len(profiles), 50)

    def test_second_call_creates_nothing_new(self):
        ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=50)
        created_again = ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=50)
        self.assertEqual(created_again, 0)
        profiles = get_profiles(self.conn, "nfl_2026_v1", "nfl")
        self.assertEqual(len(profiles), 50)

    def test_identity_stable_across_calls(self):
        ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=20)
        before = get_profiles(self.conn, "nfl_2026_v1", "nfl")
        ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=20)
        after = get_profiles(self.conn, "nfl_2026_v1", "nfl")
        self.assertEqual(before, after)

    def test_nfl_and_cfb_are_independent_populations(self):
        ensure_population(self.conn, "nfl_2026_v1", "nfl", bettor_count=10)
        ensure_population(self.conn, "cfb_2026_v1", "cfb", bettor_count=10)
        nfl_profiles = get_profiles(self.conn, "nfl_2026_v1", "nfl")
        cfb_profiles = get_profiles(self.conn, "cfb_2026_v1", "cfb")
        self.assertEqual(len(nfl_profiles), 10)
        self.assertEqual(len(cfb_profiles), 10)
        nfl_ids = {p["bettor_id"] for p in nfl_profiles}
        cfb_ids = {p["bettor_id"] for p in cfb_profiles}
        self.assertEqual(nfl_ids & cfb_ids, set())


class TestRunSimulation(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.as_of = "2026-09-10T12:00:00Z"
        self.gid = seed_game(
            self.conn, "nfl", "nfl_kc", "nfl_lac", "2026-09-10T20:00:00Z",
            [
                ("draftkings", "2026-09-09T12:00:00Z", -3.0, -110, -110),
                ("fanduel", "2026-09-09T12:00:00Z", -3.5, -108, -112),
                ("betmgm", "2026-09-09T12:00:00Z", -3.0, -112, -108),
            ],
        )

    def test_produces_a_ledger_for_the_game(self):
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        result = run_simulation(self.conn, "nfl_2026_v1", "nfl", games, self.as_of, bettor_count=500)
        self.assertIn(self.gid, result["games"])
        ledger = result["games"][self.gid]
        self.assertEqual(ledger["favorite_side"], "home")  # -3.0 median -> home favored
        self.assertGreaterEqual(ledger["total_tickets"], 0)
        self.assertEqual(result["population_size"], 500)

    def test_deterministic_across_repeated_calls(self):
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        r1 = run_simulation(self.conn, "nfl_2026_v1", "nfl", games, self.as_of, bettor_count=300)
        r2 = run_simulation(self.conn, "nfl_2026_v1", "nfl", games, self.as_of, bettor_count=300)
        self.assertEqual(r1["games"][self.gid], r2["games"][self.gid])
        self.assertEqual(r1["total_wagers"], r2["total_wagers"])
        self.assertEqual(r1["total_handle"], r2["total_handle"])

    def test_no_crash_with_no_market_data(self):
        """A game with zero book observations yields a null offered line
        and therefore zero wagers for that game -- not a crash and not an
        invented line."""
        gid2 = seed_game(self.conn, "nfl", "nfl_buf", "nfl_mia", "2026-09-10T20:00:00Z", [])
        games = [dict(RAW_FACTS, canonical_game_id=gid2)]
        result = run_simulation(self.conn, "nfl_2026_v1", "nfl", games, self.as_of, bettor_count=200)
        self.assertEqual(result["games"][gid2]["total_tickets"], 0)
        self.assertIsNone(result["games"][gid2]["favorite_side"])

    def test_refreshing_does_not_create_monte_carlo_movement(self):
        """Explicit requirement: re-running with identical inputs must not
        move numbers around, i.e. no unseeded randomness anywhere in the path."""
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        results = [
            run_simulation(self.conn, "nfl_2026_v1", "nfl", games, self.as_of, bettor_count=250)
            for _ in range(5)
        ]
        first = results[0]
        for r in results[1:]:
            self.assertEqual(r, first)


if __name__ == "__main__":
    unittest.main()
