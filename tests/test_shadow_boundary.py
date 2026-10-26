"""
tests/test_shadow_boundary.py

Phase 1 correction: V3's favorite/underdog ledger must stay a SHADOW
output -- never fed into compute_inference's synthetic_bettor_ledger
argument, which drives book_need.synthetic_sportsbook's legacy
public/other/exposure_score contract. Proves:

  1. simulate_shared_nfl_population (the function that actually feeds
     compute_inference in run_slate_book_need.py) has no
     SYNTHETIC_BETTOR_ENGINE branch and no database access at all --
     structurally incapable of returning V3 output.
  2. run_shadow_v3_population is a separate function, returns None when
     the flag is off, and its ledger shape is genuinely incompatible
     with (not silently coercible into) the legacy sportsbook contract
     evaluate_synthetic_book() expects.
  3. run_slate_book_need.py's source never passes a shadow-V3 value as
     compute_inference's synthetic_bettor_ledger argument.

Standalone-runnable: python tests/test_shadow_boundary.py
"""

import inspect
import os
import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# TEST-ONLY SCAFFOLDING, not a production fix: this snapshot's db/
# package is genuinely absent (confirmed pre-existing, unrelated to any
# Phase 1 change -- see the deliverable's regression-suite section).
# book_need.run_slate_book_need transitively imports db.migrate (for a
# path constant it never calls from the functions this test exercises),
# so importing it here fails exactly like every other test file that
# touches book_need.inference. Stubbing a minimal fake 'db.migrate'
# module lets this ONE test file actually execute
# book_need.run_slate_book_need's real, unmodified source instead of
# only inspecting it, which is what correction #3 specifically asked
# for ("an integration test proving the real run_slate shadow
# game-facts construction..."). This stub is never used by, or
# shipped to, production -- it exists only inside this test process.
try:
    import db  # noqa: F401
except ModuleNotFoundError:
    import types
    _fake_db = types.ModuleType("db")
    _fake_migrate = types.ModuleType("db.migrate")
    _fake_migrate.DEFAULT_DB_PATH = ":memory:"
    _fake_migrate.DB_DIR = "."
    _fake_migrate.run_migration = lambda *a, **k: None
    _fake_db.migrate = _fake_migrate
    sys.modules["db"] = _fake_db
    sys.modules["db.migrate"] = _fake_migrate

from book_need.synthetic_bettors_v2 import (
    simulate_shared_nfl_population,
    run_shadow_v3_population,
    SYNTHETIC_BETTOR_ENGINE_ENV_VAR,
    V3_ENGINE,
    LEGACY_ENGINE,
)
from book_need.synthetic_sportsbook import evaluate_synthetic_book


class TestLegacyCallSiteCannotReachV3(unittest.TestCase):
    def test_simulate_shared_nfl_population_has_no_conn_or_engine_param(self):
        sig = inspect.signature(simulate_shared_nfl_population)
        self.assertEqual(list(sig.parameters), ["games", "slate_key", "bettor_count"])

    def test_simulate_shared_nfl_population_ignores_the_flag_entirely(self):
        games = [{"canonical_game_id": 1, "public_side": "home", "side_preference_percentile": 70}]

        os.environ[SYNTHETIC_BETTOR_ENGINE_ENV_VAR] = V3_ENGINE
        try:
            result = simulate_shared_nfl_population(games, "nfl|2026-09-10|t")
        finally:
            os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)

        self.assertEqual(result["version"], "synthetic_bettors_v2_shared_pool")
        self.assertIn("public_tickets", result["games"][1])

    def test_source_never_imports_the_simulation_package(self):
        """simulate_shared_nfl_population's own body never reaches into
        simulation.population -- only run_shadow_v3_population does."""
        source = inspect.getsource(simulate_shared_nfl_population)
        self.assertNotIn("simulation.population", source)
        self.assertNotIn("run_simulation", source)


class TestShadowFunctionDefaultOff(unittest.TestCase):
    def setUp(self):
        os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)
        self.conn = sqlite3.connect(":memory:")

    def test_returns_none_when_flag_unset(self):
        games = [{"canonical_game_id": 1, "home_wins_last_3": 2}]
        result = run_shadow_v3_population(games, self.conn, "2026-09-09T12:00:00Z")
        self.assertIsNone(result)

    def test_returns_none_when_flag_set_to_legacy_explicitly(self):
        os.environ[SYNTHETIC_BETTOR_ENGINE_ENV_VAR] = LEGACY_ENGINE
        try:
            games = [{"canonical_game_id": 1, "home_wins_last_3": 2}]
            result = run_shadow_v3_population(games, self.conn, "2026-09-09T12:00:00Z")
        finally:
            os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)
        self.assertIsNone(result)


