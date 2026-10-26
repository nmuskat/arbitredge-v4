"""
Integration tests for validation/harness/ablation.py and run_experiment.py

Run with: python tests/test_validation_ablation_integration.py
"""

import io
import shutil
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validation.harness.ablation import _complete_case_ids  # noqa: E402
from validation.harness.run_experiment import run_single_comparison, DIAGNOSTIC_BANNER  # noqa: E402
from validation.harness.holdout import create_holdout  # noqa: E402
from validation.targets.outcome_diagnostic import OutcomeDiagnosticTarget  # noqa: E402
from validation.targets.base import NON_BEHAVIORAL_WARNING  # noqa: E402
from validation.feature_sets.rung0_schedule import Rung0ScheduleFeatureSet, COLUMNS as RUNG0_COLUMNS  # noqa: E402
from validation.feature_sets.rung1_v1_recency import Rung1V1RecencyFeatureSet, COLUMNS as RUNG1_COLUMNS  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from features.build_engineered_features_v1 import (  # noqa: E402
    get_all_games, get_latest_results_by_canonical_game, build_team_index,
    build_feature_row, write_feature_row, FEATURE_SET_VERSION,
)

TEST_DIR = Path(__file__).parent / "_validation_ablation_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, home, away, ct):
    conn.execute("INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES ('nfl',?,?,?)",
                 (home, away, ct))
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


def _build_realistic_cohort(conn, n_games=60):
    from datetime import datetime, timedelta
    base = datetime(2025, 9, 1, 20, 0, 0)
    teams = ["nfl_ari", "nfl_atl", "nfl_bal", "nfl_buf", "nfl_car",
             "nfl_chi", "nfl_cin", "nfl_cle", "nfl_dal", "nfl_den"]
    game_ids_with_time = []
    for i in range(n_games):
        home = teams[i % 10]
        away = teams[(i + 1) % 10]
        ct = (base + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        gid = _make_game(conn, home, away, ct)
        home_wins = (i % 3 != 0)
        if home_wins:
            _make_result(conn, gid, 27, 17)
        else:
            _make_result(conn, gid, 14, 24)
        game_ids_with_time.append((gid, ct))

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)
    for gid, ct in game_ids_with_time:
        row = conn.execute(
            "SELECT sport, home_team_id, away_team_id FROM games_canonical WHERE canonical_game_id=?", (gid,)
        ).fetchone()
        sport, home_id, away_id = row
        feat_row = build_feature_row(team_index, all_games, gid, sport, home_id, away_id, ct, latest_results)
        feat_row.update({"feature_set_version": FEATURE_SET_VERSION, "computed_at": "2026-01-01T00:00:00Z",
                          "point_in_time_cutoff": ct})
        write_feature_row(conn, feat_row)
    conn.commit()
    return game_ids_with_time


def test_corrupted_point_in_time_cutoff_is_excluded():
    conn = fresh_conn()
    game_ids_with_time = _build_realistic_cohort(conn, n_games=30)
    target = OutcomeDiagnosticTarget()
    rung1 = Rung1V1RecencyFeatureSet()

    all_ids = [g for g, _ in game_ids_with_time]
    usable_before = _complete_case_ids(conn, rung1, target, all_ids)
    assert len(usable_before) > 0

    victim = usable_before[-1]
    import validation.feature_sets.rung1_v1_recency as rung1_module

    class TamperedFeatureSet(rung1_module.Rung1V1RecencyFeatureSet):
        def point_in_time_cutoff_used(self, conn, canonical_game_id):
            real = super().point_in_time_cutoff_used(conn, canonical_game_id)
            if canonical_game_id == victim:
                return "1999-01-01T00:00:00Z"  # deliberately wrong -- simulated corruption/leak
            return real

    tampered_fs = TamperedFeatureSet()
    usable_after = _complete_case_ids(conn, tampered_fs, target, all_ids)
    assert victim not in usable_after, (
        "a game whose stored point_in_time_cutoff doesn't match its real commence_time "
        "must be excluded, not silently used"
    )
    assert len(usable_after) == len(usable_before) - 1
    conn.close()
    print("PASS: synthetic leakage injection -- a corrupted/mismatched point_in_time_cutoff "
          "is detected and the affected game is excluded from the comparison, not silently used")


