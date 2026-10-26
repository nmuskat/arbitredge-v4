"""
collectors/run_results_discovery.py

MANUAL, LOCAL-ONLY SCRIPT. Same pattern as run_odds_discovery.py and
run_context_discovery.py -- this sandbox has no network access to ESPN,
so this must be run on your own machine.

What it does:
  1. Runs the results collector for NFL, then CFB, against the REAL ESPN
     scoreboard endpoint for TODAY's date (no backfill -- current/recent
     completed games only, per the approved scope).
  2. This is NOT a dry run -- it writes real game_results rows for any
     TERMINAL (final/postponed/canceled) games found. In-progress games
     produce no rows, by design.
  3. Prints a full summary per sport: games returned, results inserted,
     non-terminal games skipped (expected -- most of a live slate won't
     be final yet unless run after games have concluded), unchanged
     results correctly not duplicated, and unresolved-this-run aliases.
  4. Zero cost -- same free ESPN endpoint as context_collector.py.

Best used the day after a slate of games (e.g. Monday, to catch Sunday's
final NFL scores) so there's something terminal to actually observe.
Running it mid-slate is still safe -- it will just show mostly
non_terminal_skipped for in-progress games, which is correct behavior,
not a bug.

Setup:
  1. pip install -r requirements.txt (no API key needed for this script)
  2. python collectors/run_results_discovery.py

Please paste the full output back for review before any backfill is
discussed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import run_migration, DEFAULT_DB_PATH  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from collectors.results_collector import ResultsCollector  # noqa: E402
from collectors.run_odds_discovery import classify_unresolved_aliases, _now  # noqa: E402
import sqlite3  # noqa: E402


def run_results_for_sport(conn, team_resolver, game_resolver, sport: str) -> None:
    print(f"\n{'=' * 60}\nRESULTS COLLECTION (LIVE, NOT A DRY RUN): {sport.upper()}\n{'=' * 60}")

    since = _now()

    collector = ResultsCollector(
        conn=conn,
        team_resolver=team_resolver,
        game_resolver=game_resolver,
        min_legitimate_poll_interval_minutes=0,  # allow immediate NFL+CFB back-to-back for this one-off script
    )
    summary = collector.collect(sport)

    if summary["skipped_too_soon"]:
        print("Skipped: too soon since last poll (unexpected for a fresh run).")
        return

    print(f"Games returned:            {summary['games_returned']}")
    print(f"Results inserted:          {summary['results_inserted']}")
    print(f"Non-terminal (in-progress/scheduled) games skipped: {summary['non_terminal_skipped']}")
    print(f"Unchanged results correctly not duplicated: {summary['results_unchanged_skipped']}")
    print(f"Other records skipped:     {summary['records_skipped']}")
    print(f"raw_response_id:           {summary['raw_response_id']}")

    this_run, historical = classify_unresolved_aliases(conn, sport, since)
    if this_run:
        print(f"\nUNRESOLVED THIS RUN ({len(this_run)}) from ESPN -- real, current failures:")
        for source, raw_text, reason, count, first_seen, last_seen in this_run:
            print(f"  source={source!r} raw_text={raw_text!r} reason={reason} seen={count}x")
    else:
        print("\nUnresolved ESPN aliases encountered THIS RUN: 0")
    if historical:
        print(f"(plus {len(historical)} historical unresolved-alias audit rows for {sport}, not from this run)")

    sample = conn.execute(
        "SELECT canonical_game_id, game_status, is_final, final_score_home, final_score_away, "
        "winner_home, is_overtime, is_neutral_site "
        "FROM game_results ORDER BY result_id DESC LIMIT 5"
    ).fetchall()
    if sample:
        print("\nSample of results just written (up to 5):")
        for row in sample:
            print(f"  {row}")
    else:
        print("\nNo terminal results found this run (likely mid-slate -- try again after games conclude).")


def main():
    db_path = DEFAULT_DB_PATH
    run_migration(db_path)
    seed(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)

    run_results_for_sport(conn, team_resolver, game_resolver, "nfl")
    run_results_for_sport(conn, team_resolver, game_resolver, "cfb")

    print(f"\n{'=' * 60}\nDone. This WAS a live write to game_results (not a dry run), "
          f"terminal games only. Database: {db_path}\n{'=' * 60}")
    print(
        "\nPlease report back: games returned per sport, results inserted, "
        "non-terminal-skipped count, any unresolved-this-run aliases, and the "
        "sample rows shown above, so we can confirm ESPN's real result-field "
        "shapes match our assumptions before any historical backfill is discussed."
    )

    conn.close()


if __name__ == "__main__":
    main()
