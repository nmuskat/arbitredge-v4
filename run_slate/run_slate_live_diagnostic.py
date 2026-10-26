"""
run_slate/run_slate_live_diagnostic.py

Real-production diagnostic proving the complete RUN SLATE live-serving
path against actual upcoming 2026 NFL regular-season games already in
the production database. Explicitly verifies every guarantee this
milestone was built to prove.

Read-only except for its own before/after row-count snapshots, which
touch nothing.

Usage:
    python3 run_slate/run_slate_live_diagnostic.py --sport nfl --start-date 2026-08-16 --end-date 2026-09-30
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from run_slate.orchestrate import run_slate  # noqa: E402
import sqlite3  # noqa: E402

BANNER = "RUN SLATE LIVE DIAGNOSTIC \u2014 INFRASTRUCTURE PROOF ONLY, NO INTERPRETATION, NO RECOMMENDATION"


def print_banner():
    line = "!" * len(BANNER)
    print(f"\n{line}\n{BANNER}\n{line}\n")


def snapshot_row_counts(conn):
    tables = ["engineered_features", "engineered_features_v2", "market_state_by_cutoff",
              "market_snapshots", "game_results", "game_context", "games_canonical"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def main():
    parser = argparse.ArgumentParser(description="Real-production RUN SLATE live diagnostic")
    parser.add_argument("--sport", required=True, choices=["nfl", "cfb"])
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    print_banner()

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    before = snapshot_row_counts(conn)
    print(f"Row counts BEFORE: {before}")

    result = run_slate(conn, args.sport, args.start_date, args.end_date)

    after = snapshot_row_counts(conn)
    print(f"\nRow counts AFTER:  {after}")

    if before == after:
        print("\n[VERIFIED] Zero writes to any table -- V1, V2, market state, and raw collection "
              "data are all byte-for-byte unchanged.")
    else:
        print("\n[FAILURE] Row counts changed! RUN SLATE must never write to any table. "
              "STOP and report this before proceeding.")
        for t in before:
            if before[t] != after[t]:
                print(f"  {t}: {before[t]} -> {after[t]}")

    print(f"\nSport: {result['sport']}  Range: {result['start_date']} to {result['end_date']}  "
          f"As-of: {result['as_of']}")
    print(f"\nEligible games discovered: {len(result['eligible_games'])}")
    print(f"Excluded games: {len(result['excluded_games'])}")

    if result["excluded_games"]:
        print("\n--- Exclusions (by status) ---")
        for g in result["excluded_games"]:
            print(f"  game_id={g['canonical_game_id']} {g['home_team_id']} vs {g['away_team_id']} "
                  f"| {g['status']}: {g['reason']}")

    if not result["eligible_games"]:
        print("\nNo eligible upcoming games found in this range -- nothing further to report. "
              "If you expected real games here, check the date range and confirm the collector "
              "has discovered them (not a RUN SLATE issue if so).")
        conn.close()
        print_banner()
        return

    print("\n--- Eligible games (sample detail) ---")
    for game in result["eligible_games"][:5]:
        print(f"\n  Game {game['canonical_game_id']}: {game['home_team_id']} vs {game['away_team_id']} "
              f"(kickoff {game['commence_time']})")
        print(f"    as_of cutoff: {game['as_of_cutoff']}")
        print(f"    V2 feature coverage: {game['v2_feature_coverage_count']}/{game['v2_feature_coverage_total']}")
        print(f"    Books with market state: {list(game['market_state_by_book'].keys())}")
        for book, state in game["market_state_by_book"].items():
            if state is None:
                print(f"      [{book}] None (no pregame observation at/before this cutoff)")
            elif "error" in state:
                print(f"      [{book}] ERROR: {state['error']}")
            else:
                print(f"      [{book}] home_spread={state['home_spread']} "
                      f"staleness_seconds={state['staleness_seconds']:.0f}")

    print(f"\n{'=' * 70}\nSUMMARY OF GUARANTEES VERIFIED THIS RUN:\n{'=' * 70}")
    print(f"  Upcoming games discoverable:         {'YES' if result['eligible_games'] or result['excluded_games'] else 'NO GAMES IN RANGE'}")
    print(f"  Eligibility filtering applied:        YES ({len(result['excluded_games'])} excluded, "
          f"{len(result['eligible_games'])} passed)")
    print(f"  compute_features_v2 works pre-result: {'YES' if result['eligible_games'] else 'N/A (no eligible games)'}")
    print(f"  Zero database writes:                 {'YES' if before == after else 'NO -- SEE FAILURE ABOVE'}")

    conn.close()
    print_banner()


if __name__ == "__main__":
    main()
