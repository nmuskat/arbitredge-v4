"""
reporting/saturday_trial_report.py

Assembles an EXPERIMENTAL, purely descriptive report from already-built,
already-tested components. Writes nothing to any raw or feature table --
this is read-only reporting over data collected elsewhere.

V1 schedule/recency features are computed FRESH in memory for this report
(reusing the already-tested pure functions in features/compute_v1.py),
NOT read from or written to the persisted engineered_features table. This
is a deliberate design choice: engineered_features's cohort selection (in
features/build_engineered_features_v1.py) currently selects by date range
only, with no season_type filter -- meaning if it were ever re-run for
2026, preseason games falling in the Aug-Feb window could be swept into
the same 'v1' feature rows as regular-season games. That's a real,
adjacent gap worth fixing before the regular season starts, but computing
fresh here avoids depending on it at all for this report, so this report
carries zero risk of preseason data ever entering the persisted cohort.

season_type labeling comes directly from game_results.season_type
(1=preseason, 2=regular, 3=postseason) once a result exists; before that,
the report says so explicitly rather than guessing.

No public ticket %, handle %, Handle Intensity %, Side Preference %, Book
Need probability, or confidence score is computed or displayed anywhere
in this module.

Usage:
    python3 reporting/saturday_trial_report.py --start-date 2026-08-08 --end-date 2026-08-08
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from reporting.checkpoint_reconstruction import (  # noqa: E402
    get_books_for_game, get_book_snapshots, reconstruct_checkpoints_for_book,
    compute_movement, compute_cross_book_disagreement, ALL_CHECKPOINT_NAMES,
)
from features.compute_v1 import compute_team_features, compute_slate_features  # noqa: E402
from features.build_engineered_features_v1 import (  # noqa: E402
    get_all_games, get_latest_results_by_canonical_game, build_team_index, get_prior_games,
)
import sqlite3  # noqa: E402

EXPERIMENTAL_BANNER = "EXPERIMENTAL -- NOT A VALIDATED BOOK NEED SIGNAL -- NOT FOR MODEL PERFORMANCE CLAIMS"

SEASON_TYPE_LABELS = {1: "NFL PRESEASON", 2: "NFL REGULAR SEASON", 3: "NFL POSTSEASON"}


def get_season_type_and_result(conn, canonical_game_id):
    return conn.execute(
        "SELECT final_score_home, final_score_away, winner_home, season_year, season_type "
        "FROM game_results WHERE canonical_game_id = ? ORDER BY retrieved_at DESC LIMIT 1",
        (canonical_game_id,),
    ).fetchone()


def build_game_report(conn, canonical_game_id, home_id, away_id, commence_time, all_games, team_index):
    lines = [f"## Game {canonical_game_id}: {home_id} vs {away_id}", f"Kickoff: {commence_time}"]

    result_row = get_season_type_and_result(conn, canonical_game_id)
    target_season_year = None
    if result_row:
        home_score, away_score, winner_home, season_year, season_type = result_row
        target_season_year = season_year
        label = SEASON_TYPE_LABELS.get(season_type, f"UNKNOWN season_type={season_type}")
        lines.append(f"**Season type: {label}**")
        lines.append(f"Final result: {home_id} {home_score} - {away_score} {away_id}")
    else:
        lines.append("**Season type: UNCONFIRMED (result not yet recorded)**")
        lines.append("Final result: not yet available")

    books = get_books_for_game(conn, canonical_game_id)
    if not books:
        lines.append("\nNo market_snapshots observed for this game.")
    for book in books:
        snapshots = get_book_snapshots(conn, canonical_game_id, book)
        checkpoints = reconstruct_checkpoints_for_book(canonical_game_id, commence_time, book, snapshots)
        lines.append(f"\n### Book: {book}")
        for name in ALL_CHECKPOINT_NAMES:
            snap = checkpoints[name]
            if snap:
                lines.append(f"- {name}: spread_home={snap['spread_home']} "
                              f"price_home={snap['spread_price_home']} @ {snap['observed_at']}")
            else:
                lines.append(f"- {name}: NULL (no observation within documented tolerance)")

        movements = compute_movement(checkpoints)
        if movements:
            lines.append("\nMovement:")
            for m in movements:
                if m["price_only_move"]:
                    tag = "PRICE-ONLY MOVE"
                elif m["spread_home_delta"] not in (None, 0):
                    tag = "SPREAD-NUMBER MOVE"
                else:
                    tag = "no change"
                lines.append(f"  {m['from']} -> {m['to']}: spread_delta={m['spread_home_delta']} "
                              f"price_delta={m['spread_price_home_delta']} [{tag}]")

    disagreement = compute_cross_book_disagreement(conn, canonical_game_id, commence_time)
    lines.append("\n### Cross-book disagreement (raw values, no consensus computed)")
    for name, info in disagreement.items():
        if info["range"] is not None:
            lines.append(f"- {name}: range={info['range']} across {info['per_book']}")
        else:
            lines.append(f"- {name}: insufficient multi-book data ({info['per_book']})")

    home_history = get_prior_games(team_index, home_id, commence_time)
    away_history = get_prior_games(team_index, away_id, commence_time)
    home_features = compute_team_features(home_history, commence_time, target_season_year)
    away_features = compute_team_features(away_history, commence_time, target_season_year)
    slate_features = compute_slate_features(all_games, canonical_game_id, "nfl", commence_time)

    lines.append("\n### V1 schedule/recency features (computed fresh for this report only -- "
                  "NOT written to engineered_features)")
    lines.append(f"Home: games_played_before_this={home_features['games_played_before_this']} "
                 f"wins_last_3={home_features['wins_last_3']} last_game_result={home_features['last_game_result']}")
    lines.append(f"Away: games_played_before_this={away_features['games_played_before_this']} "
                 f"wins_last_3={away_features['wins_last_3']} last_game_result={away_features['last_game_result']}")
    lines.append(f"Slate: standalone_same_sport={slate_features['is_standalone_same_sport']} "
                 f"competing_games_all_sports={slate_features['competing_games_same_window_all_sports']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ArbitrEdge Needs -- experimental trial report")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD, inclusive, UTC")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD, inclusive, UTC")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    start = f"{args.start_date}T00:00:00Z"
    end = f"{args.end_date}T23:59:59Z"

    games = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = 'nfl' AND commence_time >= ? AND commence_time <= ? ORDER BY commence_time",
        (start, end),
    ).fetchall()

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)

    report_lines = [
        "# ArbitrEdge Needs -- Trial Report",
        f"\n**{EXPERIMENTAL_BANNER}**\n",
        f"Date range: {args.start_date} to {args.end_date} (UTC). NFL games found: {len(games)}.",
        "\nPreseason and regular-season games are labeled separately below and must never be pooled "
        "for behavioral validation or training -- that remains a separate future decision.\n",
        "---",
    ]

    for gid, home, away, commence_time in games:
        report_lines.append(build_game_report(conn, gid, home, away, commence_time, all_games, team_index))
        report_lines.append("\n---")

    report_lines.append(f"\n**{EXPERIMENTAL_BANNER}**")
    report_lines.append(
        "\nThis report contains no public ticket %, handle %, calibrated Handle Intensity %, "
        "calibrated Side Preference %, Book Need probability, or confidence score. None of those "
        "outputs have earned calibration through the validation framework yet. Every number above "
        "is a directly observed or deterministically derived raw fact."
    )

    full_report = "\n".join(report_lines)
    print(full_report)

    output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"trial_report_{args.start_date}_to_{args.end_date}.md"
    output_path.write_text(full_report)
    print(f"\nSaved to {output_path}")

    conn.close()


if __name__ == "__main__":
    main()
