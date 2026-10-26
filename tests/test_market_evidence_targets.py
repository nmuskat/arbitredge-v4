"""
Tests for validation/targets/market_movement.py, market_resistance.py
(ReversalTarget only), and cross_book.py's real implementations.

Run with: python tests/test_market_evidence_targets.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.targets.market_movement import (  # noqa: E402
    MovementDirectionTarget, MovementMagnitudeTarget, crosses_key_number, _select_book,
)
from validation.targets.market_resistance import ReversalTarget, ResistanceDivergenceTarget  # noqa: E402
from validation.targets.cross_book import CrossBookDisagreementTarget  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_market_evidence_targets_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, ct="2026-08-15T20:00:00Z"):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) "
        "VALUES ('nfl','nfl_kc','nfl_lac',?)",
        (ct,),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _snap(conn, gid, book, observed_at, spread_home, price_home=-110, commence="2026-08-15T20:00:00Z"):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home, spread_away, spread_price_home, spread_price_away) "
        "VALUES (?, 'nfl', 'KC', 'LAC', ?, ?, ?, ?, ?, ?, -110)",
        (gid, commence, book, observed_at, spread_home, -spread_home, price_home),
    )
    conn.commit()


KICKOFF = "2026-08-15T20:00:00Z"


def test_direction_toward_home_and_magnitude():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -2.5)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -4.5)

    direction = MovementDirectionTarget()
    magnitude = MovementMagnitudeTarget()
    assert direction.get_label(conn, gid, KICKOFF) == 1
    assert magnitude.get_label(conn, gid, KICKOFF) == 2.0
    conn.close()
    print("PASS: spread moving more negative correctly labeled direction=1 (toward home), magnitude=2.0")


def test_direction_toward_away():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -4.5)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -2.5)

    direction = MovementDirectionTarget()
    assert direction.get_label(conn, gid, KICKOFF) == 0
    conn.close()
    print("PASS: spread moving less negative correctly labeled direction=0 (toward away)")


def test_no_movement_returns_none_not_fabricated():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -3.0)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -3.0)

    direction = MovementDirectionTarget()
    magnitude = MovementMagnitudeTarget()
    assert direction.get_label(conn, gid, KICKOFF) is None
    assert magnitude.get_label(conn, gid, KICKOFF) == 0.0
    conn.close()
    print("PASS: no real movement correctly returns None for direction (not fabricated) "
          "while magnitude correctly reports 0.0")


def test_missing_checkpoint_returns_none():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -3.0)

    direction = MovementDirectionTarget()
    assert direction.get_label(conn, gid, KICKOFF) is None
    conn.close()
    print("PASS: a missing required checkpoint correctly returns None")


def test_no_post_kickoff_snapshot_used():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-15T18:30:00Z", -2.5)  # 1.5h before kickoff -- within last-observed lookback
    _snap(conn, gid, "draftkings", "2026-08-15T20:05:00Z", -10.0)  # AFTER kickoff -- must be ignored entirely

    direction = MovementDirectionTarget()
    magnitude = MovementMagnitudeTarget()
    # Only one real PREGAME observation exists -> earliest_observed and
    # last_observed_before_kickoff both resolve to that SAME observation ->
    # zero real movement -> direction None, magnitude 0.0 (NOT influenced by
    # the huge post-kickoff -10.0 value, which must never even be considered)
    assert direction.get_label(conn, gid, KICKOFF) is None
    assert magnitude.get_label(conn, gid, KICKOFF) == 0.0
    conn.close()
    print("PASS: a post-kickoff observation never contributes to movement direction/magnitude, "
          "even when it would produce a dramatically different (wrong) result if leaked in")


def test_explicit_book_selection_vs_default_most_complete():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "thinbook", "2026-08-15T19:00:00Z", -3.0)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -5.0)

    resolved_default = _select_book(conn, gid)
    assert resolved_default == "draftkings"

    magnitude = MovementMagnitudeTarget()
    assert magnitude.get_label(conn, gid, KICKOFF) == 3.0
    assert magnitude.get_label(conn, gid, KICKOFF, book="thinbook") == 0.0
    conn.close()
    print("PASS: default book selection picks the most complete path; explicit book= override works correctly")


def test_key_number_crossing_metadata():
    assert crosses_key_number(-2.5, -3.5) is True
    assert crosses_key_number(-2.5, -2.8) is False
    assert crosses_key_number(-6.5, -7.5) is True
    assert crosses_key_number(None, -3.0) is None
    print("PASS: key-number crossing is correctly detected as descriptive metadata, "
          "with no behavioral weight attached")


def test_monotonic_path_no_reversal():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-08-14T12:00:00Z", -2.5)
    _snap(conn, gid, "draftkings", "2026-08-15T12:00:00Z", -3.0)

    reversal = ReversalTarget()
    assert reversal.get_label(conn, gid, KICKOFF) == 0
    conn.close()
    print("PASS: a monotonic (one-directional) path correctly labeled as no reversal (0)")


def test_real_reversal_detected():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-08-14T00:00:00Z", -3.5)
    _snap(conn, gid, "draftkings", "2026-08-15T12:00:00Z", -2.5)

    reversal = ReversalTarget()
    assert reversal.get_label(conn, gid, KICKOFF) == 1
    conn.close()
    print("PASS: a real directional reversal in the observed path is correctly detected (1)")


def test_zero_delta_observations_do_not_fake_a_transition():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-08-13T13:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-08-14T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-08-15T12:00:00Z", -3.0)

    reversal = ReversalTarget()
    assert reversal.get_label(conn, gid, KICKOFF) is None
    conn.close()
    print("PASS: zero-delta (unchanged) observations are correctly excluded from transition counting, "
          "not fabricated into a false reversal signal")


def test_reversal_insufficient_data_returns_none():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -3.0)

    reversal = ReversalTarget()
    assert reversal.get_label(conn, gid, KICKOFF) is None
    conn.close()
    print("PASS: insufficient path data correctly returns None, not a guessed reversal label")


def test_resistance_divergence_stays_dormant():
    target = ResistanceDivergenceTarget()
    assert target.get_label(None, 1, KICKOFF) is None
    print("PASS: ResistanceDivergenceTarget remains explicitly dormant, as instructed")


def test_cross_book_disagreement_default_checkpoint():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -3.0)
    _snap(conn, gid, "fanduel", "2026-08-15T19:00:00Z", -2.5)
    _snap(conn, gid, "betonline", "2026-08-15T19:00:00Z", -3.5)

    target = CrossBookDisagreementTarget()
    assert target.get_label(conn, gid, KICKOFF) == 1.0
    conn.close()
    print("PASS: cross-book disagreement at the default (last_observed_before_kickoff) checkpoint is correct")


def test_cross_book_disagreement_explicit_checkpoint():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-13T12:00:00Z", -2.0)
    _snap(conn, gid, "fanduel", "2026-08-13T12:00:00Z", -3.0)

    target = CrossBookDisagreementTarget()
    assert target.get_label(conn, gid, KICKOFF, checkpoint_name="earliest_observed") == 1.0
    conn.close()
    print("PASS: an explicit checkpoint_name correctly selects that checkpoint's disagreement")


def test_cross_book_disagreement_single_book_is_none():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-08-15T19:00:00Z", -3.0)

    target = CrossBookDisagreementTarget()
    assert target.get_label(conn, gid, KICKOFF) is None
    conn.close()
    print("PASS: cross-book disagreement with only one book present correctly returns None, not zero")


def test_cross_book_invalid_checkpoint_raises():
    conn = fresh_conn()
    gid = _make_game(conn)
    target = CrossBookDisagreementTarget()
    try:
        target.get_label(conn, gid, KICKOFF, checkpoint_name="not_a_real_checkpoint")
        assert False, "must raise on an invalid checkpoint name"
    except ValueError:
        pass
    conn.close()
    print("PASS: an invalid checkpoint_name is rejected explicitly rather than silently misbehaving")


if __name__ == "__main__":
    test_direction_toward_home_and_magnitude()
    test_direction_toward_away()
    test_no_movement_returns_none_not_fabricated()
    test_missing_checkpoint_returns_none()
    test_no_post_kickoff_snapshot_used()
    test_explicit_book_selection_vs_default_most_complete()
    test_key_number_crossing_metadata()
    test_monotonic_path_no_reversal()
    test_real_reversal_detected()
    test_zero_delta_observations_do_not_fake_a_transition()
    test_reversal_insufficient_data_returns_none()
    test_resistance_divergence_stays_dormant()
    test_cross_book_disagreement_default_checkpoint()
    test_cross_book_disagreement_explicit_checkpoint()
    test_cross_book_disagreement_single_book_is_none()
    test_cross_book_invalid_checkpoint_raises()
    print("\nAll market evidence target tests passed.")
