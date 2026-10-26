"""
tests/test_cfb_population_parity.py

Phase 1 ACCEPTANCE GATE (per the approved spec, correction #5): proves
the shared simulation engine produces valid, structurally equivalent
output for CFB using the SAME engine as NFL -- not a separate or
simplified simulator -- with no AP Top-25 filtering or other
sport-specific shortcut baked into the engine itself (CFB-specific
calibration, where it exists, lives in named trait fields like
ranked_team_attraction / large_spread_tolerance, not in a fork of the
selection code).

Standalone-runnable: python tests/test_cfb_population_parity.py
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulation.population import run_simulation, ensure_population, get_profiles
from simulation import bet_selection, population as population_module
import simulation.bettor_profile as bettor_profile_module

TEST_DB_PATH = Path(__file__).parent / "_cfb_parity_test_workspace" / "test_v3_cfb.db"


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


# A CFB-flavored game -- large spread, ranked team, no "standalone primetime"
# signal relevant (that trait is NFL-only) -- but using the exact same raw
# column names build_engineered_features_v2.py already emits for both sports.
CFB_RAW_FACTS = {
    "home_wins_last_3": 3, "away_wins_last_3": 0,
    "home_point_margin_avg_last_3": 18.0, "away_point_margin_avg_last_3": -10.0,
    "home_current_win_streak": 4, "away_current_win_streak": 0,
    "home_days_since_last_game": 7, "away_days_since_last_game": 7,
    "competing_games_same_window_same_sport": 8,
    "is_standalone_same_sport": 0,
    "tv_network": "ABC",
    "ranking_home": 4, "ranking_away": None,
}

NFL_RAW_FACTS = {
    "home_wins_last_3": 2, "away_wins_last_3": 1,
    "home_point_margin_avg_last_3": 4.0, "away_point_margin_avg_last_3": -2.0,
    "home_current_win_streak": 2, "away_current_win_streak": 0,
    "home_days_since_last_game": 7, "away_days_since_last_game": 7,
    "competing_games_same_window_same_sport": 2,
    "is_standalone_same_sport": 0,
    "tv_network": "CBS",
    "ranking_home": None, "ranking_away": None,
}


class TestSharedEngineNoForkedCode(unittest.TestCase):
    """
    Structural proof that NFL and CFB go through the identical selection
    functions -- not merely 'similar' functions. select_game,
    select_spread_side, select_stake, select_wager_count, and
    run_simulation all take `sport` as plain data, never branch into a
    separate code module per sport.
    """

    def test_bet_selection_module_has_no_sport_specific_functions(self):
        import inspect
        names = [n for n, _ in inspect.getmembers(bet_selection, inspect.isfunction)]
        for n in names:
            self.assertNotIn("nfl", n.lower())
            self.assertNotIn("cfb", n.lower())

    def test_run_simulation_is_the_single_entry_point_for_both_sports(self):
        import inspect
        src = inspect.getsource(population_module.run_simulation)
        # One function body, parameterized by `sport`, not an if/else
        # dispatch into two different simulation implementations.
        self.assertNotIn('sport == "nfl"', src)
        self.assertNotIn('sport == "cfb"', src)


class TestCfbParity(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.as_of = "2026-10-10T12:00:00Z"

        self.nfl_gid = seed_game(
            self.conn, "nfl", "nfl_kc", "nfl_lac", "2026-10-11T20:00:00Z",
            [("draftkings", "2026-10-09T12:00:00Z", -3.0, -110, -110),
             ("fanduel", "2026-10-09T12:00:00Z", -3.0, -110, -110)],
        )
        self.cfb_gid = seed_game(
            self.conn, "cfb", "cfb_georgia", "cfb_kentucky", "2026-10-11T19:00:00Z",
            [("draftkings", "2026-10-09T12:00:00Z", -17.5, -110, -110),
             ("fanduel", "2026-10-09T12:00:00Z", -17.0, -108, -112)],
        )

    def test_cfb_population_can_be_created(self):
        created = ensure_population(self.conn, "cfb_2026_v1", "cfb", bettor_count=200)
        self.assertEqual(created, 200)
        profiles = get_profiles(self.conn, "cfb_2026_v1", "cfb")
        self.assertEqual(len(profiles), 200)

    def test_cfb_simulation_produces_valid_spread_wagers(self):
        games = [dict(CFB_RAW_FACTS, canonical_game_id=self.cfb_gid)]
        result = run_simulation(self.conn, "cfb_2026_v1", "cfb", games, self.as_of, bettor_count=1000)
        ledger = result["games"][self.cfb_gid]
        self.assertEqual(ledger["favorite_side"], "home")
        self.assertGreater(ledger["total_tickets"], 0, "CFB engine produced zero wagers")
        self.assertGreater(ledger["total_handle"], 0)

    def test_nfl_and_cfb_both_run_through_run_simulation_successfully(self):
        nfl_games = [dict(NFL_RAW_FACTS, canonical_game_id=self.nfl_gid)]
        cfb_games = [dict(CFB_RAW_FACTS, canonical_game_id=self.cfb_gid)]

        nfl_result = run_simulation(self.conn, "nfl_2026_v1", "nfl", nfl_games, self.as_of, bettor_count=500)
        cfb_result = run_simulation(self.conn, "cfb_2026_v1", "cfb", cfb_games, self.as_of, bettor_count=500)

        # Same output shape for both sports -- same keys, same structure.
        self.assertEqual(set(nfl_result.keys()), set(cfb_result.keys()))
        nfl_ledger_keys = set(next(iter(nfl_result["games"].values())).keys())
        cfb_ledger_keys = set(next(iter(cfb_result["games"].values())).keys())
        self.assertEqual(nfl_ledger_keys, cfb_ledger_keys)

    def test_cfb_specific_traits_are_exercised_not_dead_code(self):
        """
        ranked_team_attraction and large_spread_tolerance are CFB-only
        (forced to 0.0 for NFL). Confirm at least one CFB profile has a
        non-zero value for each, i.e. these traits actually do something
        for CFB rather than being specified but inert.
        """
        profiles = [bettor_profile_module.generate_profile("cfb_2026_v1", "cfb", i) for i in range(300)]
        self.assertTrue(any(p["ranked_team_attraction"] > 0.0 for p in profiles))
        self.assertTrue(any(p["large_spread_tolerance"] > 0.0 for p in profiles))

    def test_no_ap_top25_filtering_inside_the_engine(self):
        """
        Per the approved spec: AP Top-25 is a slate-selection filter that
        belongs OUTSIDE this engine (and is explicitly out of Phase 1
        scope), not a gate inside run_simulation/bet_selection. An
        unranked-vs-unranked CFB game must still produce wagers.
        """
        unranked_facts = dict(CFB_RAW_FACTS, ranking_home=None, ranking_away=None)
        games = [dict(unranked_facts, canonical_game_id=self.cfb_gid)]
        result = run_simulation(self.conn, "cfb_2026_v1", "cfb", games, self.as_of, bettor_count=500)
        self.assertGreater(result["games"][self.cfb_gid]["total_tickets"], 0)


if __name__ == "__main__":
    unittest.main()
