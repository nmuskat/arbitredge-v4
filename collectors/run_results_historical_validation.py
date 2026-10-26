"""
collectors/run_results_historical_validation.py

MANUAL, LOCAL-ONLY, ONE-OFF SCRIPT. Not part of the ongoing operational
collection flow (that's run_results_discovery.py, untouched by this).
This validates results_collector.py against small, real, already-completed
2025 windows chosen specifically because they each contain a verifiable
neutral-site game:

  NFL: 2025 Week 1 (Sept 4-8, 2025) -- includes Chiefs @ Chargers,
       played at Arena Corinthians, Sao Paulo, Brazil (neutral site).
  CFB: August 30, 2025 only (one day, not the full ~133-game week) --
       includes Syracuse vs Tennessee at Mercedes-Benz Stadium, Atlanta
       (neutral site).

Each window is pulled TWICE in immediate succession (bypassing the
too-soon guard for this one-off script) to directly exercise:
  - content-level dedup: second pull -> zero new game_results rows
  - Option A provenance: second pull's raw response still gets linked to
    the existing result row, via role='scoreboard_confirmation'

This is validation, not backfill -- exactly these two windows, nothing
broader. No ATS grading, no derived features.

Setup:
  1. pip install -r requirements.txt (no API key needed)
  2. python collectors/run_results_historical_validation.py

Please paste the full output back for review.
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

VALIDATION_WINDOWS = [
    {"sport": "nfl", "label": "NFL 2025 Week 1", "dates": "20250904-20250908",
     "expected_neutral_site_matchup": "Kansas City Chiefs @ Los Angeles Chargers (Sao Paulo, Brazil)"},
    {"sport": "cfb", "label": "CFB Saturday, Aug 30 2025", "dates": "20250830",
     "expected_neutral_site_matchup": "Syracuse vs Tennessee (Atlanta, GA)"},
]


def run_pull(conn, team_resolver, game_resolver, sport, dates, pull_label):
    since = _now()
    collector = ResultsCollector(
        conn=conn, team_resolver=team_resolver, game_resolver=game_resolver,
        min_legitimate_poll_interval_minutes=0,
    )
    summary = collector.collect(sport, dates=dates)
    print(f"\n--- {pull_label} ---")
    print(f"Games returned:               {summary['games_returned']}")
    print(f"Results inserted:             {summary['results_inserted']}")
    print(f"Unchanged (correctly skipped):{summary['results_unchanged_skipped']}")
    print(f"Duplicate observations linked:{summary['duplicate_observations_linked']}")
    print(f"Non-terminal skipped:         {summary['non_terminal_skipped']}")
    print(f"Other records skipped:        {summary['records_skipped']}")
    print(f"raw_response_id:              {summary['raw_response_id']}")

    this_run, _ = classify_unresolved_aliases(conn, sport, since)
    if this_run:
        print(f"Unresolved THIS pull ({len(this_run)}):")
        for source, raw_text, reason, count, first_seen, last_seen in this_run:
            print(f"  raw_text={raw_text!r} reason={reason}")
    else:
        print("Unresolved aliases this pull: 0")
    return summary


def validate_window(conn, team_resolver, game_resolver, window):
    sport = window["sport"]
    print(f"\n{'=' * 70}\n{window['label'].upper()}\n{'=' * 70}")

    summary1 = run_pull(conn, team_resolver, game_resolver, sport, window["dates"], "FIRST pull")
    summary2 = run_pull(conn, team_resolver, game_resolver, sport, window["dates"], "SECOND identical pull")

    print(f"\nDedup check: first pull inserted {summary1['results_inserted']} result(s); "
          f"second pull inserted {summary2['results_inserted']} (expected 0) and linked "
          f"{summary2['duplicate_observations_linked']} confirmation(s) "
          f"(expected == first pull's insert count).")

    print(f"\nAll stored {sport.upper()} results (this validation run):")
    rows = conn.execute(
        "SELECT gr.result_id, gr.canonical_game_id, gc.home_team_id, gc.away_team_id, "
        "gr.game_status, gr.is_final, gr.final_score_home, gr.final_score_away, "
        "gr.winner_home, gr.is_overtime, gr.is_neutral_site, gr.venue_name "
        "FROM game_results gr "
        "JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id "
        "WHERE gc.sport = ? ORDER BY gr.result_id",
        (sport,),
    ).fetchall()
    for row in rows:
        print(f"  {row}")

    print(f"\nExpected neutral-site matchup to check by eye above: {window['expected_neutral_site_matchup']}")

    result_ids = [r[0] for r in rows]
    if result_ids:
        placeholders = ",".join("?" * len(result_ids))
        links = conn.execute(
            f"SELECT result_id, role, raw_response_id FROM game_results_raw_response_links "
            f"WHERE result_id IN ({placeholders}) ORDER BY result_id, role",
            result_ids,
        ).fetchall()
        print(f"\nProvenance links for these {len(result_ids)} result(s): {len(links)} total link rows")
        confirmation_links = [link for link in links if link[1] == "scoreboard_confirmation"]
        print(f"  'scoreboard' (original) links: {len(links) - len(confirmation_links)}")
        print(f"  'scoreboard_confirmation' (repeat-observation) links: {len(confirmation_links)}")

    raw_response_count = conn.execute(
        "SELECT COUNT(*) FROM raw_responses WHERE collector_name = 'results_collector'"
    ).fetchone()[0]
    print(f"\nTotal raw_responses rows from results_collector so far: {raw_response_count} "
          f"(expected 2 for this window: one per pull, since each pull makes a real HTTP request "
          f"regardless of whether its content was new)")


def main():
    db_path = DEFAULT_DB_PATH
    run_migration(db_path)
    seed(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)

    for window in VALIDATION_WINDOWS:
        validate_window(conn, team_resolver, game_resolver, window)

    print(f"\n{'=' * 70}\nDone. This wrote real game_results rows for exactly the two windows "
          f"above -- no backfill, no other weeks touched. Database: {db_path}\n{'=' * 70}")
    print(
        "\nPlease report back: the full result dump for each sport (does it match the "
        "real final scores you'd expect?), whether the expected neutral-site games show "
        "is_neutral_site=1, the dedup counts from the second pull, and the provenance "
        "link breakdown, so we can confirm this before any backfill is discussed."
    )

    conn.close()


if __name__ == "__main__":
    main()