class TestShadowLedgerShapeIncompatibleWithLegacyContract(unittest.TestCase):
    """
    The legacy synthetic sportsbook (evaluate_synthetic_book) is written
    around a public_tickets/other_tickets/public_dollars/other_dollars/
    exposure_score ledger. V3's favorite/underdog ledger has none of
    those keys -- it CANNOT silently satisfy that contract; it would
    either KeyError or be read as all-None, not quietly produce a wrong
    number.
    """

    def _build_db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE games_canonical (
                canonical_game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL, home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL,
                commence_time TEXT NOT NULL
            );
            CREATE TABLE market_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_game_id INTEGER NOT NULL,
                sport TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
                commence_time TEXT NOT NULL, book TEXT NOT NULL, observed_at TEXT NOT NULL,
                book_last_update TEXT, spread_home REAL, spread_away REAL,
                spread_price_home REAL, spread_price_away REAL, moneyline_home REAL,
                moneyline_away REAL, total_points REAL, total_price_over REAL,
                total_price_under REAL, parser_version TEXT, raw_response_id INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
            "VALUES ('nfl','kc','lac','2026-09-10T20:00:00Z')"
        )
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, "
            "commence_time, book, observed_at, spread_home, spread_away, spread_price_home, "
            "spread_price_away) VALUES (1,'nfl','KC','LAC','2026-09-10T20:00:00Z','draftkings',"
            "'2026-09-09T12:00:00Z',-3.0,3.0,-110,-110)"
        )
        conn.commit()
        return conn

    def test_v3_ledger_lacks_legacy_keys(self):
        os.environ[SYNTHETIC_BETTOR_ENGINE_ENV_VAR] = V3_ENGINE
        try:
            conn = self._build_db()
            games = [{"canonical_game_id": 1, "home_wins_last_3": 2, "away_wins_last_3": 1}]
            shadow = run_shadow_v3_population(games, conn, "2026-09-09T12:00:00Z", sport="nfl", bettor_count=300)
        finally:
            os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)

        self.assertIsNotNone(shadow)
        ledger = shadow["games"][1]
        # Legacy V2 shape (public/other tickets+dollars, exposure_score)
        # AND Phase 1's own favorite/underdog shape are both absent --
        # Phase 2A's ledger is home/away-oriented real money, not either
        # of those.
        for legacy_key in (
            "public_tickets", "other_tickets", "public_dollars", "other_dollars",
            "exposure_score", "favorite_tickets", "underdog_tickets",
        ):
            self.assertNotIn(legacy_key, ledger)
        self.assertIn("home_tickets", ledger)
        self.assertIn("away_tickets", ledger)
        self.assertIn("pnl_if_home_covers", ledger)
        self.assertIn("pnl_if_away_covers", ledger)

    def test_feeding_v3_ledger_into_legacy_sportsbook_does_not_silently_succeed(self):
        """
        If someone DID (incorrectly) pass the V3 shadow ledger as
        evaluate_synthetic_book's synthetic_ledger argument, it must not
        produce a plausible-looking number -- it should come back with
        exposure_score/handle_share missing (None), which
        evaluate_synthetic_book already treats as insufficient evidence
        and refuses to score, rather than silently misreading
        favorite_tickets as public_tickets.
        """
        os.environ[SYNTHETIC_BETTOR_ENGINE_ENV_VAR] = V3_ENGINE
        try:
            conn = self._build_db()
            games = [{"canonical_game_id": 1, "home_wins_last_3": 2, "away_wins_last_3": 1}]
            shadow = run_shadow_v3_population(games, conn, "2026-09-09T12:00:00Z", sport="nfl", bettor_count=300)
        finally:
            os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)

        v3_ledger = shadow["games"][1]
        public_pressure = {
            "side_preference_direction": "home",
            "handle_intensity_percentile": 60,
            "side_preference_magnitude_percentile": 70,
        }
        segment = {"per_book": {}}

        result = evaluate_synthetic_book(
            public_pressure, segment, usable_book_count=3,
            canonical_game_id=1, synthetic_ledger=v3_ledger,
        )
        # .get("exposure_score") on the V3 dict is None (key absent) ->
        # evaluate_synthetic_book's own "insufficient_exposure_evidence"
        # guard fires, rather than it computing a number from the wrong
        # buckets.
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["reason"], "insufficient_exposure_evidence")
        self.assertIsNone(result["liability_score"])


