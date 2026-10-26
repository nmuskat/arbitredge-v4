"""
tests/test_phase2a_ledger_and_accounting.py

ArbitrEdge V3 Phase 2A. Standalone-runnable: python tests/test_phase2a_ledger_and_accounting.py

Covers: American-odds accounting math, deterministic/order-independent
representative price selection, run-identity separation of as_of from
simulation_run_key, individual-wager accepted_line sign correctness,
hand-verified monetary ledger P&L, push accounting as a real third
state, idempotent append-only wager persistence (including the
fail-loud-on-inconsistent-duplicate path), write_wagers=False as the
safe default, and CFB V3 shadow wiring through the real
run_slate_book_need.py orchestration.
"""

import sqlite3
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# TEST-ONLY SCAFFOLDING, not a production fix: see tests/test_shadow_boundary.py's
# identical stub for the full explanation -- this snapshot's db/ package
# is genuinely absent (pre-existing, unrelated to Phase 2A), and
# book_need.run_slate_book_need transitively imports db.migrate. Only
# TestCfbShadowWiring needs this; stubbed unconditionally here so the
# whole file stays standalone-runnable.
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

from simulation.accounting import (
    implied_probability, profit_and_gross_return, InvalidPriceError,
)
from simulation.offered_line import construct_offered_line, select_representative_price
from simulation.ledger import build_game_ledger, build_total_ledger
from simulation.run_identity import (
    build_simulation_run_key, compute_simulation_run_id, wager_rng_seed,
    total_wager_rng_seed, default_population_version,
)
from simulation.population import run_simulation, ensure_schema, _persist_wagers


def seed_game(conn, sport, home, away, commence_time, book_rows):
    cur = conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES (?, ?, ?, ?)",
        (sport, home, away, commence_time),
    )
    gid = cur.lastrowid
    for book, observed_at, spread_home, price_home, price_away in book_rows:
        conn.execute(
            "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, "
            "commence_time, book, observed_at, spread_home, spread_away, spread_price_home, "
            "spread_price_away) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (gid, sport, home, away, commence_time, book, observed_at,
             spread_home, -spread_home, price_home, price_away),
        )
    conn.commit()
    return gid


