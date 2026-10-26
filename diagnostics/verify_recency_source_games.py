"""
diagnostics/verify_recency_source_games.py

READ-ONLY. Shows the EXACT prior games feeding a specific team's recency
history as of a given cutoff, using the SAME functions the preseason
mechanics diagnostic actually calls (get_regular_and_postseason_results_only,
build_team_index, get_prior_games) -- not a separate hand-rolled query
that could diverge from real behavior.

Usage:
    python3 diagnostics/verify_recency_source_games.py --team nfl_sea --cutoff 2026-08-16T00:00:00Z
    python3 diagnostics/verify_recency_source_games.py --team nfl_dal --cutoff 2026-08-16T00:00:00Z
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from behavioral.run_preseason_mechanics_diagnostic import get_regular_and_postseason_results_only  # noqa: E402
from features.build_engineered_features_v1 import get_all_games, build_team_index, get_prior_games  # noqa: E402
import sqlite3  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Verify the exact source games feeding a team's recency history")
    parser.add_argument("--team", required=True, help="canonical_team_id, e.g. nfl_sea")
    parser.add_argument("--cutoff", required=True, help="ISO timestamp, e.g. 2026-08-16T00:00:00Z")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    all_games = get_all_games(conn)
    filtered_results = get_regular_and_postseason_results_only(conn)
    team_index = build_team_index(all_games, filtered_results)
    history = get_prior_games(team_index, args.team, args.cutoff)

    print(f"\n{'=' * 70}\nRecency source games for {args.team}, as of cutoff {args.cutoff}\n{'=' * 70}")
    print(f"Total prior games found (post-filter): {len(history)}\n")

    if not history:
        print("No prior regular-season/postseason games found -- this team has zero eligible recency history.")
        conn.close()
        return

    any_preseason = False
    for g in history:
        row = conn.execute(
            "SELECT gr.season_year, gr.season_type, gc.canonical_game_id, gc.home_team_id, gc.away_team_id "
            "FROM game_results gr JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id "
            "WHERE gc.commence_time = ? AND (gc.home_team_id = ? OR gc.away_team_id = ?) "
            "ORDER BY gr.retrieved_at DESC LIMIT 1",
            (g["commence_time"], args.team, args.team),
        ).fetchone()

        if row is None:
            print(f"  WARNING: could not cross-reference {g['commence_time']} back to game_results -- investigate")
            continue

        season_year, season_type, gid, home, away = row
        season_type_label = {1: "PRESEASON", 2: "REGULAR SEASON", 3: "POSTSEASON"}.get(season_type, f"UNKNOWN({season_type})")
        flag = ""
        if season_type == 1:
            any_preseason = True
            flag = " <<< DEFECT: PRESEASON SHOULD NEVER APPEAR HERE"
        print(f"  game_id={gid} {home} vs {away} | {g['commence_time']} | "
              f"season_year={season_year} season_type={season_type} ({season_type_label}){flag}")
        print(f"    result={g['result']} points_for={g['points_for']} points_against={g['points_against']} "
              f"margin={g['margin']}")

    print(f"\n{'=' * 70}")
    if any_preseason:
        print("RESULT: A PRESEASON RESULT WAS FOUND IN THE FILTERED HISTORY -- this is a real defect, "
              "the exclusion fix is not working correctly. Investigate before trusting any coverage numbers.")
    else:
        print("RESULT: No preseason results found in this team's recency history. All source games are "
              "REGULAR SEASON or POSTSEASON -- consistent with the approved cross-season carryover policy. "
              "High coverage here is CORRECT, not a defect.")
    print(f"{'=' * 70}\n")

    conn.close()


if __name__ == "__main__":
    main()
