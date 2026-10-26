"""
collectors/run_results_backfill_dryrun.py

MANUAL, LOCAL-ONLY, COUNT-ONLY SCRIPT. Projects the scope of a full 2025
NFL (Weeks 1-18) and CFB (FBS-vs-FBS only) results backfill WITHOUT
writing any game_results rows.

============================================================
FIX A -- classification decoupled from full identity resolution
============================================================
The original version required BOTH sides of a game to fully resolve
before rendering any verdict, which meant every never-seeded FCS buy-game
opponent produced a false "blocking" result even when the FBS side alone
already proved the game couldn't be FBS-vs-FBS. Corrected decision table,
per side:

  side resolves AND canonical_team_id in the 2025 FBS allowlist -> 'fbs'
  side resolves AND canonical_team_id NOT in the allowlist       -> 'not_fbs' (known team, just not FBS)
  side does NOT resolve, TeamResolver logged reason='no_match'   -> 'not_fbs' (no matching alias anywhere --
                                                                      see the coverage precondition below for
                                                                      why this is safe to treat as excludable
                                                                      without seeding the opponent)
  side does NOT resolve, reason startswith 'ambiguous'           -> 'ambiguous' (text matched something,
                                                                      just not uniquely -- genuinely unclear)

Per-game verdict:
  RETAINED  if both sides == 'fbs'
  EXCLUDED  if EITHER side == 'not_fbs' -- regardless of the other side's
            status, since FBS-vs-FBS requires both sides FBS; one
            confirmed-non-FBS side already rules the game out even if the
            other side is ambiguous
  BLOCKING  only when neither side is 'not_fbs' and at least one side is
            'ambiguous' -- the only case where the verdict genuinely
            cannot be determined without more information

DATA-DRIVEN COVERAGE PRECONDITION (replaces a naive "does this team have
an alias literally tagged source='espn'" check, which was tested and
found to be the wrong proxy -- most major programs resolve via
TeamResolver's cross-source exact-text match, which doesn't require an
espn-tagged row, since their existing alias text already matches ESPN's
real displayName string). Instead: every canonical_team_id classified
'fbs' anywhere in this run is tracked in a set. After the full season
pull, any of the 136 allowlisted teams NEVER seen in that set is reported
as a genuine coverage gap -- validated against real 2025 ESPN data, not
assumed from a database table.

============================================================
FIX B -- authoritative unique game counts, not summed per-window totals
============================================================
CFB windows previously anchored on Saturday (CFB's single heaviest game
day), so the deliberate 1-day window overlap caused most Saturdays to be
double-counted in a naive per-window sum. Corrected:
  - CFB window anchor moved to Tuesday (matching NFL's scheme -- a
    day with negligible scheduled games in both sports), which does not
    eliminate overlap in principle but removes the systematic worst case.
  - Per-window "retained" counts are now explicitly labeled PROGRESS
    ONLY and are never summed into a reported total.
  - The authoritative CFB total is the count of DISTINCT ESPN
    source_game_key values in the retained set, accumulated across all
    windows in a Python set (an ESPN game id is stable and repeats
    verbatim if a boundary game is touched by two windows, so set()
    dedup is exact, not approximate).
  - Both the raw (duplicated) sum and the deduplicated unique count are
    printed side by side, plus the count of duplicate observations
    removed, so the overlap effect itself stays visible rather than
    silently disappearing.

Usage:
    python collectors/run_results_backfill_dryrun.py
"""

import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import run_migration, DEFAULT_DB_PATH  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from collectors.results_collector import ResultsCollector, TERMINAL_STATUSES  # noqa: E402
from collectors.run_odds_discovery import classify_unresolved_aliases, _now  # noqa: E402
import sqlite3  # noqa: E402

FBS_MEMBERSHIP_PATH = Path(__file__).resolve().parent.parent / "reference" / "fbs_membership.csv"
BACKFILL_SEASON = 2025

NFL_WINDOW_START = date(2025, 9, 2)
NFL_WINDOW_COUNT = 18
CFB_WINDOW_START = date(2025, 8, 19)  # Fix B: moved off Saturday to Tuesday
CFB_WINDOW_COUNT = 16
WINDOW_SPAN_DAYS = 8


def generate_windows(start: date, count: int, span_days: int = WINDOW_SPAN_DAYS):
    windows = []
    cur = start
    for i in range(count):
        end = cur + timedelta(days=span_days - 1)
        windows.append((i + 1, cur, end))
        cur = end
    return windows


