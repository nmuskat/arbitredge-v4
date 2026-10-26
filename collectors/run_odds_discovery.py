"""
collectors/run_odds_discovery.py

MANUAL, LOCAL-ONLY SCRIPT. This sandbox has no network access to
the-odds-api.com, so this cannot be run here -- it's meant for you to run
on your own machine with your own API key.

What it does:
  1. Runs the odds collector in DRY-RUN mode for NFL, then CFB, against the
     REAL the-odds-api.com current-odds endpoint (spreads, US region only).
     Dry run means: makes the real request, resolves identity, validates
     everything, but writes ZERO market_snapshots rows. This doubles as
     the "discovery request" -- any team name we can't resolve gets logged
     to unresolved_aliases, which this script prints at the end.
  2. Prints a full summary: games returned, bookmakers seen, records that
     would have been inserted/skipped, API credits used/remaining, and any
     unresolved aliases found.
  3. Does NOT write any market data (dry run) and does NOT start a
     scheduler or repeated polling. This is a single manual invocation.

Setup:
  1. pip install -r requirements.txt
  2. export THE_ODDS_API_KEY="your-real-key-here"
     (get one at https://the-odds-api.com -- verify the hyphenated domain)
  3. python collectors/run_odds_discovery.py

Please paste the full output back for review before we proceed to
scheduling repeated polling.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import run_migration, DEFAULT_DB_PATH  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from collectors.odds_collector import OddsCollector  # noqa: E402
from collectors.config import get_env
import sqlite3  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def classify_unresolved_aliases(conn, sport: str, since_timestamp: str):
    """
    unresolved_aliases is a persistent audit table: a row is only ever
    written when a resolution FAILS, and a later successful resolution
    never touches or clears that row (by design -- see identity/team_resolver.py).
    That means a naive "SELECT ... WHERE sport = ?" mixes together:
      - aliases that failed to resolve DURING THIS RUN (real, actionable)
      - stale rows from a PRIOR run, before a seed update fixed them, that
        are simply preserved history and are NOT evidence of a current
        resolution failure

    This splits the two using last_seen_at vs. a timestamp captured right
    before this run's collection started: TeamResolver.resolve() updates
    last_seen_at on every occurrence (even repeat failures), so any row
    NOT touched during this run keeps its old last_seen_at and is
    correctly classified as historical.

    Returns (this_run_rows, historical_rows), each a list of
    (source, raw_text, reason, occurrence_count, first_seen_at, last_seen_at).
    """
    rows = conn.execute(
        "SELECT source, raw_text, reason, occurrence_count, first_seen_at, last_seen_at "
        "FROM unresolved_aliases WHERE sport = ? ORDER BY raw_text",
        (sport,),
    ).fetchall()
    this_run = [r for r in rows if r[5] >= since_timestamp]
    historical = [r for r in rows if r[5] < since_timestamp]
    return this_run, historical


def run_discovery_for_sport(conn, team_resolver, game_resolver, sport: str) -> None:
    print(f"\n{'=' * 60}\nDISCOVERY / DRY RUN: {sport.upper()}\n{'=' * 60}")

    since = _now()  # boundary: anything logged at/after this is "this run"

    collector = OddsCollector(
        conn=conn,
        team_resolver=team_resolver,
        game_resolver=game_resolver,
        dry_run=True,
        min_legitimate_poll_interval_minutes=0,  # allow immediate NFL+CFB back-to-back for this one-off script
    )
    summary = collector.collect(sport)

    if summary["skipped_too_soon"]:
        print("Skipped: too soon since last poll (unexpected for a fresh discovery run).")
        return

    print(f"Games returned:        {summary['games_returned']}")
    print(f"Bookmakers seen:       {sorted(summary['bookmakers_seen'])}")
    print(f"Would insert:          {len(summary['dry_run_would_insert'])}")
    print(f"Records skipped:       {summary['records_skipped']}")
    print(f"Already-started games: {summary['already_started_skipped']}")
    print(f"API usage info:        {summary['usage_info']}")
    print(f"raw_response_id:       {summary['raw_response_id']}")

    if summary["dry_run_would_insert"]:
        print("\nSample of what would be inserted (first 5):")
        for row in summary["dry_run_would_insert"][:5]:
            print(f"  {row}")

    this_run, historical = classify_unresolved_aliases(conn, sport, since)

    if this_run:
        print(f"\nUNRESOLVED THIS RUN ({len(this_run)}) -- these are real, current failures, "
              f"need review/seed additions:")
        for source, raw_text, reason, count, first_seen, last_seen in this_run:
            print(f"  source={source!r} raw_text={raw_text!r} reason={reason} seen={count}x")
    else:
        print("\nUnresolved aliases encountered THIS RUN: 0")

    if historical:
        print(f"\nHistorical unresolved-alias audit rows retained for {sport} ({len(historical)}) "
              f"-- NOT from this run, preserved as provenance/history, not currently blocking anything:")
        for source, raw_text, reason, count, first_seen, last_seen in historical:
            print(f"  source={source!r} raw_text={raw_text!r} reason={reason} "
                  f"seen={count}x last_seen={last_seen}")


def main():
    api_key = get_env("THE_ODDS_API_KEY", required=True)
    print(f"Using API key ending in ...{api_key[-4:]} (never printed in full)")

    db_path = DEFAULT_DB_PATH
    run_migration(db_path)
    seed(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)

    run_discovery_for_sport(conn, team_resolver, game_resolver, "nfl")
    run_discovery_for_sport(conn, team_resolver, game_resolver, "cfb")

    print(f"\n{'=' * 60}\nDone. This was a DRY RUN -- zero market_snapshots rows were "
          f"written. Database: {db_path}\n{'=' * 60}")
    print(
        "\nPlease report back: games returned, bookmakers returned, any "
        "unresolved aliases, and the API usage info (credits used/remaining) "
        "shown above, so we can confirm real field shapes match our "
        "assumptions before enabling live scheduled collection."
    )

    conn.close()


if __name__ == "__main__":
    main()
