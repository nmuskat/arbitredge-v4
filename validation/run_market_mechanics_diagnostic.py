"""
validation/run_market_mechanics_diagnostic.py

Validates the four implemented market-evidence targets MECHANICALLY
against real preseason market_snapshots. This is explicitly NOT a
behavioral validation run -- it doesn't compare against Handle Intensity,
Side Preference, or any pressure estimate (none exist yet). It only
confirms the target computations themselves produce correct, sane,
non-fabricated output against real multi-book data.

Preseason data used here must NEVER be pooled with regular-season
behavioral validation/training -- this script only ever reports diagnostic
output, never writes anything to validation_experiments or any other
table. It is read-only.

Usage:
    python3 validation/run_market_mechanics_diagnostic.py --start-date 2026-08-13 --end-date 2026-08-16
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from validation.targets.market_movement import (  # noqa: E402
    MovementDirectionTarget, MovementMagnitudeTarget, crosses_key_number, _select_book,
)
from validation.targets.market_resistance import ReversalTarget  # noqa: E402
from validation.targets.cross_book import CrossBookDisagreementTarget  # noqa: E402
from reporting.checkpoint_reconstruction import (  # noqa: E402
    get_books_for_game, get_book_snapshots, reconstruct_checkpoints_for_book, ALL_CHECKPOINT_NAMES,
)
import sqlite3  # noqa: E402

DIAGNOSTIC_BANNER = (
    "DIAGNOSTIC / MARKET-MECHANICS VALIDATION ONLY \u2014 NOT EVIDENCE OF PUBLIC PRESSURE OR BOOK NEED"
)


def print_banner():
    line = "!" * len(DIAGNOSTIC_BANNER)
    print(f"\n{line}\n{DIAGNOSTIC_BANNER}\n{line}\n")


def get_games_with_market_data(conn, start_date, end_date):
    start = f"{start_date}T00:00:00Z"
    end = f"{end_date}T23:59:59Z"
    return conn.execute(
        "SELECT DISTINCT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc JOIN market_snapshots ms ON gc.canonical_game_id = ms.canonical_game_id "
        "WHERE gc.commence_time >= ? AND gc.commence_time <= ? "
        "ORDER BY gc.commence_time",
        (start, end),
    ).fetchall()


def diagnose_game(conn, gid, home, away, commence_time):
    print(f"\n--- Game {gid}: {home} vs {away} (kickoff {commence_time}) ---")
    books = get_books_for_game(conn, gid)
    print(f"Books observed: {books}")

    default_book = _select_book(conn, gid)
    print(f"Default book (most complete path): {default_book}")

    direction_t = MovementDirectionTarget()
    magnitude_t = MovementMagnitudeTarget()
    reversal_t = ReversalTarget()
    cross_book_t = CrossBookDisagreementTarget()

    for book in books:
        direction = direction_t.get_label(conn, gid, commence_time, book=book)
        magnitude = magnitude_t.get_label(conn, gid, commence_time, book=book)
        reversal = reversal_t.get_label(conn, gid, commence_time, book=book)

        snapshots = get_book_snapshots(conn, gid, book)
        checkpoints = reconstruct_checkpoints_for_book(gid, commence_time, book, snapshots)
        earliest = checkpoints["earliest_observed"]
        last = checkpoints["last_observed_before_kickoff"]
        key_number_crossed = None
        if earliest and last and earliest["spread_home"] is not None and last["spread_home"] is not None:
            key_number_crossed = crosses_key_number(earliest["spread_home"], last["spread_home"])

        print(f"  [{book}] direction={direction} magnitude={magnitude} reversal={reversal} "
              f"key_number_crossed(metadata only, no weight)={key_number_crossed}")
        for name in ALL_CHECKPOINT_NAMES:
            snap = checkpoints[name]
            if snap:
                print(f"      {name}: spread_home={snap['spread_home']} price_home={snap['spread_price_home']} "
                      f"@ {snap['observed_at']}")
            else:
                print(f"      {name}: NULL")

    print("\n  Cross-book disagreement (descriptive market-state measurement, not a sharp signal):")
    for checkpoint_name in ALL_CHECKPOINT_NAMES:
        disagreement = cross_book_t.get_label(conn, gid, commence_time, checkpoint_name=checkpoint_name)
        print(f"    {checkpoint_name}: range={disagreement}")


def main():
    parser = argparse.ArgumentParser(description="Market-evidence target mechanics diagnostic")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD, inclusive, UTC")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD, inclusive, UTC")
    args = parser.parse_args()

    print_banner()
    print("This run validates ONLY that MovementDirectionTarget, MovementMagnitudeTarget, "
          "ReversalTarget, and CrossBookDisagreementTarget compute CORRECT numbers against "
          "real observed market data. It says nothing about Handle Intensity, Side Preference, "
          "Public Pressure, or Book Need -- none of those exist yet.")
    print("ResistanceDivergenceTarget remains explicitly dormant and is not exercised here.")
    print("Preseason data used in this run is NEVER pooled with regular-season behavioral validation.")

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    games = get_games_with_market_data(conn, args.start_date, args.end_date)
    print(f"\nGames with real market_snapshots in range {args.start_date} to {args.end_date}: {len(games)}")

    for gid, home, away, commence_time in games:
        diagnose_game(conn, gid, home, away, commence_time)

    conn.close()

    print_banner()


if __name__ == "__main__":
    main()
