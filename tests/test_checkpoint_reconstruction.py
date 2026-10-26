"""
Tests for reporting/checkpoint_reconstruction.py

Run with: python tests/test_checkpoint_reconstruction.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reporting.checkpoint_reconstruction import (  # noqa: E402
    reconstruct_checkpoints_for_book, compute_movement, compute_cross_book_disagreement,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_checkpoint_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _snap(observed_at, spread_home=-3.0, spread_price_home=-110, book_last_update=None):
    return {
        "observed_at": observed_at, "book_last_update": book_last_update,
        "spread_home": spread_home, "spread_away": -spread_home if spread_home is not None else None,
        "spread_price_home": spread_price_home, "spread_price_away": -110,
    }


def _insert_snapshot(conn, gid, book, observed_at, spread_home=-3.0, spread_price_home=-110):
    exists = conn.execute("SELECT 1 FROM games_canonical WHERE canonical_game_id=?", (gid,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO games_canonical (canonical_game_id, sport, home_team_id, away_team_id, commence_time) "
            "VALUES (?, 'nfl', 'nfl_kc', 'nfl_lac', '2026-08-08T20:00:00Z')", (gid,),
        )
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home, spread_away, spread_price_home, spread_price_away) "
        "VALUES (?, 'nfl', 'A', 'B', '2026-08-08T20:00:00Z', ?, ?, ?, ?, ?, -110)",
        (gid, book, observed_at, spread_home, -spread_home, spread_price_home),
    )
    conn.commit()


def test_no_post_kickoff_observation_ever_used():
    kickoff = "2026-08-08T20:00:00Z"
    snapshots = [
        _snap("2026-08-08T20:05:00Z"),
        _snap("2026-08-08T21:00:00Z"),
    ]
    result = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots)
    assert all(v is None for v in result.values())
    print("PASS: post-kickoff observations never fill any checkpoint, including earliest_observed")


def test_earliest_observed_picks_true_earliest():
    kickoff = "2026-08-08T20:00:00Z"
    snapshots = [
        _snap("2026-08-05T12:00:00Z", spread_home=-2.5),
        _snap("2026-08-07T12:00:00Z", spread_home=-3.0),
        _snap("2026-08-08T19:00:00Z", spread_home=-3.5),
    ]
    result = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots)
    assert result["earliest_observed"]["observed_at"] == "2026-08-05T12:00:00Z"
    assert result["earliest_observed"]["spread_home"] == -2.5
    print("PASS: earliest_observed correctly picks the true earliest pregame observation")


def test_t24h_within_tolerance():
    kickoff = "2026-08-08T20:00:00Z"
    snapshots = [_snap("2026-08-07T15:00:00Z", spread_home=-3.0)]
    result = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots)
    assert result["T-24h"] is not None
    assert result["T-24h"]["spread_home"] == -3.0
    print("PASS: T-24h correctly matches an observation within tolerance")


def test_t24h_outside_tolerance_stays_null():
    kickoff = "2026-08-08T20:00:00Z"
    snapshots = [_snap("2026-08-06T00:00:00Z", spread_home=-3.0)]
    result = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots)
    assert result["T-24h"] is None
    print("PASS: an observation outside the documented tolerance correctly leaves the checkpoint NULL")


def test_last_observed_respects_max_lookback():
    kickoff = "2026-08-08T20:00:00Z"
    snapshots = [_snap("2026-08-08T10:00:00Z", spread_home=-3.0)]
    result = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots)
    assert result["last_observed_before_kickoff"] is None

    snapshots2 = [_snap("2026-08-08T18:00:00Z", spread_home=-3.0)]
    result2 = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots2)
    assert result2["last_observed_before_kickoff"] is not None
    print("PASS: last_observed_before_kickoff respects its explicit max-lookback rule")


def test_no_snapshots_all_checkpoints_null():
    result = reconstruct_checkpoints_for_book(1, "2026-08-08T20:00:00Z", "draftkings", [])
    assert all(v is None for v in result.values())
    print("PASS: a book with zero snapshots produces all-NULL checkpoints, never fabricated values")


def test_book_last_update_preferred_over_observed_at():
    kickoff = "2026-08-08T20:00:00Z"
    snapshots = [{
        "observed_at": "2026-08-08T19:00:00Z",
        "book_last_update": "2026-08-07T19:00:00Z",
        "spread_home": -3.0, "spread_away": 3.0, "spread_price_home": -110, "spread_price_away": -110,
    }]
    result = reconstruct_checkpoints_for_book(1, kickoff, "draftkings", snapshots)
    assert result["T-1h"] is None
    print("PASS: book_last_update (the book's own reported price time) is correctly preferred over our poll time")


def test_compute_movement_spread_and_price_only():
    checkpoints = {
        "earliest_observed": _snap("2026-08-05T12:00:00Z", spread_home=-2.5, spread_price_home=-110),
        "T-24h": _snap("2026-08-07T20:00:00Z", spread_home=-3.0, spread_price_home=-110),
        "T-6h": _snap("2026-08-08T14:00:00Z", spread_home=-3.0, spread_price_home=-120),
        "T-1h": None,
        "last_observed_before_kickoff": _snap("2026-08-08T19:30:00Z", spread_home=-3.5, spread_price_home=-115),
    }
    movements = compute_movement(checkpoints)
    assert len(movements) == 3
    assert movements[0]["spread_home_delta"] == -0.5
    assert movements[1]["spread_home_delta"] == 0.0
    assert movements[1]["price_only_move"] is True
    assert movements[2]["spread_home_delta"] == -0.5
    print("PASS: movement computation correctly identifies spread-number moves and price-only moves separately")


def test_cross_book_disagreement_real_db():
    conn = fresh_conn()
    kickoff = "2026-08-08T20:00:00Z"
    _insert_snapshot(conn, 1, "draftkings", "2026-08-08T19:00:00Z", spread_home=-3.0)
    _insert_snapshot(conn, 1, "fanduel", "2026-08-08T19:00:00Z", spread_home=-2.5)
    _insert_snapshot(conn, 1, "betmgm", "2026-08-08T19:00:00Z", spread_home=-3.0)

    disagreement = compute_cross_book_disagreement(conn, 1, kickoff)
    t1h = disagreement["T-1h"]
    assert set(t1h["per_book"].keys()) == {"draftkings", "fanduel", "betmgm"}
    assert t1h["range"] == 0.5
    conn.close()
    print("PASS: cross-book disagreement correctly compares real per-book values without computing a consensus")


def test_single_book_no_disagreement_range():
    conn = fresh_conn()
    _insert_snapshot(conn, 1, "draftkings", "2026-08-08T19:00:00Z", spread_home=-3.0)
    disagreement = compute_cross_book_disagreement(conn, 1, "2026-08-08T20:00:00Z")
    assert disagreement["T-1h"]["range"] is None
    conn.close()
    print("PASS: cross-book disagreement range is NULL (not zero) when only one book has an observation")


def test_movement_collapses_identical_repeated_observations():
    """When a book has only one real observation, multiple checkpoint
    names correctly point to that same observation -- movement must not
    report a fake zero-change entry between an observation and itself."""
    single = _snap("2026-08-08T19:00:00Z", spread_home=-3.0, spread_price_home=-110)
    checkpoints = {
        "earliest_observed": single, "T-24h": None, "T-6h": None,
        "T-1h": single, "last_observed_before_kickoff": single,
    }
    movements = compute_movement(checkpoints)
    assert movements == [], "identical repeated observations must produce zero movement entries, not fake ones"
    print("PASS: movement correctly collapses consecutive checkpoints pointing at the same underlying observation")


if __name__ == "__main__":
    test_no_post_kickoff_observation_ever_used()
    test_earliest_observed_picks_true_earliest()
    test_t24h_within_tolerance()
    test_t24h_outside_tolerance_stays_null()
    test_last_observed_respects_max_lookback()
    test_no_snapshots_all_checkpoints_null()
    test_book_last_update_preferred_over_observed_at()
    test_compute_movement_spread_and_price_only()
    test_cross_book_disagreement_real_db()
    test_single_book_no_disagreement_range()
    test_movement_collapses_identical_repeated_observations()
    print("\nAll checkpoint_reconstruction tests passed.")
