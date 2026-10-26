"""
collectors/run_results_backfill.py

REAL BACKFILL. Writes game_results rows for the approved scope only:
  - 2025 NFL regular season (Weeks 1-18)
  - 2025 CFB regular season, FBS-vs-FBS only (per reference/fbs_membership.csv)

No postseason/bowls. Uses the exact window generation, FBS allowlist, and
per-side classification logic already validated in
run_results_backfill_dryrun.py (imported directly, not reimplemented) --
that script and results_collector.py are both left untouched by this file.

NFL: uses ResultsCollector.collect() directly, dry_run=False. That method's
own append-only insert / content-level dedup / Option-A confirmation-link
logic (already built and tested in results_collector.py) handles
everything correctly with no filter needed.

CFB: results_collector.collect() has no concept of the FBS filter (by
design -- that logic lives here, not in the collector). So this script
calls collect(dry_run=True) per window (identical to the dry run: real
request, identity resolution, raw response stored, zero game_results
writes), then performs a TARGETED write pass over only the classified
'retained' games. The write path mirrors results_collector's own
dedup/insert/link logic (same RESULT_COMPARISON_FIELDS content check,
same 'scoreboard' / 'scoreboard_confirmation' role semantics) rather than
calling into a private method across files, to keep both already-tested
files unmodified.

Usage:
    python collectors/run_results_backfill.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import run_migration, DEFAULT_DB_PATH  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from collectors.results_collector import ResultsCollector, TERMINAL_STATUSES, RESULT_COMPARISON_FIELDS  # noqa: E402
from collectors.run_results_backfill_dryrun import (  # noqa: E402
    generate_windows, load_fbs_allowlist, classify_side,
    NFL_WINDOW_START, NFL_WINDOW_COUNT, CFB_WINDOW_START, CFB_WINDOW_COUNT,
)
from collectors.run_odds_discovery import classify_unresolved_aliases, _now  # noqa: E402
import sqlite3  # noqa: E402

BACKFILL_SEASON = 2025


def write_cfb_result_if_new_or_changed(conn, canonical_game_id, home_raw, away_raw,
                                        commence_time_raw, observed_at, parsed, raw_response_id, parser_version):
    existing = conn.execute(
        "SELECT result_id, game_status, is_final, final_score_home, final_score_away, winner_home, "
        "is_overtime, is_neutral_site, season_year, season_type, week_number "
        "FROM game_results WHERE canonical_game_id = ? ORDER BY retrieved_at DESC LIMIT 1",
        (canonical_game_id,),
    ).fetchone()

    if existing is not None:
        existing_dict = dict(zip(RESULT_COMPARISON_FIELDS, existing[1:]))
        if ResultsCollector._results_materially_equal(existing_dict, parsed):
            existing_result_id = existing[0]
            conn.execute(
                "INSERT INTO game_results_raw_response_links (result_id, raw_response_id, role) "
                "VALUES (?, ?, 'scoreboard_confirmation')",
                (existing_result_id, raw_response_id),
            )
            conn.commit()
            return "unchanged"
        outcome = "corrected"
    else:
        outcome = "new"

    cur = conn.execute(
        "INSERT INTO game_results "
        "(canonical_game_id, observed_at, game_status, is_final, final_score_home, "
        "final_score_away, winner_home, is_overtime, is_neutral_site, season_year, "
        "season_type, week_number, venue_name, espn_status_detail, "
        "source, source_url, retrieved_at, source_reported_at, parser_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            canonical_game_id, observed_at, parsed["game_status"], parsed["is_final"],
            parsed["final_score_home"], parsed["final_score_away"], parsed["winner_home"],
            parsed["is_overtime"], parsed["is_neutral_site"], parsed["season_year"],
            parsed["season_type"], parsed["week_number"], parsed["venue_name"],
            parsed["espn_status_detail"],
            "espn", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
            observed_at, commence_time_raw, parser_version,
        ),
    )
    result_id = cur.lastrowid
    conn.execute(
        "INSERT INTO game_results_raw_response_links (result_id, raw_response_id, role) "
        "VALUES (?, ?, 'scoreboard')",
        (result_id, raw_response_id),
    )
    conn.commit()
    return outcome


def backfill_cfb_window(conn, team_resolver, game_resolver, collector, fbs_allowlist, dates_str):
    since = _now()
    summary = collector.collect("cfb", dates=dates_str)
    if summary["raw_response_id"] is None:
        return {"failed": True, "new": 0, "unchanged": 0, "corrected": 0, "excluded": 0,
                "blocking": 0, "unresolved_this_window": 0}

    body = conn.execute(
        "SELECT response_body FROM raw_responses WHERE raw_response_id = ?", (summary["raw_response_id"],)
    ).fetchone()[0]
    events = json.loads(body).get("events", [])

    outcome_counts = {"new": 0, "unchanged": 0, "corrected": 0, "excluded": 0, "blocking": 0}

    for event in events:
        parsed = ResultsCollector._extract_result_fields(event)
        if parsed is None or parsed["game_status"] not in TERMINAL_STATUSES:
            continue

        competitions = event.get("competitions") or []
        competitors = (competitions[0].get("competitors") if competitions else None) or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_name = (home.get("team") or {}).get("displayName") if home else None
        away_name = (away.get("team") or {}).get("displayName") if away else None
        game_id = event.get("id")
        commence_time_raw = event.get("date") or (competitions[0].get("date") if competitions else None)
        if not home_name or not away_name or not game_id or not commence_time_raw:
            continue

        home_status, _ = classify_side(conn, team_resolver, "espn", home_name, "cfb", fbs_allowlist)
        away_status, _ = classify_side(conn, team_resolver, "espn", away_name, "cfb", fbs_allowlist)

        if home_status == "fbs" and away_status == "fbs":
            canonical_game_id = game_resolver.resolve_or_create(
                "espn", str(game_id), "cfb", home_name, away_name, commence_time_raw,
            )
            if canonical_game_id is None:
                outcome_counts["blocking"] += 1
                continue
            outcome = write_cfb_result_if_new_or_changed(
                conn, canonical_game_id, home_name, away_name, commence_time_raw,
                since, parsed, summary["raw_response_id"], collector.PARSER_VERSION,
            )
            outcome_counts[outcome] += 1
        elif home_status == "not_fbs" or away_status == "not_fbs":
            outcome_counts["excluded"] += 1
        else:
            outcome_counts["blocking"] += 1

    this_run_unresolved, _ = classify_unresolved_aliases(conn, "cfb", since)
    outcome_counts["unresolved_this_window"] = len(this_run_unresolved)
    outcome_counts["failed"] = False
    return outcome_counts


def backfill_nfl(conn, team_resolver, game_resolver):
    print(f"\n{'=' * 70}\nNFL 2025 REGULAR SEASON -- REAL BACKFILL\n{'=' * 70}")
    windows = generate_windows(NFL_WINDOW_START, NFL_WINDOW_COUNT)
    collector = ResultsCollector(
        conn=conn, team_resolver=team_resolver, game_resolver=game_resolver,
        dry_run=False, min_legitimate_poll_interval_minutes=0,
    )

    totals = {"new": 0, "unchanged": 0, "requests": 0, "failed": []}

    for week_num, start, end in windows:
        dates_str = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        since = _now()
        summary = collector.collect("nfl", dates=dates_str)
        totals["requests"] += 1
        if summary["raw_response_id"] is None:
            totals["failed"].append(("nfl", week_num, dates_str))
            print(f"Week {week_num:2} ({dates_str}): REQUEST FAILED")
            continue

        this_run_unresolved, _ = classify_unresolved_aliases(conn, "nfl", since)
        print(f"Week {week_num:2} ({dates_str}): inserted={summary['results_inserted']} "
              f"unchanged={summary['results_unchanged_skipped']} "
              f"unresolved_this_window={len(this_run_unresolved)}")
        totals["new"] += summary["results_inserted"]
        totals["unchanged"] += summary["results_unchanged_skipped"]

    return totals


def backfill_cfb(conn, team_resolver, game_resolver, fbs_allowlist):
    print(f"\n{'=' * 70}\nCFB 2025 REGULAR SEASON, FBS-vs-FBS ONLY -- REAL BACKFILL\n{'=' * 70}")
    windows = generate_windows(CFB_WINDOW_START, CFB_WINDOW_COUNT)
    collector = ResultsCollector(
        conn=conn, team_resolver=team_resolver, game_resolver=game_resolver,
        dry_run=True,
        min_legitimate_poll_interval_minutes=0,
    )

    totals = {"new": 0, "unchanged": 0, "corrected": 0, "excluded": 0, "blocking": 0,
              "unresolved": 0, "requests": 0, "failed": []}

    for week_num, start, end in windows:
        dates_str = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        totals["requests"] += 1
        result = backfill_cfb_window(conn, team_resolver, game_resolver, collector, fbs_allowlist, dates_str)
        if result["failed"]:
            totals["failed"].append(("cfb", week_num, dates_str))
            print(f"Week {week_num:2} ({dates_str}): REQUEST FAILED")
            continue

        print(f"Week {week_num:2} ({dates_str}): new={result['new']} unchanged={result['unchanged']} "
              f"corrected={result['corrected']} excluded={result['excluded']} "
              f"blocking={result['blocking']} unresolved_this_window={result['unresolved_this_window']}")

        for k in ("new", "unchanged", "corrected", "excluded", "blocking"):
            totals[k] += result[k]
        totals["unresolved"] += result["unresolved_this_window"]

    return totals


def verify_and_report(conn, fbs_allowlist, game_results_before):
    print(f"\n{'=' * 70}\nPOST-BACKFILL VERIFICATION\n{'=' * 70}")

    nfl_rows = conn.execute(
        "SELECT DISTINCT gr.canonical_game_id FROM game_results gr "
        "JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id WHERE gc.sport = 'nfl'"
    ).fetchall()
    print(f"Unique NFL results stored: {len(nfl_rows)} (expected 272)")

    cfb_rows = conn.execute(
        "SELECT gr.canonical_game_id, gc.home_team_id, gc.away_team_id "
        "FROM game_results gr JOIN games_canonical gc ON gr.canonical_game_id = gc.canonical_game_id "
        "WHERE gc.sport = 'cfb'"
    ).fetchall()
    unique_cfb_games = {r[0] for r in cfb_rows}
    print(f"Unique CFB results stored: {len(unique_cfb_games)} (expected 761)")

    non_fbs_violations = [r for r in cfb_rows if r[1] not in fbs_allowlist or r[2] not in fbs_allowlist]
    print(f"CFB stored-result FBS integrity check: {len(non_fbs_violations)} violation(s)")
    if non_fbs_violations:
        print(f"  VIOLATIONS: {non_fbs_violations}")

    dup_check = conn.execute(
        "SELECT canonical_game_id, COUNT(*) as n FROM game_results GROUP BY canonical_game_id HAVING n > 1"
    ).fetchall()
    print(f"\nDuplicate canonical_game_id result rows: {len(dup_check)}")
    for canonical_game_id, n in dup_check:
        rows = conn.execute(
            "SELECT result_id, game_status, final_score_home, final_score_away, retrieved_at "
            "FROM game_results WHERE canonical_game_id = ? ORDER BY retrieved_at", (canonical_game_id,)
        ).fetchall()
        print(f"  canonical_game_id={canonical_game_id}: {n} rows -- legitimate append-only correction history")
        for r in rows:
            print(f"    {r}")

    missing_scores = conn.execute(
        "SELECT result_id FROM game_results WHERE is_final = 1 "
        "AND (final_score_home IS NULL OR final_score_away IS NULL)"
    ).fetchall()
    print(f"\nFinal games missing a score: {len(missing_scores)} "
          f"({'PASS' if not missing_scores else 'FAIL -- ' + str(missing_scores)})")

    orphan_links = conn.execute(
        "SELECT l.link_id FROM game_results_raw_response_links l "
        "LEFT JOIN game_results r ON l.result_id = r.result_id WHERE r.result_id IS NULL"
    ).fetchall()
    unlinked_results = conn.execute(
        "SELECT r.result_id FROM game_results r "
        "LEFT JOIN game_results_raw_response_links l ON r.result_id = l.result_id WHERE l.link_id IS NULL"
    ).fetchall()
    print(f"\nProvenance integrity: orphan links={len(orphan_links)}, "
          f"results with zero raw-response links={len(unlinked_results)} "
          f"({'PASS' if not orphan_links and not unlinked_results else 'FAIL'})")

    game_results_after = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    print(f"\nFinal game_results row count: before={game_results_before}, after={game_results_after}")


def main():
    db_path = DEFAULT_DB_PATH
    run_migration(db_path)
    seed(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    game_results_before = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    print(f"game_results rows before backfill: {game_results_before}")

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)
    fbs_allowlist = load_fbs_allowlist(BACKFILL_SEASON)

    nfl_totals = backfill_nfl(conn, team_resolver, game_resolver)
    cfb_totals = backfill_cfb(conn, team_resolver, game_resolver, fbs_allowlist)

    print(f"\n{'=' * 70}\nBACKFILL SUMMARY\n{'=' * 70}")
    print(f"NFL: new={nfl_totals['new']} unchanged={nfl_totals['unchanged']} "
          f"requests={nfl_totals['requests']} failed={nfl_totals['failed']}")
    print(f"CFB: new={cfb_totals['new']} unchanged={cfb_totals['unchanged']} "
          f"corrected={cfb_totals['corrected']} excluded={cfb_totals['excluded']} "
          f"blocking={cfb_totals['blocking']} unresolved={cfb_totals['unresolved']} "
          f"requests={cfb_totals['requests']} failed={cfb_totals['failed']}")

    all_failed = nfl_totals["failed"] + cfb_totals["failed"]
    print(f"\nTotal failed requests: {len(all_failed)}")
    if all_failed:
        print(f"  {all_failed}")
    if cfb_totals["blocking"] > 0:
        print(f"\nUNEXPECTED: {cfb_totals['blocking']} blocking case(s) encountered during the real backfill "
              f"despite the dry run showing zero. STOPPING for review rather than guessing.")

    verify_and_report(conn, fbs_allowlist, game_results_before)

    conn.close()


if __name__ == "__main__":
    main()
