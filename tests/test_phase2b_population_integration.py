import unittest
import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_phase2b_totals import fresh_conn, seed_game
from simulation.population import run_simulation
from simulation.run_identity import build_simulation_run_key

class TestTotalsPopulationIntegration(unittest.TestCase):
    def test_totals_generation(self):
        conn = fresh_conn()
        gid = seed_game(conn, [("draftkings", "2026-09-09T12:00:00Z", 47.5, -115, -105)])
        conn.execute("UPDATE market_snapshots SET spread_home=-3.0, spread_away=3.0, spread_price_home=-110, spread_price_away=-110")
        conn.commit()
        facts = dict(canonical_game_id=gid, home_points_for_avg_last_3=30.0, away_points_for_avg_last_3=24.0, home_points_against_avg_last_3=21.0, away_points_against_avg_last_3=27.0)
        result = run_simulation(conn, "nfl_2026_v1", "nfl", [facts], "2026-09-10T12:00:00Z", bettor_count=300, simulation_run_key=build_simulation_run_key("nfl", "2026-09-10", "nfl_2026_v1"))
        totals = [w for w in result["wagers"] if w["market"] == "total"]
        spreads = [w for w in result["wagers"] if w["market"] == "spread"]
        self.assertGreater(len(totals), 0)
        self.assertGreater(len(spreads), 0)
        self.assertEqual(result["population_size"], 300)
        self.assertTrue(all(w["accepted_line"] == 47.5 for w in totals))
        self.assertTrue(all(w["accepted_price"] == (-115 if w["side"] == "over" else -105) for w in totals))
        self.assertTrue(all(w["wager_ordinal"] >= 1000000 for w in totals))
        self.assertEqual(result["active_bettors"], len({w["bettor_id"] for w in result["wagers"]}))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0], 0)

    def test_totals_do_not_change_spread_wagers(self):
        conn = fresh_conn()
        gid = seed_game(conn, [("draftkings", "2026-09-09T12:00:00Z", 47.5, -115, -105)])
        conn.execute("UPDATE market_snapshots SET spread_home=-3, spread_away=3, spread_price_home=-110, spread_price_away=-110")
        conn.commit()
        facts = dict(canonical_game_id=gid, home_points_for_avg_last_3=30.0, away_points_for_avg_last_3=24.0, home_points_against_avg_last_3=21.0, away_points_against_avg_last_3=27.0)
        key = build_simulation_run_key("nfl", "2026-09-10", "nfl_2026_v1")
        baseline = run_simulation(conn, "nfl_2026_v1", "nfl", [facts], "2026-09-10T12:00:00Z", bettor_count=300, simulation_run_key=key)
        baseline_spreads = [w for w in baseline["wagers"] if w["market"] == "spread"]
        conn.execute("UPDATE market_snapshots SET total_points=NULL, total_price_over=NULL, total_price_under=NULL")
        conn.commit()
        comparison = run_simulation(conn, "nfl_2026_v1", "nfl", [facts], "2026-09-10T12:00:00Z", bettor_count=300, simulation_run_key=key)
        comparison_spreads = [w for w in comparison["wagers"] if w["market"] == "spread"]
        self.assertGreater(len(baseline_spreads), 0)
        self.assertEqual(baseline_spreads, comparison_spreads)
        self.assertGreater(len([w for w in baseline["wagers"] if w["market"] == "total"]), 0)
        self.assertEqual(len([w for w in comparison["wagers"] if w["market"] == "total"]), 0)

    def test_totals_persistence_is_idempotent(self):
        conn = fresh_conn()
        gid = seed_game(conn, [("draftkings", "2026-09-09T12:00:00Z", 47.5, -115, -105)])
        conn.execute("UPDATE market_snapshots SET spread_home=-3, spread_away=3, spread_price_home=-110, spread_price_away=-110")
        conn.commit()
        facts = dict(canonical_game_id=gid, home_points_for_avg_last_3=30.0, away_points_for_avg_last_3=24.0, home_points_against_avg_last_3=21.0, away_points_against_avg_last_3=27.0)
        key = build_simulation_run_key("nfl", "2026-09-10", "nfl_2026_v1")
        first = run_simulation(conn, "nfl_2026_v1", "nfl", [facts], "2026-09-10T12:00:00Z", bettor_count=300, simulation_run_key=key, write_wagers=True)
        first_count = conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]
        second = run_simulation(conn, "nfl_2026_v1", "nfl", [facts], "2026-09-10T12:00:00Z", bettor_count=300, simulation_run_key=key, write_wagers=True)
        second_count = conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]
        self.assertGreater(first_count, 0)
        self.assertEqual(first["wagers"], second["wagers"])
        self.assertEqual(first_count, len(first["wagers"]))
        self.assertEqual(first_count, second_count)
        markets = {row[0] for row in conn.execute("SELECT DISTINCT market FROM synthetic_wager")}
        self.assertEqual(markets, {"spread", "total"})
        identities = [(w["bettor_id"], w["simulation_run_id"], w["wager_ordinal"]) for w in first["wagers"]]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(w["wager_ordinal"] >= 1000000 for w in first["wagers"] if w["market"] == "total"))