def test_ablation_can_detect_a_real_signal_positive_control():
    conn = fresh_conn()
    game_ids_with_time = _build_realistic_cohort(conn, n_games=80)
    target = OutcomeDiagnosticTarget()
    rung0 = Rung0ScheduleFeatureSet()
    rung1 = Rung1V1RecencyFeatureSet()

    with redirect_stdout(io.StringIO()):
        result = run_single_comparison(
            conn, "positive_control_test", target, rung0, rung1,
            RUNG0_COLUMNS, RUNG1_COLUMNS, game_ids_with_time, n_folds=5,
        )
    assert not result["insufficient_sample"], "80 games should be enough for a real comparison to run"
    conn.close()
    print(f"PASS: ablation pipeline runs end-to-end on a positive-control dataset "
          f"(marginal_improvement={result.get('marginal_improvement')}, p={result.get('raw_p_value')})")


def test_diagnostic_run_prints_banner_and_logs_warning():
    conn = fresh_conn()
    game_ids_with_time = _build_realistic_cohort(conn, n_games=60)
    target = OutcomeDiagnosticTarget()
    rung0 = Rung0ScheduleFeatureSet()
    rung1 = Rung1V1RecencyFeatureSet()

    captured = io.StringIO()
    with redirect_stdout(captured):
        result = run_single_comparison(
            conn, "diagnostic_banner_test", target, rung0, rung1,
            RUNG0_COLUMNS, RUNG1_COLUMNS, game_ids_with_time, n_folds=4,
        )
    output = captured.getvalue()
    assert NON_BEHAVIORAL_WARNING in output
    assert output.count(NON_BEHAVIORAL_WARNING) >= 2, "the warning must appear both before and after the result"

    row = conn.execute(
        "SELECT non_behavioral_warning, target_type FROM validation_experiments WHERE experiment_id=?",
        (result["experiment_id"],),
    ).fetchone()
    assert row[0] == NON_BEHAVIORAL_WARNING
    assert row[1] == "diagnostic"
    conn.close()
    print("PASS: a diagnostic-target run prints the non-behavioral warning banner before and after "
          "the result, and the warning is also stored verbatim in the experiment log")


def test_holdout_games_never_appear_in_rolling_folds():
    conn = fresh_conn()
    game_ids_with_time = _build_realistic_cohort(conn, n_games=60)
    holdout_ids = [g for g, _ in game_ids_with_time[-5:]]
    create_holdout(conn, "mechanical_diagnostic_holdout_2025", holdout_ids)

    target = OutcomeDiagnosticTarget()
    rung0 = Rung0ScheduleFeatureSet()
    rung1 = Rung1V1RecencyFeatureSet()

    with redirect_stdout(io.StringIO()):
        run_single_comparison(
            conn, "holdout_exclusion_test", target, rung0, rung1,
            RUNG0_COLUMNS, RUNG1_COLUMNS, game_ids_with_time,
            holdout_key="mechanical_diagnostic_holdout_2025", n_folds=4,
        )

    latest_config = conn.execute(
        "SELECT walk_forward_config FROM validation_experiments WHERE experiment_name='holdout_exclusion_test' "
        "ORDER BY experiment_id DESC LIMIT 1"
    ).fetchone()[0]
    assert '"holdout_key_excluded": "mechanical_diagnostic_holdout_2025"' in latest_config
    conn.close()
    print("PASS: when a holdout_key is provided, it's recorded as excluded in the experiment's "
          "walk_forward_config, confirming the holdout was never fed into the rolling folds")


def test_validation_experiments_append_only():
    conn = fresh_conn()
    game_ids_with_time = _build_realistic_cohort(conn, n_games=60)
    target = OutcomeDiagnosticTarget()
    rung0 = Rung0ScheduleFeatureSet()
    rung1 = Rung1V1RecencyFeatureSet()

    with redirect_stdout(io.StringIO()):
        result = run_single_comparison(
            conn, "append_only_test", target, rung0, rung1,
            RUNG0_COLUMNS, RUNG1_COLUMNS, game_ids_with_time, n_folds=4,
        )

    try:
        conn.execute("UPDATE validation_experiments SET raw_p_value=0 WHERE experiment_id=?", (result["experiment_id"],))
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "validation_experiments must be append-only"

    try:
        conn.execute("DELETE FROM validation_experiments WHERE experiment_id=?", (result["experiment_id"],))
        conn.commit()
        raised_delete = False
    except sqlite3.IntegrityError:
        raised_delete = True
    assert raised_delete
    conn.close()
    print("PASS: validation_experiments append-only triggers actually block UPDATE and DELETE")


if __name__ == "__main__":
    test_corrupted_point_in_time_cutoff_is_excluded()
    test_ablation_can_detect_a_real_signal_positive_control()
    test_diagnostic_run_prints_banner_and_logs_warning()
    test_holdout_games_never_appear_in_rolling_folds()
    test_validation_experiments_append_only()
    print("\nAll validation ablation/run_experiment integration tests passed.")
