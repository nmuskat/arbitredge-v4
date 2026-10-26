"""
diagnostics/evidence_readiness_monitor.py

READ-ONLY. Reports how much usable 2026 regular-season evidence has
accumulated -- counts only, never a declared "ready" threshold. Reuses
the exact cohort-selection logic already built and tested
(build_engineered_features_v2_batch.py's get_nfl_regular_cohort_v2/
get_cfb_regular_cohort_v2), the same NAMED_CUTOFFS, the behavioral
FeatureSet wrappers, and the real (non-dormant) market-response targets
-- nothing here recomputes anything, it only counts what already exists.

Reports, separately for NFL and CFB, at each named cutoff:
  - confirmed regular-season games in the cohort
  - games with a valid (non-None) Handle Intensity score
  - games with a valid (non-None) Side Preference score
  - coverage_count distribution for each model
  - games with valid MovementDirection/MovementMagnitude/
    CrossBookDisagreement evidence
  - overlap counts: games where BOTH a behavioral score AND the
    corresponding evidence family are simultaneously available -- this
    is the number that actually matters for a future validation run,
    not the raw cohort size

No sample-size judgment is made anywhere in this script. It reports what
exists; it does not decide whether that is enough.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from features.build_engineered_features_v2_batch import (  # noqa: E402
    get_nfl_regular_cohort_v2, get_cfb_regular_cohort_v2, check_2026_fbs_membership_available, NAMED_CUTOFFS,
)
from features.market_state_as_of import resolve_named_cutoff  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from validation.feature_sets.behavioral_handle_intensity_v1 import BehavioralHandleIntensityV1FeatureSet  # noqa: E402
from validation.feature_sets.behavioral_side_preference_v1 import BehavioralSidePreferenceV1FeatureSet  # noqa: E402
from validation.targets.market_movement import MovementDirectionTarget, MovementMagnitudeTarget  # noqa: E402
from validation.targets.cross_book import CrossBookDisagreementTarget  # noqa: E402
import sqlite3  # noqa: E402


def report_cohort(conn, label, games):
    print(f"\n{'=' * 70}\n{label} \u2014 {len(games)} confirmed regular-season games\n{'=' * 70}")
    if not games:
        print("  No confirmed regular-season games yet.")
        return

    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    sp_fs = BehavioralSidePreferenceV1FeatureSet()
    movement_dir = MovementDirectionTarget()
    movement_mag = MovementMagnitudeTarget()
    cross_book = CrossBookDisagreementTarget()

    for named_cutoff in NAMED_CUTOFFS:
        print(f"\n  --- cutoff: {named_cutoff} ---")
        hi_valid = 0
        sp_valid = 0
        hi_coverage_dist = Counter()
        sp_coverage_dist = Counter()
        movement_dir_valid = 0
        movement_mag_valid = 0
        cross_book_valid = 0
        overlap_hi_movement_mag = 0
        overlap_hi_cross_book = 0
        overlap_sp_movement_dir = 0

        for gid, home, away, commence_time in games:
            resolved_cutoff = resolve_named_cutoff(commence_time, named_cutoff)

            hi_result = hi_fs.get_features(conn, gid, resolved_cutoff)
            sp_result = sp_fs.get_features(conn, gid, resolved_cutoff)
            if hi_result is not None:
                hi_valid += 1
                hi_coverage_dist[hi_result["handle_intensity_coverage_count"]] += 1
            if sp_result is not None:
                sp_valid += 1
                sp_coverage_dist[sp_result["side_preference_coverage_count"]] += 1

            try:
                dir_label = movement_dir.get_label(conn, gid, commence_time)
            except Exception:
                dir_label = None
            try:
                mag_label = movement_mag.get_label(conn, gid, commence_time)
            except Exception:
                mag_label = None
            try:
                cb_label = cross_book.get_label(conn, gid, commence_time, checkpoint_name="last_observed_before_kickoff")
            except Exception:
                cb_label = None

            if dir_label is not None:
                movement_dir_valid += 1
            if mag_label is not None:
                movement_mag_valid += 1
            if cb_label is not None:
                cross_book_valid += 1

            if hi_result is not None and mag_label is not None:
                overlap_hi_movement_mag += 1
            if hi_result is not None and cb_label is not None:
                overlap_hi_cross_book += 1
            if sp_result is not None and dir_label is not None:
                overlap_sp_movement_dir += 1

        print(f"    Handle Intensity: valid={hi_valid}/{len(games)}  coverage_distribution={dict(hi_coverage_dist)}")
        print(f"    Side Preference:  valid={sp_valid}/{len(games)}  coverage_distribution={dict(sp_coverage_dist)}")
        print(f"    MovementDirection evidence available:    {movement_dir_valid}/{len(games)}")
        print(f"    MovementMagnitude evidence available:    {movement_mag_valid}/{len(games)}")
        print(f"    CrossBookDisagreement evidence available: {cross_book_valid}/{len(games)}")
        print(f"    OVERLAP -- Handle Intensity x MovementMagnitude:    {overlap_hi_movement_mag}")
        print(f"    OVERLAP -- Handle Intensity x CrossBookDisagreement: {overlap_hi_cross_book}")
        print(f"    OVERLAP -- Side Preference x MovementDirection:      {overlap_sp_movement_dir}")


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    print(f"{'=' * 70}\nEVIDENCE READINESS MONITOR (read-only, no thresholds asserted)\n{'=' * 70}")

    nfl_cohort = get_nfl_regular_cohort_v2(conn)
    report_cohort(conn, "NFL", nfl_cohort)

    if check_2026_fbs_membership_available():
        cfb_allowlist = load_fbs_allowlist(2026)
        cfb_cohort = get_cfb_regular_cohort_v2(conn, cfb_allowlist)
        report_cohort(conn, "CFB", cfb_cohort)
    else:
        print(f"\n{'=' * 70}\nCFB \u2014 SKIPPED: no verified 2026 FBS membership data exists yet "
              f"(the same hard production prerequisite already established; not resolved here)\n{'=' * 70}")

    conn.close()

    print(f"\n{'=' * 70}\nEnd of report. This script reports counts only -- it does not declare "
          f"whether accumulated evidence is sufficient for any validation claim.\n{'=' * 70}")


if __name__ == "__main__":
    main()
