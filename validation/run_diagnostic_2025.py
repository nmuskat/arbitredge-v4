"""
validation/run_diagnostic_2025.py

The 2025 diagnostic run. Does exactly two things, both explicitly
non-behavioral:

  1. Runs Rung 0 vs Rung 1 (schedule-only vs schedule+V1 recency/rest) on
     the OutcomeDiagnosticTarget, against the real 1,033-game 2025 cohort,
     via the full walk-forward/ablation/statistics/experiment-logging
     pipeline. This proves the harness machinery works end to end against
     real data. It is NOT evidence about Public Pressure, Handle
     Intensity, Side Preference, or Book Need -- it measures only whether
     V1's recency/rest features correlate with who actually won.

  2. Creates 'mechanical_diagnostic_holdout_2025' (a small locked slice of
     the 2025 cohort, excluded from step 1's rolling folds) and
     demonstrates the holdout access mechanics against the real database:
     first access succeeds and returns the held-out games; a second access
     is refused and logged. This holdout has ZERO authority over any
     future behavioral rung advancement -- it exists solely to prove the
     lock/access/refusal mechanics work before they matter for real.

Usage:
    python validation/run_diagnostic_2025.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from validation.targets.outcome_diagnostic import OutcomeDiagnosticTarget  # noqa: E402
from validation.feature_sets.rung0_schedule import Rung0ScheduleFeatureSet, COLUMNS as RUNG0_COLUMNS  # noqa: E402
from validation.feature_sets.rung1_v1_recency import Rung1V1RecencyFeatureSet, COLUMNS as RUNG1_COLUMNS  # noqa: E402
from validation.harness.run_experiment import run_single_comparison, DIAGNOSTIC_BANNER  # noqa: E402
from validation.harness.holdout import (  # noqa: E402
    create_holdout, access_holdout_for_confirmation, HoldoutAlreadyAccessedError,
)
import sqlite3  # noqa: E402

MECHANICAL_HOLDOUT_KEY = "mechanical_diagnostic_holdout_2025"
HOLDOUT_SIZE = 30

NFL_SEASON_START = "2025-08-01T00:00:00Z"
NFL_SEASON_END = "2026-02-01T00:00:00Z"
CFB_SEASON_START = "2025-08-01T00:00:00Z"
CFB_SEASON_END = "2026-02-01T00:00:00Z"


def get_cohort_with_time(conn):
    fbs_allowlist = load_fbs_allowlist(2025)
    nfl = conn.execute(
        "SELECT canonical_game_id, commence_time FROM games_canonical "
        "WHERE sport='nfl' AND commence_time >= ? AND commence_time < ? "
        "AND canonical_game_id IN (SELECT canonical_game_id FROM game_results)",
        (NFL_SEASON_START, NFL_SEASON_END),
    ).fetchall()
    cfb_raw = conn.execute(
        "SELECT canonical_game_id, commence_time, home_team_id, away_team_id FROM games_canonical "
        "WHERE sport='cfb' AND commence_time >= ? AND commence_time < ? "
        "AND canonical_game_id IN (SELECT canonical_game_id FROM game_results)",
        (CFB_SEASON_START, CFB_SEASON_END),
    ).fetchall()
    cfb = [(r[0], r[1]) for r in cfb_raw if r[2] in fbs_allowlist and r[3] in fbs_allowlist]
    return nfl + cfb


def main():
    print(DIAGNOSTIC_BANNER)
    print("2025 DIAGNOSTIC RUN -- harness mechanics validation only.\n")

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    cohort = get_cohort_with_time(conn)
    print(f"Approved 2025 cohort: {len(cohort)} games (expect 1033)")

    sorted_cohort = sorted(cohort, key=lambda g: g[1])
    holdout_candidates = [g for g, _ in sorted_cohort[-HOLDOUT_SIZE:]]
    holdout_status = create_holdout(conn, MECHANICAL_HOLDOUT_KEY, holdout_candidates)
    print(f"Mechanical holdout '{MECHANICAL_HOLDOUT_KEY}': {holdout_status}, "
          f"{len(holdout_candidates)} candidate games (most recent slice)")

    target = OutcomeDiagnosticTarget()
    rung0 = Rung0ScheduleFeatureSet()
    rung1 = Rung1V1RecencyFeatureSet()

    print(f"\n{'=' * 70}\nSTEP 1: Rung 0 vs Rung 1, OutcomeDiagnosticTarget, real 2025 cohort\n{'=' * 70}")
    result = run_single_comparison(
        conn, "2025_diagnostic_rung0_vs_rung1", target, rung0, rung1,
        RUNG0_COLUMNS, RUNG1_COLUMNS, cohort,
        holdout_key=MECHANICAL_HOLDOUT_KEY, n_folds=8,
    )
    print(f"\nStep 1 result summary (experiment_id={result['experiment_id']}): {result}")

    print(f"\n{'=' * 70}\nSTEP 2: holdout access mechanics self-test (real database)\n{'=' * 70}")
    print("First access (should succeed and return the held-out game list):")
    first_access = access_holdout_for_confirmation(conn, MECHANICAL_HOLDOUT_KEY, experiment_id=result["experiment_id"])
    print(f"  SUCCESS -- returned {len(first_access)} game IDs")

    print("Second access (should be refused, not return data):")
    try:
        access_holdout_for_confirmation(conn, MECHANICAL_HOLDOUT_KEY, experiment_id=result["experiment_id"])
        print("  UNEXPECTED: second access was NOT refused -- this is a bug, investigate immediately")
    except HoldoutAlreadyAccessedError as e:
        print(f"  CORRECTLY REFUSED: {e}")

    access_log_count = conn.execute(
        "SELECT COUNT(*) FROM validation_holdout_access_log WHERE holdout_key = ?", (MECHANICAL_HOLDOUT_KEY,)
    ).fetchone()[0]
    print(f"Total logged access attempts for this holdout: {access_log_count} (expect 2: 1 success + 1 refusal)")

    conn.close()

    print(DIAGNOSTIC_BANNER)
    print("Reminder: the Step 1 result above describes correlation with GAME OUTCOMES ONLY.")
    print(f"'{MECHANICAL_HOLDOUT_KEY}' has ZERO authority over Public Pressure, Handle Intensity, "
          f"Side Preference, or Book Need rung advancement.")
    print(DIAGNOSTIC_BANNER)


if __name__ == "__main__":
    main()
