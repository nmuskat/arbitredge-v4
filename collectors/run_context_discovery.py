"""
collectors/run_context_discovery.py

MANUAL, LOCAL-ONLY SCRIPT. Same pattern as run_odds_discovery.py -- this
sandbox has no network access to ESPN either, so this must be run on your
own machine.

What it does:
  1. Runs the context collector for NFL, then CFB, against the REAL ESPN
     public scoreboard endpoints (and, for CFB, the rankings endpoint).
  2. This is NOT a dry run -- it DOES write real game_context rows (no
     dry-run mode is implemented for this collector, since ESPN is free;
     there's no credit-cost reason to withhold writes the way there was
     for the odds collector). It does NOT touch market_snapshots at all.
  3. Prints a full summary per sport: games returned, rows inserted vs.
     skipped, whether rankings were available, unresolved aliases found
     THIS RUN (vs. historical), and raw_response_id linkage.
  4. Zero the-odds-api.com credits are used by this script.

Setup:
  1. pip install -r requirements.txt  (no API key needed for this script)
  2. python collectors/run_context_discovery.py

Please paste the full output back for review before we proceed to
scheduling or the next collector.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import run_migration, DEFAULT_DB_PATH  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from collectors.context_collector import ContextCollector  # noqa: E402
from collectors.run_odds_discovery import classify_unresolved_aliases, _now  # noqa: E402
import sqlite3  # noqa: E402


def run_context_for_sport(conn, team_resolver, game_resolver, sport: str) -> None:
    print(f"\n{'=' * 60}\nCONTEXT COLLECTION (LIVE, NOT A DRY RUN): {sport.upper()}\n{'=' * 60}")

    since = _now()

    collector = ContextCollector(
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
    print(f"Rows inserted:             {summary['records_inserted']}")
    print(f"Records skipped:           {summary['records_skipped']}")
    print(f"Scoreboard raw_response_id:{summary['scoreboard_raw_response_id']}")
    if sport == "cfb":
        print(f"Rankings available:       {summary['rankings_available']}")
        print(f"Rankings raw_response_id: {summary['rankings_raw_response_id']}")

    this_run, historical = classify_unresolved_aliases(conn, sport, since)
    if this_run:
        print(f"\nUNRESOLVED THIS RUN ({len(this_run)}) from ESPN -- real, current failures:")
        for source, raw_text, reason, count, first_seen, last_seen in this_run:
            print(f"  source={source!r} raw_text={raw_text!r} reason={reason} seen={count}x")
    else:
        print("\nUnresolved ESPN aliases encountered THIS RUN: 0")
    if historical:
        print(f"(plus {len(historical)} historical unresolved-alias audit rows for {sport}, "
              f"not from this run, preserved as history)")

    # Show a few sample rows actually written
    sample = conn.execute(
        "SELECT canonical_game_id, ranking_home, ranking_away, record_home, record_away, "
        "tv_network, is_primetime, kickoff_window FROM game_context "
        "ORDER BY context_id DESC LIMIT 5"
    ).fetchall()
    if sample:
        print("\nSample of rows just written (up to 5):")
        for row in sample:
            print(f"  {row}")


def main():
    db_path = DEFAULT_DB_PATH
    run_migration(db_path)
    seed(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)

    run_context_for_sport(conn, team_resolver, game_resolver, "nfl")
    run_context_for_sport(conn, team_resolver, game_resolver, "cfb")

    print(f"\n{'=' * 60}\nDone. This WAS a live write to game_context (not a dry run). "
          f"market_snapshots was not touched. Database: {db_path}\n{'=' * 60}")
    print(
        "\nPlease report back: games returned per sport, rows inserted/skipped, "
        "whether CFB rankings were available, any unresolved-this-run aliases, "
        "and a couple of sample rows, so we can confirm ESPN's real field shapes "
        "match our assumptions."
    )

    conn.close()


if __name__ == "__main__":
    main()