def load_fbs_allowlist(season: int) -> set:
    allowlist = set()
    with open(FBS_MEMBERSHIP_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if int(row["season"]) == season and row["classification"] == "FBS":
                allowlist.add(row["canonical_team_id"])
    return allowlist


def static_alias_tag_report(conn, allowlist) -> dict:
    have_espn_tag = 0
    non_espn_tag_only = 0
    zero_aliases = []
    for cid in allowlist:
        rows = conn.execute("SELECT source FROM team_aliases WHERE canonical_team_id=?", (cid,)).fetchall()
        sources = {r[0] for r in rows}
        if not rows:
            zero_aliases.append(cid)
        elif "espn" in sources:
            have_espn_tag += 1
        else:
            non_espn_tag_only += 1
    return {
        "total_checked": len(allowlist),
        "have_espn_tag": have_espn_tag,
        "non_espn_tag_only": non_espn_tag_only,
        "zero_aliases": zero_aliases,
    }


def get_unresolved_reason(conn, sport, source, raw_text):
    row = conn.execute(
        "SELECT reason FROM unresolved_aliases WHERE sport = ? AND source = ? AND raw_text = ?",
        (sport, source, raw_text),
    ).fetchone()
    return row[0] if row else None


def classify_side(conn, team_resolver, source, raw_name, sport, fbs_allowlist):
    team_id = team_resolver.resolve(source, raw_name, sport)
    if team_id is not None:
        return ("fbs", team_id) if team_id in fbs_allowlist else ("not_fbs", team_id)
    reason = get_unresolved_reason(conn, sport, source, raw_name)
    if reason == "no_match":
        return ("not_fbs", None)
    return ("ambiguous", None)


def analyze_raw_response(conn, team_resolver, raw_response_id, sport, fbs_allowlist=None):
    body = conn.execute(
        "SELECT response_body FROM raw_responses WHERE raw_response_id = ?", (raw_response_id,)
    ).fetchone()[0]
    data = json.loads(body)
    events = data.get("events", [])

    stats = {
        "games_returned": len(events),
        "terminal": 0,
        "retained": 0,
        "excluded": 0,
        "blocking": [],
        "score_missing_on_final": [],
        "retained_espn_game_ids": [],
        "fbs_team_ids_seen": set(),
    }

    for event in events:
        game_id = event.get("id")
        parsed = ResultsCollector._extract_result_fields(event)
        if parsed is None:
            continue
        if parsed["game_status"] not in TERMINAL_STATUSES:
            continue
        stats["terminal"] += 1

        competitions = event.get("competitions") or []
        competitors = (competitions[0].get("competitors") if competitions else None) or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        home_name = (home.get("team") or {}).get("displayName") if home else None
        away_name = (away.get("team") or {}).get("displayName") if away else None
        if not home_name or not away_name:
            continue

        if fbs_allowlist is None:
            home_id = team_resolver.resolve("espn", home_name, sport)
            away_id = team_resolver.resolve("espn", away_name, sport)
            if home_id and away_id:
                stats["retained"] += 1
                stats["retained_espn_game_ids"].append(game_id)
                if parsed["is_final"] and (parsed["final_score_home"] is None or parsed["final_score_away"] is None):
                    stats["score_missing_on_final"].append(game_id)
            continue

        home_status, home_id = classify_side(conn, team_resolver, "espn", home_name, sport, fbs_allowlist)
        away_status, away_id = classify_side(conn, team_resolver, "espn", away_name, sport, fbs_allowlist)

        if home_status == "fbs" and away_status == "fbs":
            stats["retained"] += 1
            stats["retained_espn_game_ids"].append(game_id)
            stats["fbs_team_ids_seen"].add(home_id)
            stats["fbs_team_ids_seen"].add(away_id)
            if parsed["is_final"] and (parsed["final_score_home"] is None or parsed["final_score_away"] is None):
                stats["score_missing_on_final"].append(game_id)
        elif home_status == "not_fbs" or away_status == "not_fbs":
            stats["excluded"] += 1
            if home_status == "fbs":
                stats["fbs_team_ids_seen"].add(home_id)
            if away_status == "fbs":
                stats["fbs_team_ids_seen"].add(away_id)
        else:
            stats["blocking"].append({
                "game_id": game_id, "home": home_name, "away": away_name,
                "home_status": home_status, "away_status": away_status,
            })

    return stats


def run_nfl_dryrun(conn, team_resolver, game_resolver):
    print(f"\n{'=' * 70}\nNFL 2025 REGULAR SEASON -- DRY RUN\n{'=' * 70}")
    windows = generate_windows(NFL_WINDOW_START, NFL_WINDOW_COUNT)
    collector = ResultsCollector(
        conn=conn, team_resolver=team_resolver, game_resolver=game_resolver,
        dry_run=True, min_legitimate_poll_interval_minutes=0,
    )

    unique_retained_ids = set()
    raw_retained_sum = 0
    requests_made = 0
    failed_requests = []

    for week_num, start, end in windows:
        dates_str = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        since = _now()
        summary = collector.collect("nfl", dates=dates_str)
        requests_made += 1
        if summary["raw_response_id"] is None:
            failed_requests.append(("nfl", week_num, dates_str))
            print(f"Week {week_num:2} ({dates_str}): REQUEST FAILED -- see collection_log")
            continue

        stats = analyze_raw_response(conn, team_resolver, summary["raw_response_id"], "nfl")
        this_run, _ = classify_unresolved_aliases(conn, "nfl", since)

        print(f"Week {week_num:2} ({dates_str}) [progress only, not summable as total]: "
              f"returned={stats['games_returned']:3} terminal={stats['terminal']:3} "
              f"retained={stats['retained']:3} unresolved_this_week={len(this_run):2}")

        raw_retained_sum += stats["retained"]
        unique_retained_ids.update(stats["retained_espn_game_ids"])

    print(f"\nNFL raw per-window sum (includes overlap duplicates): {raw_retained_sum}")
    print(f"NFL UNIQUE retained games (authoritative): {len(unique_retained_ids)}")
    print(f"NFL duplicate overlap observations removed: {raw_retained_sum - len(unique_retained_ids)}")
    print(f"NFL requests made: {requests_made}")
    if failed_requests:
        print(f"NFL FAILED REQUESTS: {failed_requests}")

    if len(unique_retained_ids) == 272:
        print("Sanity check 1: PASS -- unique NFL total reconciles exactly to the known 272-game season.")
    else:
        diff = len(unique_retained_ids) - 272
        print(f"Sanity check 1: DISCREPANCY -- {len(unique_retained_ids)} unique, expected 272 "
              f"(difference: {diff:+d}).")

    return unique_retained_ids, requests_made, failed_requests


def run_cfb_dryrun(conn, team_resolver, game_resolver, fbs_allowlist):
    print(f"\n{'=' * 70}\nCFB 2025 REGULAR SEASON, FBS-vs-FBS ONLY -- DRY RUN\n{'=' * 70}")
    windows = generate_windows(CFB_WINDOW_START, CFB_WINDOW_COUNT)
    collector = ResultsCollector(
        conn=conn, team_resolver=team_resolver, game_resolver=game_resolver,
        dry_run=True, min_legitimate_poll_interval_minutes=0,
    )

    raw_retained_sum = 0
    raw_excluded_sum = 0
    unique_retained_ids = set()
    fbs_team_ids_seen_overall = set()
    requests_made = 0
    failed_requests = []
    all_blocking = []
    all_score_missing = []

    for week_num, start, end in windows:
        dates_str = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
        since = _now()
        summary = collector.collect("cfb", dates=dates_str)
        requests_made += 1
        if summary["raw_response_id"] is None:
            failed_requests.append(("cfb", week_num, dates_str))
            print(f"Week {week_num:2} ({dates_str}): REQUEST FAILED -- see collection_log")
            continue

        stats = analyze_raw_response(conn, team_resolver, summary["raw_response_id"], "cfb", fbs_allowlist)
        this_run, _ = classify_unresolved_aliases(conn, "cfb", since)

        print(f"Week {week_num:2} ({dates_str}) [progress only, not summable as total]: "
              f"returned={stats['games_returned']:3} terminal={stats['terminal']:3} "
              f"retained={stats['retained']:3} excluded={stats['excluded']:3} "
              f"blocking={len(stats['blocking']):2} unresolved_this_week={len(this_run):2}")

        raw_retained_sum += stats["retained"]
        raw_excluded_sum += stats["excluded"]
        unique_retained_ids.update(stats["retained_espn_game_ids"])
        fbs_team_ids_seen_overall.update(stats["fbs_team_ids_seen"])
        all_blocking.extend(stats["blocking"])
        all_score_missing.extend(stats["score_missing_on_final"])

    print(f"\nCFB raw per-window retained sum (includes overlap duplicates): {raw_retained_sum}")
    print(f"CFB raw per-window excluded sum (includes overlap duplicates): {raw_excluded_sum}")
    print(f"CFB UNIQUE retained FBS-vs-FBS games (authoritative): {len(unique_retained_ids)}")
    print(f"CFB duplicate overlap observations removed: {raw_retained_sum - len(unique_retained_ids)}")
    print(f"CFB requests made: {requests_made}")
    if failed_requests:
        print(f"CFB FAILED REQUESTS: {failed_requests}")

    return (unique_retained_ids, fbs_team_ids_seen_overall, all_blocking,
            all_score_missing, requests_made, failed_requests)


def main():
    db_path = DEFAULT_DB_PATH
    run_migration(db_path)
    seed(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    game_results_before = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)
    fbs_allowlist = load_fbs_allowlist(BACKFILL_SEASON)
    print(f"Loaded {len(fbs_allowlist)} 2025 FBS canonical_team_id values from {FBS_MEMBERSHIP_PATH}")

    tag_report = static_alias_tag_report(conn, fbs_allowlist)
    print(f"\n{'=' * 70}\nSTATIC ALIAS-TAG REPORT (informational only -- does NOT gate "
          f"classification; see module docstring)\n{'=' * 70}")
    print(f"  136/136 allowlisted canonical teams checked: {tag_report['total_checked']}")
    print(f"  Have at least one alias literally tagged source='espn': {tag_report['have_espn_tag']}")
    print(f"  Have only non-espn-tagged aliases: {tag_report['non_espn_tag_only']}")
    print(f"  Zero aliases whatsoever (would be a real problem): {len(tag_report['zero_aliases'])}")
    if tag_report["zero_aliases"]:
        print(f"    {tag_report['zero_aliases']}")

    nfl_unique_ids, nfl_requests, nfl_failed = run_nfl_dryrun(conn, team_resolver, game_resolver)
    (cfb_unique_ids, cfb_fbs_seen, cfb_blocking, cfb_score_missing,
     cfb_requests, cfb_failed) = run_cfb_dryrun(conn, team_resolver, game_resolver, fbs_allowlist)

    game_results_after = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]

    print(f"\n{'=' * 70}\nOVERALL SUMMARY\n{'=' * 70}")
    total_requests = nfl_requests + cfb_requests
    print(f"Total ESPN requests made: {total_requests} (NFL: {nfl_requests}, CFB: {cfb_requests})")
    print(f"Projected UNIQUE NFL results: {len(nfl_unique_ids)}")
    print(f"Projected UNIQUE CFB FBS-vs-FBS results: {len(cfb_unique_ids)}")
    print(f"Projected UNIQUE TOTAL results: {len(nfl_unique_ids) + len(cfb_unique_ids)}")
    print(f"game_results row count before: {game_results_before}, after: {game_results_after} -- "
          f"{'CONFIRMED UNCHANGED' if game_results_before == game_results_after else 'CHANGED -- BUG, INVESTIGATE'}")

    all_failed = nfl_failed + cfb_failed
    print(f"\nFailed/empty requests: {len(all_failed)}")
    if all_failed:
        print(f"  {all_failed}")

    if cfb_score_missing:
        print(f"\nSanity check 4: FAIL -- {len(cfb_score_missing)} final CFB game(s) missing a score: "
              f"{cfb_score_missing}")
    else:
        print("\nSanity check 4: PASS -- every retained-final game has both scores.")

    print(f"\n{'=' * 70}\nDATA-DRIVEN FBS COVERAGE VALIDATION (real 2025 ESPN data, "
          f"not a table check)\n{'=' * 70}")
    never_seen = fbs_allowlist - cfb_fbs_seen
    print(f"FBS teams classified 'fbs' at least once across the full season pull: {len(cfb_fbs_seen)}/136")
    if never_seen:
        print(f"NEVER SEEN as 'fbs' in any game this run ({len(never_seen)}) -- investigate each "
              f"(could be a genuine alias gap, or simply not fully attributable if any requests failed above):")
        for tid in sorted(never_seen):
            print(f"    {tid}")
    else:
        print("PASS -- all 136 FBS teams were successfully classified 'fbs' at least once against "
              "real 2025 ESPN data.")

    print(f"\nSanity check 5 / true remaining blockers -- games where neither side is confirmed "
          f"non-FBS and at least one side is genuinely ambiguous: {len(cfb_blocking)}")
    if cfb_blocking:
        print("  BLOCKING -- these must be resolved before a real backfill runs:")
        for entry in cfb_blocking:
            print(f"    {entry}")
        print("\n  RECOMMENDATION: DO NOT PROCEED to the real backfill until the above are resolved.")
    elif never_seen:
        print("  No blocking games found, but the coverage gap above should be investigated first.")
        print("\n  RECOMMENDATION: investigate the never-seen FBS teams above before proceeding.")
    else:
        print("  PASS -- zero blocking games, full FBS coverage confirmed against real data.")
        print("\n  RECOMMENDATION: no blocking issues found. Awaiting explicit approval before any real backfill write.")

    conn.close()


if __name__ == "__main__":
    main()
