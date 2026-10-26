"""
collectors/verify_2025_backfill_reconciliation.py

MANUAL, LOCAL-ONLY VERIFICATION SCRIPT. Run once, after the real 2025
backfill, to confirm the reconciliation and produce the two distinct views
requested:

  DATABASE CONTENT: every historical game_results observation ever stored,
  unfiltered by season or subdivision -- this is raw, append-only history
  and is never modified or deleted by this script.

  2025 APPROVED RESEARCH COHORT: the season-and-subdivision-scoped subset
  that the project actually treats as "the 2025 backfill" -- NFL 2025
  regular season, CFB 2025 regular season FBS-vs-FBS only. Built by
  filtering on BOTH the season-specific FBS allowlist AND each canonical
  game's actual commence_time falling inside the 2025 season window (not
  just allowlist membership alone, which would incorrectly include a
  future game between two teams who also happened to be 2025 FBS members
  if this query is ever reused without a season filter later).

This script writes nothing. It only reads and reports.

Usage:
    python collectors/verify_2025_backfill_reconciliation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
import sqlite3  # noqa: E402

PRE_BACKFILL_ROW_COUNT = 66  # 16 NFL + 50 CFB, from the historical-validation runs
NFL_SEASON_START = "2025-08-01T00:00:00Z"
NFL_SEASON_END = "2026-02-01T00:00:00Z"
CFB_SEASON_START = "2025-08-01T00:00:00Z"
CFB_SEASON_END = "2026-02-01T00:00:00Z"


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")
    fbs_allowlist = load_fbs_allowlist(2025)

    print(f"{'=' * 70}\nDATABASE CONTENT (all historical game_results, unfiltered)\n{'=' * 70}")
    total_rows = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    print(f"Total game_results rows (all-time, all sports, all subdivisions): {total_rows}")

    all_cfb = conn.execute(
        "SELECT gr.result_id, gr.canonical_game_id, gc.home_team_id, gc.away_team_id "
        "FROM game_results gr JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id "
        "WHERE gc.sport = 'cfb'"
    ).fetchall()
    violations = [r for r in all_cfb if r[2] not in fbs_allowlist or r[3] not in fbs_allowlist]
    print(f"Total CFB game_results rows (all-time, unfiltered by subdivision): {len(all_cfb)}")
    print(f"Of those, rows with at least one non-2025-FBS side: {len(violations)}")

    print(f"\n{'=' * 70}\nITEM 1: do all violations predate the real backfill?\n{'=' * 70}")
    violation_result_ids = sorted(r[0] for r in violations)
    pre_backfill_violations = [rid for rid in violation_result_ids if rid <= PRE_BACKFILL_ROW_COUNT]
    post_backfill_violations = [rid for rid in violation_result_ids if rid > PRE_BACKFILL_ROW_COUNT]
    print(f"Violation result_ids: {violation_result_ids}")
    print(f"Violations with result_id <= {PRE_BACKFILL_ROW_COUNT} (pre-backfill, from historical validation): "
          f"{len(pre_backfill_violations)}")
    print(f"Violations with result_id > {PRE_BACKFILL_ROW_COUNT} (would mean the REAL backfill wrote one -- "
          f"should be zero): {len(post_backfill_violations)}")
    if post_backfill_violations:
        print(f"  UNEXPECTED, INVESTIGATE: {post_backfill_violations}")
        item1_result = "FAIL"
    else:
        item1_result = "PASS" if len(pre_backfill_violations) == len(violations) else "INCONCLUSIVE"
    print(f"Item 1: {item1_result}")

    print(f"\n{'=' * 70}\nITEM 2: did the real backfill write zero non-FBS-vs-FBS CFB rows?\n{'=' * 70}")
    print(f"Item 2: {'PASS' if not post_backfill_violations else 'FAIL'} "
          f"(post-backfill violation count = {len(post_backfill_violations)}; "
          f"also true by construction -- write_cfb_result_if_new_or_changed() in run_results_backfill.py "
          f"is only ever called from the home_status=='fbs' and away_status=='fbs' branch)")

    print(f"\n{'=' * 70}\n2025 APPROVED RESEARCH COHORT (season + subdivision scoped)\n{'=' * 70}")

    nfl_cohort = conn.execute(
        "SELECT DISTINCT gr.canonical_game_id FROM game_results gr "
        "JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id "
        "WHERE gc.sport = 'nfl' AND gc.commence_time >= ? AND gc.commence_time < ?",
        (NFL_SEASON_START, NFL_SEASON_END),
    ).fetchall()
    print(f"NFL 2025 regular season, unique games: {len(nfl_cohort)} (expected 272)")

    cfb_cohort = conn.execute(
        "SELECT DISTINCT gr.canonical_game_id, gc.home_team_id, gc.away_team_id "
        "FROM game_results gr JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id "
        "WHERE gc.sport = 'cfb' AND gc.commence_time >= ? AND gc.commence_time < ?",
        (CFB_SEASON_START, CFB_SEASON_END),
    ).fetchall()
    cfb_cohort_fbs_only = [r for r in cfb_cohort if r[1] in fbs_allowlist and r[2] in fbs_allowlist]
    print(f"CFB 2025 regular season, all subdivisions, unique games: {len(cfb_cohort)}")
    print(f"CFB 2025 regular season, FBS-vs-FBS ONLY, unique games: {len(cfb_cohort_fbs_only)} (expected 761)")

    item3_result = "PASS" if len(cfb_cohort_fbs_only) == 761 else "FAIL"
    item4_result = "PASS" if len(nfl_cohort) == 272 else "FAIL"
    print(f"\nItem 3 (CFB cohort == 761): {item3_result}")
    print(f"Item 4 (NFL cohort == 272): {item4_result}")

    print(f"\n{'=' * 70}\nITEM 5: remaining score/provenance/identity defects\n{'=' * 70}")

    missing_scores = conn.execute(
        "SELECT result_id FROM game_results WHERE is_final = 1 "
        "AND (final_score_home IS NULL OR final_score_away IS NULL)"
    ).fetchall()
    print(f"Final games missing a score (all-time, unfiltered): {len(missing_scores)}")

    orphan_links = conn.execute(
        "SELECT l.link_id FROM game_results_raw_response_links l "
        "LEFT JOIN game_results r ON l.result_id = r.result_id WHERE r.result_id IS NULL"
    ).fetchall()
    unlinked_results = conn.execute(
        "SELECT r.result_id FROM game_results r "
        "LEFT JOIN game_results_raw_response_links l ON r.result_id = l.result_id WHERE l.link_id IS NULL"
    ).fetchall()
    print(f"Orphan provenance links: {len(orphan_links)}")
    print(f"Results with zero provenance links: {len(unlinked_results)}")

    dup_check = conn.execute(
        "SELECT canonical_game_id, COUNT(*) as n FROM game_results GROUP BY canonical_game_id HAVING n > 1"
    ).fetchall()
    print(f"Canonical games with more than one game_results row (expected: legitimate corrections only, "
          f"if any): {len(dup_check)}")

    item5_defects = len(missing_scores) + len(orphan_links) + len(unlinked_results)
    item5_result = "PASS -- no genuine defects found" if item5_defects == 0 else f"FAIL -- {item5_defects} defect(s)"
    print(f"\nItem 5: {item5_result}")

    print(f"\n{'=' * 70}\nOVERALL\n{'=' * 70}")
    all_pass = (item1_result == "PASS" and item3_result == "PASS"
                and item4_result == "PASS" and item5_defects == 0)
    print("2025 RESULTS BACKFILL: " + ("COMPLETE -- reconciliation and all checks pass"
                                        if all_pass else "NOT YET CONFIRMED -- see failing item(s) above"))

    conn.close()


if __name__ == "__main__":
    main()