def fresh_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE games_canonical (
            canonical_game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
            home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL, commence_time TEXT NOT NULL
        );
        CREATE TABLE market_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_game_id INTEGER NOT NULL, sport TEXT NOT NULL,
            home_team TEXT NOT NULL, away_team TEXT NOT NULL, commence_time TEXT NOT NULL,
            book TEXT NOT NULL, observed_at TEXT NOT NULL, book_last_update TEXT,
            spread_home REAL, spread_away REAL, spread_price_home REAL, spread_price_away REAL
        );
        """
    )
    conn.commit()
    return conn


RAW_FACTS = {"home_wins_last_3": 2, "away_wins_last_3": 1, "home_current_win_streak": 2}


# ---------------------------------------------------------------------
# A. American odds accounting
# ---------------------------------------------------------------------

class TestAccountingMath(unittest.TestCase):
    def test_even_money_both_directions(self):
        profit, gross = profit_and_gross_return(100.0, 100)
        self.assertAlmostEqual(profit, 100.0)
        self.assertAlmostEqual(gross, 200.0)
        profit, gross = profit_and_gross_return(100.0, -100)
        self.assertAlmostEqual(profit, 100.0)
        self.assertAlmostEqual(gross, 200.0)

    def test_positive_price_formula(self):
        profit, gross = profit_and_gross_return(50.0, 150)
        self.assertAlmostEqual(profit, 75.0)
        self.assertAlmostEqual(gross, 125.0)

    def test_negative_price_formula(self):
        profit, gross = profit_and_gross_return(110.0, -110)
        self.assertAlmostEqual(profit, 100.0)
        self.assertAlmostEqual(gross, 210.0)

    def test_zero_price_rejected(self):
        with self.assertRaises(InvalidPriceError):
            profit_and_gross_return(100.0, 0)
        with self.assertRaises(InvalidPriceError):
            implied_probability(0)

    def test_none_price_rejected(self):
        with self.assertRaises(InvalidPriceError):
            profit_and_gross_return(100.0, None)

    def test_implied_probability_bounds(self):
        self.assertAlmostEqual(implied_probability(100), 0.5)
        self.assertAlmostEqual(implied_probability(-100), 0.5)
        self.assertGreater(implied_probability(-200), implied_probability(100))


# ---------------------------------------------------------------------
# B. Deterministic, order-independent representative price selection
# ---------------------------------------------------------------------

class TestRepresentativePriceSelection(unittest.TestCase):
    def test_order_independence(self):
        matching = [
            ("BookA", {"home_price": -115, "away_price": -105}),
            ("BookB", {"home_price": -110, "away_price": -110}),
        ]
        reversed_matching = list(reversed(matching))
        self.assertEqual(
            select_representative_price(matching, "home_price"),
            select_representative_price(reversed_matching, "home_price"),
        )

    def test_never_fabricates_a_price(self):
        """The result must always be a value one of the input books
        actually quoted -- never a synthesized number (e.g. a default
        -110) and never a naive numeric average across the +/- boundary."""
        matching = [
            ("BookA", {"home_price": -120}),
            ("BookB", {"home_price": 110}),
        ]
        quoted = {-120, 110}
        result = select_representative_price(matching, "home_price")
        self.assertIn(result, quoted)
        self.assertNotEqual(result, -5)  # the naive numeric "median" of -120 and 110

    def test_skips_books_with_no_price_for_this_side(self):
        matching = [
            ("BookA", {"home_price": None}),
            ("BookB", {"home_price": -110}),
        ]
        self.assertEqual(select_representative_price(matching, "home_price"), -110)

    def test_returns_none_when_no_book_has_a_legitimate_price(self):
        matching = [
            ("BookA", {"home_price": None}),
            ("BookB", {"home_price": 0}),  # illegitimate quote, not usable
        ]
        self.assertIsNone(select_representative_price(matching, "home_price"))

    def test_construct_offered_line_leaves_price_none_when_unavailable(self):
        conn = fresh_conn()
        gid = seed_game(
            conn, "nfl", "kc", "lac", "2026-09-10T20:00:00Z",
            [("draftkings", "2026-09-09T12:00:00Z", -3.0, None, -110)],
        )
        line = construct_offered_line(conn, gid, "2026-09-10T00:00:00Z")
        self.assertIsNone(line["home_price"])
        self.assertEqual(line["away_price"], -110)


# ---------------------------------------------------------------------
# C. run_identity: as_of vs simulation_run_key separation, determinism
# ---------------------------------------------------------------------

class TestRunIdentity(unittest.TestCase):
    def test_run_key_is_stable_across_as_of_changes(self):
        key1 = build_simulation_run_key("nfl", "2026-10-11", "nfl_2026_v1")
        key2 = build_simulation_run_key("nfl", "2026-10-11", "nfl_2026_v1")
        self.assertEqual(key1, key2)

    def test_run_id_is_deterministic_pure_function(self):
        id1 = compute_simulation_run_id("nfl_2026_v1", "nfl", "nfl|2026-10-11|nfl_2026_v1", 1000)
        id2 = compute_simulation_run_id("nfl_2026_v1", "nfl", "nfl|2026-10-11|nfl_2026_v1", 1000)
        self.assertEqual(id1, id2)

    def test_run_id_is_stable_across_as_of_blocker_1(self):
        """
        R2 BLOCKER 1 regression: as_of is NOT an input to
        simulation_run_id at all (R1's mistake was including it). Same
        simulation_run_key + same population + same bettor_count must
        produce the SAME simulation_run_id regardless of as_of -- a
        later dashboard refresh (later as_of) inspecting the same
        logical run must not mint a new run identity, since
        synthetic_wager's idempotency is scoped to simulation_run_id.
        """
        id1 = compute_simulation_run_id("nfl_2026_v1", "nfl", "k", 1000)
        id2 = compute_simulation_run_id("nfl_2026_v1", "nfl", "k", 1000)
        self.assertEqual(id1, id2)
        # compute_simulation_run_id doesn't even accept an as_of
        # parameter any more -- there is no way to pass one in that
        # would change the result, by construction.
        import inspect
        params = list(inspect.signature(compute_simulation_run_id).parameters)
        self.assertNotIn("as_of", params)

    def test_run_id_changes_with_simulation_run_key_or_population(self):
        base = compute_simulation_run_id("nfl_2026_v1", "nfl", "k1", 1000)
        diff_key = compute_simulation_run_id("nfl_2026_v1", "nfl", "k2", 1000)
        diff_pop = compute_simulation_run_id("nfl_2026_v2", "nfl", "k1", 1000)
        diff_count = compute_simulation_run_id("nfl_2026_v1", "nfl", "k1", 500)
        self.assertNotEqual(base, diff_key)
        self.assertNotEqual(base, diff_pop)
        self.assertNotEqual(base, diff_count)

    def test_wager_rng_seed_depends_on_run_key_not_as_of(self):
        """The whole point of separating as_of from simulation_run_key:
        the RNG seed (and therefore every bettor's decisions) must be
        identical for the SAME run_key regardless of as_of."""
        seed_a = wager_rng_seed("stable_key", "pop_v1", 42)
        seed_b = wager_rng_seed("stable_key", "pop_v1", 42)
        self.assertEqual(seed_a, seed_b)

    def test_wager_rng_seed_changes_with_run_key(self):
        seed_a = wager_rng_seed("key_one", "pop_v1", 42)
        seed_b = wager_rng_seed("key_two", "pop_v1", 42)
        self.assertNotEqual(seed_a, seed_b)

    def test_total_wager_rng_seed_is_deterministic(self):
        seed_a = total_wager_rng_seed("stable_key", "pop_v1", 42)
        seed_b = total_wager_rng_seed("stable_key", "pop_v1", 42)
        self.assertEqual(seed_a, seed_b)

    def test_total_rng_namespace_is_separate_from_spread(self):
        spread_seed = wager_rng_seed("stable_key", "pop_v1", 42)
        total_seed = total_wager_rng_seed("stable_key", "pop_v1", 42)
        self.assertNotEqual(spread_seed, total_seed)

    def test_population_version_single_source_of_truth(self):
        self.assertEqual(default_population_version("nfl"), "nfl_2026_v1")
        self.assertEqual(default_population_version("cfb"), "cfb_2026_v1")


# ---------------------------------------------------------------------
# D/E/F. End-to-end run_simulation: accepted_line sign, hand-verified
# P&L, push_possible
# ---------------------------------------------------------------------

class TestEndToEndLedgerAccuracy(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        self.as_of = "2026-09-10T12:00:00Z"
        self.gid = seed_game(
            self.conn, "nfl", "kc", "lac", "2026-09-10T20:00:00Z",
            [
                ("draftkings", "2026-09-09T12:00:00Z", -3.0, -110, -110),
                ("fanduel", "2026-09-09T12:00:00Z", -3.0, -110, -110),
            ],
        )

    def test_accepted_line_sign_per_side(self):
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        key = build_simulation_run_key("nfl", "2026-09-10", "nfl_2026_v1")
        result = run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, self.as_of,
            bettor_count=400, simulation_run_key=key,
        )
        home_wagers = [w for w in result["wagers"] if w["side"] == "home"]
        away_wagers = [w for w in result["wagers"] if w["side"] == "away"]
        self.assertTrue(home_wagers, "expected at least one home wager in this fixture")
        self.assertTrue(away_wagers, "expected at least one away wager in this fixture")
        for w in home_wagers:
            self.assertEqual(w["accepted_line"], -3.0)
        for w in away_wagers:
            self.assertEqual(w["accepted_line"], 3.0)  # mirror of home_spread, never reused unchanged

    def test_hand_verified_pnl_against_crafted_wagers(self):
        """Bypass the stochastic engine entirely: hand-build a tiny
        wager set with known stakes/prices and verify build_game_ledger
        produces the exact expected dollar figures."""
        wagers = [
            {"side": "home", "stake": 100.0, "accepted_price": -110},
            {"side": "home", "stake": 50.0, "accepted_price": -110},
            {"side": "away", "stake": 200.0, "accepted_price": -110},
        ]
        ledger = build_game_ledger("G1", wagers, home_spread=-3.0)

        self.assertEqual(ledger["home_handle"], 150.0)
        self.assertEqual(ledger["away_handle"], 200.0)
        self.assertEqual(ledger["total_handle"], 350.0)

        # home covers: both home wagers win (-110 -> profit = stake*100/110),
        # away wager loses (book keeps its $200 stake).
        home_profit_total = 100.0 * 100.0 / 110.0 + 50.0 * 100.0 / 110.0
        expected_pnl_home_covers = round(-home_profit_total + 200.0, 2)
        self.assertEqual(ledger["pnl_if_home_covers"], expected_pnl_home_covers)

        # away covers: away wager wins, both home wagers lose (book keeps $150).
        away_profit = 200.0 * 100.0 / 110.0
        expected_pnl_away_covers = round(-away_profit + 150.0, 2)
        self.assertEqual(ledger["pnl_if_away_covers"], expected_pnl_away_covers)

        self.assertEqual(ledger["pnl_if_push"], 0.0)
        self.assertTrue(ledger["push_possible"])  # -3.0 is a whole number
        self.assertEqual(
            ledger["pnl_swing"],
            round(abs(expected_pnl_home_covers - expected_pnl_away_covers), 2),
        )

        gross_return_home_covers = round(
            (100.0 + 100.0 * 100.0 / 110.0) + (50.0 + 50.0 * 100.0 / 110.0), 2
        )
        self.assertEqual(ledger["gross_return_obligation_if_home_covers"], gross_return_home_covers)

    def test_push_not_possible_on_half_point_spread(self):
        ledger = build_game_ledger(
            "G2",
            [{"side": "home", "stake": 10.0, "accepted_price": -110}],
            home_spread=-3.5,
        )
        self.assertFalse(ledger["push_possible"])
        self.assertEqual(ledger["pnl_if_push"], 0.0)  # still trivially reported, but not a live outcome
        # R2 BLOCKER 3: a half-point line can never push -- there is no
        # live payout obligation to report, so this must NOT advertise
        # total_handle (or 0.0, which would misleadingly read as "zero
        # obligation" rather than "not a real state").
        self.assertIsNone(ledger["gross_return_obligation_if_push"])

    def test_push_gross_return_obligation_equals_total_handle_when_possible(self):
        """
        R2 BLOCKER 3: under a TRUE push (whole-number line), the book
        returns every accepted stake in full -- its gross payout
        obligation equals total_handle, even though net P&L is 0.0
        (money moves out and back, it just nets to nothing).
        """
        wagers = [
            {"side": "home", "stake": 100.0, "accepted_price": -110},
            {"side": "away", "stake": 60.0, "accepted_price": 120},
        ]
        ledger = build_game_ledger("G2b", wagers, home_spread=-3.0)
        self.assertTrue(ledger["push_possible"])
        self.assertEqual(ledger["gross_return_obligation_if_push"], ledger["total_handle"])
        self.assertEqual(ledger["gross_return_obligation_if_push"], 160.0)

    def test_need_side_and_tie_state(self):
        # Deliberately balanced book (both sides win-equivalent amounts
        # under their respective cover outcome) -> exact monetary tie.
        wagers = [
            {"side": "home", "stake": 110.0, "accepted_price": -110},
            {"side": "away", "stake": 110.0, "accepted_price": -110},
        ]
        ledger = build_game_ledger("G3", wagers, home_spread=-3.0)
        self.assertTrue(ledger["ledger_implied_need_tie"])
        self.assertIsNone(ledger["ledger_implied_need_side"])
        self.assertEqual(ledger["pnl_if_home_covers"], ledger["pnl_if_away_covers"])

        unbalanced = [
            {"side": "home", "stake": 500.0, "accepted_price": -110},
            {"side": "away", "stake": 50.0, "accepted_price": -110},
        ]
        ledger2 = build_game_ledger("G4", unbalanced, home_spread=-3.0)
        self.assertFalse(ledger2["ledger_implied_need_tie"])
        # Book is heavily exposed to a home win (far more home stake) ->
        # book prefers (needs) AWAY to cover.
        self.assertEqual(ledger2["ledger_implied_need_side"], "away")

    def test_never_reuses_legacy_liability_score_shape(self):
        ledger = build_game_ledger(
            "G5", [{"side": "home", "stake": 10.0, "accepted_price": -110}], home_spread=-3.0
        )
        for legacy_key in ("liability_score", "liability_strength", "decision", "exposure_score"):
            self.assertNotIn(legacy_key, ledger)


# ---------------------------------------------------------------------
# G/H. Idempotent, append-only wager persistence; write_wagers default
# ---------------------------------------------------------------------

class TestTotalLedgerAccuracy(unittest.TestCase):
    def test_hand_verified_total_pnl(self):
        wagers = [
            {"side": "over", "stake": 100.0, "accepted_price": -110},
            {"side": "over", "stake": 50.0, "accepted_price": -110},
            {"side": "under", "stake": 200.0, "accepted_price": -110},
        ]
        ledger = build_total_ledger("T1", wagers, total_points=47.0)

        self.assertEqual(ledger["over_handle"], 150.0)
        self.assertEqual(ledger["under_handle"], 200.0)
        self.assertEqual(ledger["total_handle"], 350.0)
        self.assertEqual(ledger["over_tickets"], 2)
        self.assertEqual(ledger["under_tickets"], 1)
        self.assertEqual(ledger["over_ticket_pct"], 66.7)
        self.assertEqual(ledger["under_ticket_pct"], 33.3)
        self.assertEqual(ledger["over_money_pct"], 42.9)
        self.assertEqual(ledger["under_money_pct"], 57.1)

        over_profit = 100.0 * 100.0 / 110.0 + 50.0 * 100.0 / 110.0
        expected_over_pnl = round(-over_profit + 200.0, 2)
        self.assertEqual(ledger["pnl_if_over"], expected_over_pnl)

        under_profit = 200.0 * 100.0 / 110.0
        expected_under_pnl = round(-under_profit + 150.0, 2)
        self.assertEqual(ledger["pnl_if_under"], expected_under_pnl)

        self.assertTrue(ledger["push_possible"])
        self.assertEqual(ledger["pnl_if_push"], 0.0)
        self.assertEqual(ledger["gross_return_obligation_if_push"], 350.0)
        self.assertEqual(
            ledger["pnl_swing"],
            round(abs(expected_over_pnl - expected_under_pnl), 2),
        )

        self.assertEqual(ledger["ledger_implied_need_side"], "over")
        self.assertFalse(ledger["ledger_implied_need_tie"])
        self.assertEqual(ledger["ledger_implied_need_magnitude"], ledger["pnl_swing"])


class TestTotalLedgerHalfPoint(unittest.TestCase):
    def test_half_point_total_cannot_push(self):
        wagers = [
            {"side": "over", "stake": 100.0, "accepted_price": -110},
            {"side": "under", "stake": 100.0, "accepted_price": -110},
        ]
        ledger = build_total_ledger("T2", wagers, total_points=47.5)

        self.assertFalse(ledger["push_possible"])
        self.assertEqual(ledger["pnl_if_push"], 0.0)
        self.assertIsNone(ledger["gross_return_obligation_if_push"])


class TestTotalLedgerPriceSafety(unittest.TestCase):
    def test_missing_accepted_price_fails_closed(self):
        wagers = [
            {"side": "over", "stake": 100.0, "accepted_price": None},
        ]

        with self.assertRaises(InvalidPriceError):
            build_total_ledger("T3", wagers, total_points=47.5)


class TestWagerPersistence(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_conn()
        self.as_of = "2026-09-10T12:00:00Z"
        self.gid = seed_game(
            self.conn, "nfl", "kc", "lac", "2026-09-10T20:00:00Z",
            [("draftkings", "2026-09-09T12:00:00Z", -3.0, -110, -110)],
        )
        self.key = build_simulation_run_key("nfl", "2026-09-10", "nfl_2026_v1")

    def test_write_wagers_false_is_the_default_and_mutates_nothing(self):
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        result = run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, self.as_of,
            bettor_count=300, simulation_run_key=self.key,
        )
        self.assertGreater(result["total_wagers"], 0)
        count = self.conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]
        self.assertEqual(count, 0, "write_wagers defaults to False -- no rows should be written")

    def test_replay_is_idempotent_not_duplicating(self):
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        r1 = run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, self.as_of,
            bettor_count=300, simulation_run_key=self.key, write_wagers=True,
        )
        count_after_first = self.conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]
        self.assertEqual(count_after_first, r1["total_wagers"])

        r2 = run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, self.as_of,
            bettor_count=300, simulation_run_key=self.key, write_wagers=True,
        )
        count_after_second = self.conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]
        self.assertEqual(count_after_second, count_after_first, "replay must not duplicate rows")
        self.assertEqual(r1["simulation_run_id"], r2["simulation_run_id"])

    def test_replay_at_a_later_as_of_is_still_idempotent_blocker_1(self):
        """
        R2 BLOCKER 1 consequence: the same logical run (same
        simulation_run_key), persisted once, then re-invoked LATER with
        a DIFFERENT (later) as_of -- same market data still in effect
        in this fixture -- must produce the SAME simulation_run_id and
        must NOT insert a second set of "new" wagers. This is the
        scenario R1 got wrong (as_of was folded into the run id).
        """
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        r1 = run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, self.as_of,
            bettor_count=300, simulation_run_key=self.key, write_wagers=True,
        )
        count_after_first = self.conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]

        later_as_of = "2026-09-10T15:00:00Z"  # still pregame, still before kickoff
        r2 = run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, later_as_of,
            bettor_count=300, simulation_run_key=self.key, write_wagers=True,
        )
        count_after_second = self.conn.execute("SELECT COUNT(*) FROM synthetic_wager").fetchone()[0]

        self.assertEqual(r1["simulation_run_id"], r2["simulation_run_id"])
        self.assertEqual(count_after_second, count_after_first)

    def test_persist_wagers_batch_rolls_back_on_failure(self):
        """
        R2 BLOCKER 2: one batch is one transaction. A valid new row
        earlier in the batch must NOT survive if a later row in the
        SAME batch call hits an inconsistent-identity collision.
        """
        ensure_schema(self.conn)
        for idx in (0, 1):
            self.conn.execute(
                "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
                "archetype_distribution_version, created_at) VALUES "
                f"('nfl', {idx}, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', "
                "'2026-01-01T00:00:00Z')"
            )
        rows = self.conn.execute(
            "SELECT bettor_id, bettor_index FROM synthetic_bettor_identity ORDER BY bettor_index"
        ).fetchall()
        bettor_a, bettor_b = rows[0][0], rows[1][0]
        self.conn.commit()

        # Pre-existing row under bettor_b's identity, from an earlier,
        # separate, already-committed call.
        pre_existing = {
            "bettor_id": bettor_b, "population_version": "nfl_2026_v1",
            "canonical_game_id": str(self.gid), "sport": "nfl", "market": "spread",
            "side": "home", "stake": 50.0, "accepted_line": -3.0, "accepted_price": -110,
            "reference_books_used": "draftkings", "as_of_decision": self.as_of,
            "accepted_at": self.as_of, "simulation_run_id": "run_y", "wager_ordinal": 0,
        }
        _persist_wagers(self.conn, [pre_existing])

        valid_new = {
            "bettor_id": bettor_a, "population_version": "nfl_2026_v1",
            "canonical_game_id": str(self.gid), "sport": "nfl", "market": "spread",
            "side": "away", "stake": 75.0, "accepted_line": 3.0, "accepted_price": -110,
            "reference_books_used": "draftkings", "as_of_decision": self.as_of,
            "accepted_at": self.as_of, "simulation_run_id": "run_y", "wager_ordinal": 0,
        }
        colliding = dict(pre_existing, stake=999.0)  # same identity as pre_existing, different content

        with self.assertRaises(RuntimeError):
            _persist_wagers(self.conn, [valid_new, colliding])

        # The earlier-in-batch valid_new row must NOT have survived --
        # the whole batch rolled back, not just the failing row.
        stored = self.conn.execute(
            "SELECT COUNT(*) FROM synthetic_wager WHERE bettor_id=? AND simulation_run_id='run_y' AND wager_ordinal=0",
            (bettor_a,),
        ).fetchone()[0]
        self.assertEqual(stored, 0, "valid_new from the failed batch must have been rolled back")

        # And the pre-existing row from the EARLIER, separate, already-
        # committed call must be untouched.
        still_there = self.conn.execute(
            "SELECT stake FROM synthetic_wager WHERE bettor_id=? AND simulation_run_id='run_y' AND wager_ordinal=0",
            (bettor_b,),
        ).fetchone()
        self.assertEqual(still_there[0], 50.0)

    def test_inconsistent_duplicate_identity_fails_loudly(self):
        ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
            "archetype_distribution_version, created_at) VALUES "
            "('nfl', 0, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', '2026-01-01T00:00:00Z')"
        )
        bettor_id = self.conn.execute(
            "SELECT bettor_id FROM synthetic_bettor_identity WHERE bettor_index=0"
        ).fetchone()[0]
        self.conn.commit()

        base = {
            "bettor_id": bettor_id, "population_version": "nfl_2026_v1",
            "canonical_game_id": str(self.gid), "sport": "nfl", "market": "spread",
            "side": "home", "stake": 100.0, "accepted_line": -3.0, "accepted_price": -110,
            "reference_books_used": "draftkings", "as_of_decision": self.as_of,
            "accepted_at": self.as_of, "simulation_run_id": "run_x", "wager_ordinal": 0,
        }
        _persist_wagers(self.conn, [base])

        conflicting = dict(base, stake=999.0)  # SAME identity, DIFFERENT content
        with self.assertRaises(RuntimeError):
            _persist_wagers(self.conn, [conflicting])

    def test_persist_wagers_accepts_integer_canonical_game_id(self):
        ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
            "archetype_distribution_version, created_at) VALUES "
            "('nfl', 99, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', '2026-01-01T00:00:00Z')"
        )
        bettor_id = self.conn.execute(
            "SELECT bettor_id FROM synthetic_bettor_identity WHERE bettor_index=99"
        ).fetchone()[0]
        self.conn.commit()

        wager = {
            "bettor_id": bettor_id, "population_version": "nfl_2026_v1",
            "canonical_game_id": self.gid, "sport": "nfl", "market": "spread",
            "side": "home", "stake": 100.0, "accepted_line": -3.0, "accepted_price": -110,
            "reference_books_used": "draftkings", "as_of_decision": self.as_of,
            "accepted_at": self.as_of, "simulation_run_id": "run_integer_gid", "wager_ordinal": 0,
        }

        _persist_wagers(self.conn, [wager])
        _persist_wagers(self.conn, [wager])

        rows = self.conn.execute(
            "SELECT canonical_game_id FROM synthetic_wager "
            "WHERE bettor_id=? AND simulation_run_id='run_integer_gid'",
            (bettor_id,),
        ).fetchall()
        self.assertEqual(rows, [(str(self.gid),)])

    def test_append_only_still_enforced_on_persisted_wagers(self):
        games = [dict(RAW_FACTS, canonical_game_id=self.gid)]
        run_simulation(
            self.conn, "nfl_2026_v1", "nfl", games, self.as_of,
            bettor_count=300, simulation_run_key=self.key, write_wagers=True,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE synthetic_wager SET stake = 1.0 WHERE wager_id = 1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM synthetic_wager WHERE wager_id = 1")

    def test_new_writes_cannot_omit_or_null_the_deterministic_identity(self):
        """
        Migration review: a NEW row with a NULL wager_ordinal must be
        rejected at the database level (trg_synthetic_wager_ordinal_required),
        not merely by convention in population.py's own code.
        """
        ensure_schema(self.conn)
        self.conn.execute(
            "INSERT INTO synthetic_bettor_identity (sport, bettor_index, archetype, "
            "archetype_distribution_version, created_at) VALUES "
            "('nfl', 0, 'CASUAL_BALANCED', 'archetype_distribution_v1_unvalidated_prior', '2026-01-01T00:00:00Z')"
        )
        bettor_id = self.conn.execute(
            "SELECT bettor_id FROM synthetic_bettor_identity WHERE bettor_index=0"
        ).fetchone()[0]
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO synthetic_wager (bettor_id, population_version, canonical_game_id, "
                "sport, market, side, stake, accepted_line, accepted_price, reference_books_used, "
                "as_of_decision, accepted_at, simulation_run_id, wager_ordinal) "
                "VALUES (?, 'nfl_2026_v1', '1', 'nfl', 'spread', 'home', 100.0, -3.0, -110, "
                "'draftkings', ?, ?, 'run_z', NULL)",
                (bettor_id, self.as_of, self.as_of),
            )

    def test_legacy_null_ordinal_rows_are_left_alone_by_migration(self):
        """
        Migration review: a pre-Phase-2A database whose synthetic_wager
        table predates wager_ordinal entirely is migrated additively --
        _migrate_wager_ordinal only adds the column/index/trigger, it
        never inspects or rewrites whatever rows (if any) already exist.
        This simulates that by hand-building the OLD (pre-2A) table
        shape with one existing row, then running ensure_schema on it.
        """
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE synthetic_bettor_identity (
                bettor_id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL,
                bettor_index INTEGER NOT NULL, archetype TEXT NOT NULL,
                archetype_distribution_version TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE (sport, bettor_index)
            );
            CREATE TABLE synthetic_bettor_profile (
                profile_id INTEGER PRIMARY KEY AUTOINCREMENT, bettor_id INTEGER NOT NULL,
                population_version TEXT NOT NULL, profile_version TEXT NOT NULL,
                favorite_bias REAL NOT NULL, underdog_contrarian_bias REAL NOT NULL,
                home_team_bias REAL NOT NULL, recency_sensitivity REAL NOT NULL,
                rest_sensitivity REAL NOT NULL, standalone_primetime_bias REAL NOT NULL,
                ranked_team_attraction REAL NOT NULL, large_spread_tolerance REAL NOT NULL,
                stake_tier_shift REAL NOT NULL, bet_frequency_shift REAL NOT NULL,
                created_at TEXT NOT NULL, UNIQUE (bettor_id, population_version)
            );
            CREATE TABLE synthetic_wager (
                wager_id INTEGER PRIMARY KEY AUTOINCREMENT, bettor_id INTEGER NOT NULL,
                population_version TEXT NOT NULL, canonical_game_id TEXT NOT NULL,
                sport TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
                stake REAL NOT NULL, accepted_line REAL NOT NULL, accepted_price REAL,
                reference_books_used TEXT NOT NULL, as_of_decision TEXT NOT NULL,
                accepted_at TEXT NOT NULL, simulation_run_id TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO synthetic_wager (bettor_id, population_version, canonical_game_id, sport, "
            "market, side, stake, accepted_line, reference_books_used, as_of_decision, accepted_at, "
            "simulation_run_id) VALUES (1, 'nfl_2026_v0_legacy', '1', 'nfl', 'spread', 'home', "
            "100.0, -3.0, 'draftkings', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'legacy_run')"
        )
        conn.commit()

        ensure_schema(conn)  # runs _migrate_wager_ordinal

        legacy_row = conn.execute(
            "SELECT wager_ordinal FROM synthetic_wager WHERE simulation_run_id='legacy_run'"
        ).fetchone()
        self.assertIsNone(legacy_row[0], "pre-existing row must be left exactly as it was")

        # And a NEW insert still can't sneak in with a NULL ordinal.
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO synthetic_wager (bettor_id, population_version, canonical_game_id, "
                "sport, market, side, stake, accepted_line, reference_books_used, as_of_decision, "
                "accepted_at, simulation_run_id, wager_ordinal) VALUES (1, 'nfl_2026_v1', '1', "
                "'nfl', 'spread', 'home', 100.0, -3.0, 'draftkings', '2026-01-01T00:00:00Z', "
                "'2026-01-01T00:00:00Z', 'new_run', NULL)"
            )


# ---------------------------------------------------------------------
# J. CFB V3 shadow wiring through the real orchestration
# ---------------------------------------------------------------------

class TestCfbShadowWiring(unittest.TestCase):
    def test_cfb_shadow_block_source_is_structurally_separate_from_legacy_call(self):
        """
        Static check mirroring the existing NFL shadow-boundary test:
        the CFB branch's only compute_inference call must never receive
        synthetic_bettor_ledger=, and the V3 shadow output must be
        attached under its own clearly-named key.
        """
        source = (Path(__file__).resolve().parents[1] / "book_need" / "run_slate_book_need.py").read_text()
        self.assertIn('inference["synthetic_cfb_slate_v3_shadow"]', source)
        self.assertNotIn("compute_inference(conn, gid, as_of, synthetic_bettor_ledger", source)

    def test_cfb_shadow_runs_through_run_book_need_slate(self):
        import os
        from book_need.synthetic_bettors_v2 import SYNTHETIC_BETTOR_ENGINE_ENV_VAR, V3_ENGINE
        import book_need.run_slate_book_need as rsbn

        conn = fresh_conn()
        conn.execute(
            """
            CREATE TABLE engineered_features_v2 (
                feature_id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_game_id INTEGER, point_in_time_cutoff TEXT,
                competing_games_same_window_same_sport INTEGER, is_standalone_same_sport INTEGER,
                tv_network TEXT, ranking_home INTEGER, ranking_away INTEGER,
                home_wins_last_3 INTEGER, away_wins_last_3 INTEGER,
                home_point_margin_avg_last_3 REAL, away_point_margin_avg_last_3 REAL,
                home_current_win_streak INTEGER, away_current_win_streak INTEGER,
                home_days_since_last_game INTEGER, away_days_since_last_game INTEGER
            )
            """
        )
        conn.commit()
        gid = seed_game(
            conn, "cfb", "cfb_georgia", "cfb_kentucky", "2026-10-11T19:00:00Z",
            [("draftkings", "2026-10-09T12:00:00Z", -17.5, -110, -110)],
        )

        # Minimal stand-ins for the orchestration's other dependencies --
        # this test exercises _shared_pool_game + run_shadow_v3_population
        # wiring directly (the piece Phase 2A adds), not slate discovery
        # or eligibility, which are unrelated and unchanged.
        inference = {"canonical_game_id": gid, "public_pressure": {}}
        pool_game = rsbn._shared_pool_game(conn, inference, "2026-10-10T00:00:00Z")

        os.environ[SYNTHETIC_BETTOR_ENGINE_ENV_VAR] = V3_ENGINE
        try:
            from book_need.synthetic_bettors_v2 import run_shadow_v3_population
            from simulation.run_identity import build_simulation_run_key, default_population_version
            run_key = build_simulation_run_key("cfb", "2026-10-11", default_population_version("cfb"))
            shadow = run_shadow_v3_population(
                [pool_game], conn, "2026-10-10T00:00:00Z", sport="cfb",
                simulation_run_key=run_key, bettor_count=300,
            )
        finally:
            os.environ.pop(SYNTHETIC_BETTOR_ENGINE_ENV_VAR, None)

        self.assertIsNotNone(shadow)
        self.assertEqual(shadow["sport"], "cfb")
        self.assertIn(gid, shadow["games"])


if __name__ == "__main__":
    unittest.main()
