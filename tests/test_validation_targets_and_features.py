"""
Tests for validation/targets/* and validation/feature_sets/*.

Run with: python tests/test_validation_targets_and_features.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.targets.base import Target, EVIDENCE_FAMILIES  # noqa: E402
from validation.targets.outcome_diagnostic import OutcomeDiagnosticTarget  # noqa: E402
from validation.targets.market_movement import MovementDirectionTarget, MovementMagnitudeTarget  # noqa: E402
from validation.targets.market_resistance import ResistanceDivergenceTarget, ReversalTarget  # noqa: E402
from validation.targets.market_clv import CLVTarget  # noqa: E402
from validation.targets.cross_book import CrossBookDisagreementTarget  # noqa: E402
from validation.targets.split_target import SplitLabelTarget  # noqa: E402
from validation.feature_sets.rung0_schedule import Rung0ScheduleFeatureSet, COLUMNS as RUNG0_COLUMNS  # noqa: E402
from validation.feature_sets.rung1_v1_recency import Rung1V1RecencyFeatureSet, COLUMNS as RUNG1_COLUMNS  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from features.build_engineered_features_v1 import (  # noqa: E402
    get_all_games, get_latest_results_by_canonical_game, build_team_index,
    build_feature_row, write_feature_row, FEATURE_SET_VERSION,
)

TEST_DIR = Path(__file__).parent / "_validation_targets_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, sport, home, away, ct):
    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
                 (sport, home, away, ct))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_result(conn, cgid, hs, aws, retrieved="2026-01-01T00:00:00Z"):
    wh = 1 if hs > aws else (0 if aws > hs else None)
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?,?, 'STATUS_FINAL', 1, ?, ?, ?, 2025, 2, 'espn', ?)",
        (cgid, retrieved, hs, aws, wh, retrieved),
    )
    conn.commit()


# ============================================================
# Target interface validation
# ============================================================

def test_diagnostic_target_forbids_evidence_family():
    class BadTarget(Target):
        name = "bad"
        target_type = "diagnostic"
        evidence_family = "directional"  # invalid -- diagnostic must not have one
        metric_family = "classification"

        def get_label(self, conn, cgid, cutoff):
            return None

        def data_availability_check(self, conn):
            return 0

    try:
        BadTarget()
        assert False, "must have raised"
    except ValueError as e:
        assert "must not declare an evidence_family" in str(e)
    print("PASS: a diagnostic target declaring an evidence_family is rejected at construction")


def test_behavioral_target_requires_known_evidence_family():
    class BadTarget(Target):
        name = "bad2"
        target_type = "behavioral"
        evidence_family = "made_up_family"
        metric_family = "regression"

        def get_label(self, conn, cgid, cutoff):
            return None

        def data_availability_check(self, conn):
            return 0

    try:
        BadTarget()
        assert False, "must have raised"
    except ValueError as e:
        assert "known evidence_family" in str(e)
    print("PASS: a behavioral target with an unknown evidence_family is rejected at construction")


def test_all_targets_construct_validly_and_interface_types_are_correct():
    """
    MovementDirectionTarget, MovementMagnitudeTarget, ReversalTarget, and
    CrossBookDisagreementTarget are now REAL implementations (see
    tests/test_market_evidence_targets.py for their actual correctness
    tests) -- calling get_label with conn=None on them is no longer a
    valid dormant-stub check, it's just an AttributeError, since they
    genuinely try to query the database now. Only ResistanceDivergenceTarget,
    CLVTarget, and SplitLabelTarget remain genuinely dormant.
    """
    still_dormant = [ResistanceDivergenceTarget, CLVTarget, SplitLabelTarget]
    for cls in still_dormant:
        instance = cls()
        assert instance.target_type == "behavioral"
        assert instance.evidence_family in EVIDENCE_FAMILIES
        assert instance.get_label(None, 1, None) is None  # dormant -- always None, never fabricated
    print(f"PASS: the {len(still_dormant)} still-dormant behavioral targets "
          f"(ResistanceDivergenceTarget, CLVTarget, SplitLabelTarget) construct validly "
          f"and return None with no database access at all")

    now_implemented = [MovementDirectionTarget, MovementMagnitudeTarget, ReversalTarget, CrossBookDisagreementTarget]
    for cls in now_implemented:
        instance = cls()
        assert instance.target_type == "behavioral"
        assert instance.evidence_family in EVIDENCE_FAMILIES
    print(f"PASS: the {len(now_implemented)} now-implemented targets construct validly with correct "
          f"interface metadata (their actual computation correctness is tested separately in "
          f"tests/test_market_evidence_targets.py, against real data, not conn=None)")


# ============================================================
# OutcomeDiagnosticTarget correctness
# ============================================================

def test_outcome_diagnostic_target_win_loss_tie_missing():
    conn = fresh_conn()
    target = OutcomeDiagnosticTarget()

    g_home_win = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g_home_win, 24, 17)
    g_away_win = _make_game(conn, "nfl", "nfl_kc", "nfl_den", "2025-09-14T20:00:00Z")
    _make_result(conn, g_away_win, 10, 24)
    g_tie = _make_game(conn, "nfl", "nfl_kc", "nfl_lv", "2025-09-21T20:00:00Z")
    _make_result(conn, g_tie, 20, 20)
    g_no_result = _make_game(conn, "nfl", "nfl_kc", "nfl_ne", "2025-09-28T20:00:00Z")

    assert target.get_label(conn, g_home_win, None) == 1
    assert target.get_label(conn, g_away_win, None) == 0
    assert target.get_label(conn, g_tie, None) is None, "a tie must not be fabricated into a binary label"
    assert target.get_label(conn, g_no_result, None) is None

    availability = target.data_availability_check(conn)
    assert availability == 2, "only the 2 decisive (non-tie) results count as usable labels"
    conn.close()
    print("PASS: OutcomeDiagnosticTarget correctly labels wins/losses, refuses ties, handles missing results")


# ============================================================
# Feature sets
# ============================================================

def test_rung0_and_rung1_read_correct_columns():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    g1 = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z")
    _make_result(conn, g1, 24, 17)

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)
    row = build_feature_row(team_index, all_games, g1, "nfl", "nfl_kc", "nfl_lac", "2025-09-07T20:00:00Z", latest_results)
    row.update({"feature_set_version": FEATURE_SET_VERSION, "computed_at": "2026-01-01T00:00:00Z",
                "point_in_time_cutoff": "2025-09-07T20:00:00Z"})
    write_feature_row(conn, row)

    rung0 = Rung0ScheduleFeatureSet()
    rung1 = Rung1V1RecencyFeatureSet()

    f0 = rung0.get_features(conn, g1, None)
    f1 = rung1.get_features(conn, g1, None)
    assert f0 is not None and set(f0.keys()) == set(RUNG0_COLUMNS)
    assert f1 is not None and set(f1.keys()) == set(RUNG1_COLUMNS)
    assert set(RUNG0_COLUMNS).issubset(set(RUNG1_COLUMNS)), "Rung 1 must be cumulative, including Rung 0's columns"

    cutoff0 = rung0.point_in_time_cutoff_used(conn, g1)
    cutoff1 = rung1.point_in_time_cutoff_used(conn, g1)
    assert cutoff0 == cutoff1 == "2025-09-07T20:00:00Z"
    conn.close()
    print("PASS: Rung 0 and Rung 1 read the correct, cumulative column sets from engineered_features")


def test_feature_set_returns_none_for_unknown_game():
    conn = fresh_conn()
    rung0 = Rung0ScheduleFeatureSet()
    assert rung0.get_features(conn, 999999, None) is None
    conn.close()
    print("PASS: a feature set returns None (not an error or fabricated row) for a game with no engineered_features row")


if __name__ == "__main__":
    test_diagnostic_target_forbids_evidence_family()
    test_behavioral_target_requires_known_evidence_family()
    test_all_targets_construct_validly_and_interface_types_are_correct()
    test_outcome_diagnostic_target_win_loss_tie_missing()
    test_rung0_and_rung1_read_correct_columns()
    test_feature_set_returns_none_for_unknown_game()
    print("\nAll validation targets/feature_sets tests passed.")