class TestRealShadowPathReceivesRawRecencyRestFields(unittest.TestCase):
    """
    R3 regression test (correction #3): run_slate_book_need.py's actual
    _latest_v2_context()/_shared_pool_game() construction -- the real
    path that feeds run_shadow_v3_population -- must carry the raw
    recency/rest columns select_spread_side needs, not just
    competing_games/standalone/tv_network/rankings. Before this fix,
    those columns were never queried, so V3's relative-recency and
    relative-rest contributions silently collapsed to neutral for every
    real NFL shadow run. Also confirms the sanitized (post-allowlist)
    facts still exclude every forbidden/unknown field.
    """

    def _build_db_with_engineered_features(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE engineered_features_v2 (
                feature_id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_game_id INTEGER NOT NULL,
                point_in_time_cutoff TEXT NOT NULL,
                competing_games_same_window_same_sport INTEGER,
                is_standalone_same_sport INTEGER,
                tv_network TEXT,
                ranking_home INTEGER,
                ranking_away INTEGER,
                home_wins_last_3 INTEGER,
                away_wins_last_3 INTEGER,
                home_point_margin_avg_last_3 REAL,
                away_point_margin_avg_last_3 REAL,
                home_current_win_streak INTEGER,
                away_current_win_streak INTEGER,
                home_days_since_last_game INTEGER,
                away_days_since_last_game INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO engineered_features_v2 (canonical_game_id, point_in_time_cutoff, "
            "competing_games_same_window_same_sport, is_standalone_same_sport, tv_network, "
            "ranking_home, ranking_away, home_wins_last_3, away_wins_last_3, "
            "home_point_margin_avg_last_3, away_point_margin_avg_last_3, home_current_win_streak, "
            "away_current_win_streak, home_days_since_last_game, away_days_since_last_game) "
            "VALUES (1, '2026-09-09T12:00:00Z', 2, 0, 'CBS', NULL, NULL, 3, 0, 14.0, -14.0, 4, 0, 7, 7)"
        )
        conn.commit()
        return conn

    def test_shared_pool_game_contains_the_raw_recency_rest_columns(self):
        from book_need.run_slate_book_need import _shared_pool_game

        conn = self._build_db_with_engineered_features()
        inference = {
            "canonical_game_id": 1,
            "public_pressure": {
                "side_preference_direction": "home",
                "side_preference_magnitude_percentile": 70,
                "handle_intensity_percentile": 60,
            },
        }
        game = _shared_pool_game(conn, inference, "2026-09-09T12:00:00Z")

        for key, expected in [
            ("home_wins_last_3", 3), ("away_wins_last_3", 0),
            ("home_point_margin_avg_last_3", 14.0), ("away_point_margin_avg_last_3", -14.0),
            ("home_current_win_streak", 4), ("away_current_win_streak", 0),
            ("home_days_since_last_game", 7), ("away_days_since_last_game", 7),
        ]:
            self.assertEqual(game.get(key), expected, f"{key} missing or wrong in _shared_pool_game output")

        # The legacy-only fields are still present too -- this dict still
        # feeds the UNCHANGED legacy call as well.
        self.assertEqual(game["public_side"], "home")

    def test_sanitized_v3_facts_contain_recency_rest_and_exclude_forbidden_fields(self):
        """
        End-to-end: the real _shared_pool_game() output, passed through
        the same allowlist adapter run_shadow_v3_population uses
        (build_raw_game_facts), must retain the recency/rest raw values
        and drop public_side/side_preference_percentile/
        market_activity_percentile and any unknown field.
        """
        from book_need.run_slate_book_need import _shared_pool_game
        from simulation.bet_selection import build_raw_game_facts, FORBIDDEN_GAME_FACT_KEYS

        conn = self._build_db_with_engineered_features()
        inference = {
            "canonical_game_id": 1,
            "public_pressure": {
                "side_preference_direction": "home",
                "side_preference_magnitude_percentile": 70,
                "handle_intensity_percentile": 60,
            },
        }
        game = _shared_pool_game(conn, inference, "2026-09-09T12:00:00Z")
        game["some_future_unapproved_field"] = "x"  # simulate an unknown field slipping in

        sanitized = build_raw_game_facts(game)

        self.assertEqual(sanitized.get("home_wins_last_3"), 3)
        self.assertEqual(sanitized.get("away_wins_last_3"), 0)
        self.assertEqual(sanitized.get("home_current_win_streak"), 4)
        self.assertEqual(sanitized.get("home_days_since_last_game"), 7)

        for forbidden in FORBIDDEN_GAME_FACT_KEYS:
            self.assertNotIn(forbidden, sanitized)
        self.assertNotIn("public_side", sanitized)
        self.assertNotIn("side_preference_percentile", sanitized)
        self.assertNotIn("market_activity_percentile", sanitized)
        self.assertNotIn("some_future_unapproved_field", sanitized)

    def test_shadow_simulation_recency_contribution_is_no_longer_always_neutral(self):
        """
        Full integration: run_shadow_v3_population through the real
        _shared_pool_game-shaped input must be able to produce a
        non-neutral favorite/underdog split driven by recency, now that
        the raw fields actually reach it. (Previously this collapsed to
        the magnitude/bias-only baseline for every game.)
        """
        conn = self._build_db_with_engineered_features()
        conn.executescript(
            """
            CREATE TABLE games_canonical (
                canonical_game_id INTEGER PRIMARY KEY, sport TEXT NOT NULL,
                home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL, commence_time TEXT NOT NULL
            );
            CREATE TABLE market_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_game_id INTEGER NOT NULL,
                sport TEXT NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
                commence_time TEXT NOT NULL, book TEXT NOT NULL, observed_at TEXT NOT NULL,
                book_last_update TEXT, spread_home REAL, spread_away REAL,
                spread_price_home REAL, spread_price_away REAL, moneyline_home REAL,
                moneyline_away REAL, total_points REAL, total_price_over REAL,
                total_price_under REAL, parser_version TEXT, raw_response_id INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO games_canonical (canonical_game_id, sport, home_team_id, away_team_id, commence_time) "
            "VALUES (1, 'nfl', 'kc', 'lac', '2026-09-10T20:00:00Z')"
        )
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
            "book, observed_at, spread_home, spread_away, spread_price_home, spread_price_away) "
            "VALUES (1, 'nfl', 'KC', 'LAC', '2026-09-10T20:00:00Z', 'draftkings', '2026-09-09T12:00:00Z', "
            "-3.0, 3.0, -110, -110)"
        )
        conn.commit()

        from book_need.run_slate_book_need import _shared_pool_game

        inference = {
            "canonical_game_id": 1,
            "public_pressure": {
                "side_preference_direction": "home",
                "side_preference_magnitude_percentile": 70,
                "handle_intensity_percentile": 60,
            },
        }
        pool_game = _shared_pool_game(conn, inference, "2026-09-09T12:00:00Z")

        os.environ[SYNTHETIC_BETTOR_ENGINE_ENV_VAR] = V3_ENGINE
        try:
            shadow = run_shadow_v3_population([pool_game], conn, "2026-09-09T12:00:00Z", sport="nfl", bettor_count=2000)
        finally:
            os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)

        self.assertIsNotNone(shadow)
        ledger = shadow["games"][1]
        self.assertGreater(ledger["total_tickets"], 0)
        # Home (the favorite here, red-hot per the fixture: 4-game win
        # streak, +14 margin, vs. the cold away team) should pick up a
        # meaningfully higher favorite share than magnitude/bias alone
        # would produce at this small spread -- a loose sanity bound,
        # not an exact-value test (archetype mix varies). spread_home
        # is -3.0, so home is the favorite here.
        self.assertEqual(ledger["favorite_side"], "home")
        self.assertGreater(ledger["home_ticket_pct"], 50.0)


class TestRunSlateBookNeedSourceNeverWiresShadowIntoComputeInference(unittest.TestCase):
    def test_compute_inference_call_only_uses_the_legacy_shared_by_gid_ledger(self):
        source = (REPO_ROOT / "book_need" / "run_slate_book_need.py").read_text()

        # The only identifier passed as synthetic_bettor_ledger= must be
        # `ledger` (sourced from shared_by_gid, the legacy call's output).
        self.assertIn("synthetic_bettor_ledger=ledger,", source)
        self.assertNotIn("synthetic_bettor_ledger=day_shadow_v3", source)
        self.assertNotIn("synthetic_bettor_ledger=shadow", source)

    def test_shadow_output_is_attached_under_its_own_clearly_named_key(self):
        source = (REPO_ROOT / "book_need" / "run_slate_book_need.py").read_text()
        self.assertIn("synthetic_nfl_slate_v3_shadow", source)


if __name__ == "__main__":
    unittest.main()
