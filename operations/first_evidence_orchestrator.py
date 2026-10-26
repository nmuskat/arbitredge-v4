"""
operations/first_evidence_orchestrator.py

Thin orchestration only -- every piece below is already built and tested
elsewhere; this script calls them in sequence and reports what exists.
NO new computation, NO threshold judgment, NO behavioral claim.

Sequence:
  1. collection health (operations/collection_health.py)
  2. V2 batch persistence for confirmed regular-season games
     (features/build_engineered_features_v2_batch.py)
  3. evidence-readiness counts (diagnostics/evidence_readiness_monitor.py)
  4. market-mechanics evidence availability (validation/targets/
     market_movement.py, cross_book.py -- confirms the real, implemented
     evidence-family targets are reachable against real data)
  5. behavioral FeatureSet/harness readiness (validation/feature_sets/
     behavioral_handle_intensity_v1.py, behavioral_side_preference_v1.py --
     confirms the wrappers can resolve real data, without producing or
     interpreting any score)

This script never calls persist_features_v2 with anything other than its
existing, tested defaults, never touches Behavioral V1, and never states
whether accumulated evidence is "enough" for anything.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from operations.collection_health import generate_report, format_report  # noqa: E402
from features.build_engineered_features_v2_batch import run_batch  # noqa: E402
from diagnostics.evidence_readiness_monitor import (  # noqa: E402
    get_nfl_regular_cohort_v2, get_cfb_regular_cohort_v2, check_2026_fbs_membership_available,
)
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from validation.feature_sets.behavioral_handle_intensity_v1 import BehavioralHandleIntensityV1FeatureSet  # noqa: E402
from validation.feature_sets.behavioral_side_preference_v1 import BehavioralSidePreferenceV1FeatureSet  # noqa: E402
import sqlite3  # noqa: E402

BANNER = "FIRST-EVIDENCE ORCHESTRATOR \u2014 REPORTS ACCUMULATED EVIDENCE ONLY, NO READINESS CLAIM, NO BEHAVIORAL INTERPRETATION"


def print_banner():
    line = "!" * len(BANNER)
    print(f"\n{line}\n{BANNER}\n{line}\n")


def step_collection_health(conn):
    print(f"\n{'=' * 70}\nSTEP 1: COLLECTION HEALTH\n{'=' * 70}")
    report = generate_report(conn)
    print(format_report(report))
    return report


def step_v2_batch_persistence(conn):
    print(f"\n{'=' * 70}\nSTEP 2: V2 BATCH PERSISTENCE (confirmed regular-season games only)\n{'=' * 70}")
    result = run_batch(conn, feature_set_version="v2")
    return result


def step_evidence_readiness(conn):
    print(f"\n{'=' * 70}\nSTEP 3: EVIDENCE-READINESS COUNTS\n{'=' * 70}")
    nfl_cohort = get_nfl_regular_cohort_v2(conn)
    print(f"NFL confirmed regular-season games: {len(nfl_cohort)}")

    cfb_cohort = []
    if check_2026_fbs_membership_available():
        cfb_allowlist = load_fbs_allowlist(2026)
        cfb_cohort = get_cfb_regular_cohort_v2(conn, cfb_allowlist)
        print(f"CFB confirmed regular-season games: {len(cfb_cohort)}")
    else:
        print("CFB: 2026 FBS membership unavailable (should not occur -- already verified this milestone)")

    return {"nfl_games": len(nfl_cohort), "cfb_games": len(cfb_cohort)}


def step_market_mechanics_availability(conn, nfl_cohort, cfb_cohort):
    print(f"\n{'=' * 70}\nSTEP 4: MARKET-MECHANICS EVIDENCE AVAILABILITY\n{'=' * 70}")
    from validation.targets.market_movement import MovementDirectionTarget, MovementMagnitudeTarget
    from validation.targets.cross_book import CrossBookDisagreementTarget

    movement_dir = MovementDirectionTarget()
    movement_mag = MovementMagnitudeTarget()
    cross_book = CrossBookDisagreementTarget()

    all_games = list(nfl_cohort) + list(cfb_cohort)
    reachable = {"MovementDirection": 0, "MovementMagnitude": 0, "CrossBookDisagreement": 0}
    for gid, home, away, commence_time in all_games:
        try:
            if movement_dir.get_label(conn, gid, commence_time) is not None:
                reachable["MovementDirection"] += 1
        except Exception:
            pass
        try:
            if movement_mag.get_label(conn, gid, commence_time) is not None:
                reachable["MovementMagnitude"] += 1
        except Exception:
            pass
        try:
            if cross_book.get_label(conn, gid, commence_time, checkpoint_name="last_observed_before_kickoff") is not None:
                reachable["CrossBookDisagreement"] += 1
        except Exception:
            pass

    for target_name, count in reachable.items():
        print(f"  {target_name}: {count}/{len(all_games)} games have real, computable evidence")
    return reachable


def step_behavioral_readiness(conn, nfl_cohort, cfb_cohort):
    print(f"\n{'=' * 70}\nSTEP 5: BEHAVIORAL FEATURESET/HARNESS READINESS "
          f"(resolution check only -- no scores produced or interpreted)\n{'=' * 70}")
    hi_fs = BehavioralHandleIntensityV1FeatureSet()
    sp_fs = BehavioralSidePreferenceV1FeatureSet()

    all_games = list(nfl_cohort) + list(cfb_cohort)
    hi_resolvable = 0
    sp_resolvable = 0
    for gid, home, away, commence_time in all_games:
        if hi_fs.get_features(conn, gid, commence_time) is not None:
            hi_resolvable += 1
        if sp_fs.get_features(conn, gid, commence_time) is not None:
            sp_resolvable += 1

    print(f"  HandleIntensityV1 FeatureSet resolvable: {hi_resolvable}/{len(all_games)} games")
    print(f"  SidePreferenceV1 FeatureSet resolvable: {sp_resolvable}/{len(all_games)} games")
    return {"handle_intensity_resolvable": hi_resolvable, "side_preference_resolvable": sp_resolvable}


def main():
    print_banner()
    print("This script orchestrates only already-tested pieces. It reports what evidence "
          "has accumulated and what can legitimately be exercised right now. It makes NO "
          "claim about whether that evidence is sufficient for any validation conclusion, "
          "and produces NO behavioral score, Public Pressure value, or recommendation.")

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    step_collection_health(conn)
    step_v2_batch_persistence(conn)
    readiness = step_evidence_readiness(conn)

    nfl_cohort = get_nfl_regular_cohort_v2(conn)
    cfb_cohort = []
    if check_2026_fbs_membership_available():
        cfb_cohort = get_cfb_regular_cohort_v2(conn, load_fbs_allowlist(2026))

    step_market_mechanics_availability(conn, nfl_cohort, cfb_cohort)
    step_behavioral_readiness(conn, nfl_cohort, cfb_cohort)

    conn.close()

    print_banner()
    print(f"Summary: {readiness['nfl_games']} NFL + {readiness['cfb_games']} CFB confirmed regular-season "
          f"games currently exist. This is a factual count, not a readiness declaration.")
    print_banner()


if __name__ == "__main__":
    main()
