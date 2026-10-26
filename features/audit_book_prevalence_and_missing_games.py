"""
features/audit_book_prevalence_and_missing_games.py

READ-ONLY. Two questions, one script, no writes anywhere:

1. Per-book prevalence, per cohort. No sharp/public classification, no
   weighting -- purely descriptive counts.

2. Missing-game investigation, for the SPECIFIC gap this script itself
   finds at run time (not a hardcoded list) -- the exact NFL/CFB games
   with zero market_snapshots rows. For each, attempts to distinguish:
     - IDENTITY/RESOLUTION issue: the team's the-odds-api-sourced alias
       appears in unresolved_aliases, meaning odds_collector likely DID
       see this game but failed to resolve one side.
     - NEVER SEEN BY THE API: neither team's known the-odds-api alias
       text appears in ANY stored raw_responses body for odds_collector
       -- consistent with the market simply not being posted yet (weighed
       against how far out kickoff actually is) or this matchup/tier
       never being offered by the tracked books at all.
     - SEEN BUT NOT STORED: the team names appear in a raw response body
       but produced no market_snapshots row -- a genuine processing
       question worth a closer, separate look.
     - UNKNOWN: none of the above is determinable from available data --
       labeled as such explicitly, never guessed.

CFB conference information is included ONLY if a genuinely structured,
reliable field for it exists. It does not -- reference/fbs_membership.csv's
source_note is free-text provenance commentary, not a structured
conference field, and is deliberately NOT parsed here to avoid exactly
the kind of fragile manual inference this audit was told not to do.

2026 FBS membership: this script reuses the SAME season=2026-with-2025-
fallback approach as the coverage audit, for consistency -- explicitly
NOT a production-ready 2026 membership definition, and flagged again here
per instruction to keep that issue open.

Usage:
    python3 features/audit_book_prevalence_and_missing_games.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
import sqlite3  # noqa: E402

NFL_2026_SEASON_START = "2026-08-01T00:00:00Z"
NFL_2026_SEASON_END = "2027-02-01T00:00:00Z"
CFB_2026_SEASON_START = "2026-08-01T00:00:00Z"
CFB_2026_SEASON_END = "2027-02-01T00:00:00Z"


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def get_nfl_preseason_cohort(conn):
    return conn.execute(
        "SELECT DISTINCT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc JOIN market_snapshots ms ON gc.canonical_game_id = ms.canonical_game_id "
        "WHERE ms.sport = 'nfl_preseason' ORDER BY gc.commence_time"
    ).fetchall()


def get_nfl_regular_cohort(conn, preseason_ids):
    rows = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = 'nfl' AND commence_time >= ? AND commence_time < ? ORDER BY commence_time",
        (NFL_2026_SEASON_START, NFL_2026_SEASON_END),
    ).fetchall()
    return [r for r in rows if r[0] not in preseason_ids]


def get_cfb_regular_cohort(conn, fbs_allowlist):
    rows = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = 'cfb' AND commence_time >= ? AND commence_time < ? ORDER BY commence_time",
        (CFB_2026_SEASON_START, CFB_2026_SEASON_END),
    ).fetchall()
    return [r for r in rows if r[1] in fbs_allowlist and r[2] in fbs_allowlist]


def audit_book_prevalence(conn, label, games, market_sport_filter):
    total = len(games)
    print(f"\n{'=' * 70}\n{label} -- PER-BOOK PREVALENCE ({total} games)\n{'=' * 70}")
    if total == 0:
        print("  No games in this cohort yet.")
        return

    game_ids = [g[0] for g in games]
    placeholders = ",".join("?" * len(game_ids))
    params = list(game_ids)
    sport_clause = ""
    if market_sport_filter:
        sport_clause = " AND sport = ?"
        params.append(market_sport_filter)

    books = conn.execute(
        f"SELECT DISTINCT book FROM market_snapshots WHERE canonical_game_id IN ({placeholders}){sport_clause} "
        f"ORDER BY book",
        params,
    ).fetchall()
    books = [b[0] for b in books]

    if not books:
        print("  No books observed in this cohort yet.")
        return

    for book in books:
        rows = conn.execute(
            f"SELECT canonical_game_id, COUNT(*) as obs_count, MIN(observed_at), MAX(observed_at) "
            f"FROM market_snapshots WHERE canonical_game_id IN ({placeholders}){sport_clause} AND book = ? "
            f"GROUP BY canonical_game_id",
            params + [book],
        ).fetchall()

        games_with_any = len(rows)
        games_with_multiple = sum(1 for r in rows if r[1] >= 2)
        obs_counts = [r[1] for r in rows]
        earliest_overall = min((r[2] for r in rows), default=None)
        latest_overall = max((r[3] for r in rows), default=None)

        print(f"\n  [{book}]")
        print(f"    Games with >=1 observation: {games_with_any}/{total} ({100 * games_with_any / total:.1f}%)")
        print(f"    Games with >=2 observations: {games_with_multiple}/{total} "
              f"({100 * games_with_multiple / total:.1f}%)")
        if obs_counts:
            print(f"    Observations per game -- min={min(obs_counts)} max={max(obs_counts)} "
                  f"mean={sum(obs_counts)/len(obs_counts):.1f}")
        print(f"    Earliest observation seen (this cohort): {earliest_overall}")
        print(f"    Latest observation seen (this cohort):   {latest_overall}")


def get_team_display_name(conn, canonical_team_id):
    row = conn.execute(
        "SELECT display_name FROM teams_canonical WHERE canonical_team_id = ?", (canonical_team_id,)
    ).fetchone()
    return row[0] if row else canonical_team_id


def get_the_odds_api_aliases(conn, canonical_team_id):
    rows = conn.execute(
        "SELECT alias_text FROM team_aliases WHERE canonical_team_id = ? AND source = 'the_odds_api'",
        (canonical_team_id,),
    ).fetchall()
    return [r[0] for r in rows]


def find_games_missing_market_data(conn, games):
    missing = []
    for gid, home, away, commence_time in games:
        count = conn.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE canonical_game_id = ?", (gid,)
        ).fetchone()[0]
        if count == 0:
            missing.append((gid, home, away, commence_time))
    return missing


def check_unresolved_alias_for_team(conn, sport, home_display, away_display):
    rows = conn.execute(
        "SELECT raw_text, reason FROM unresolved_aliases WHERE sport = ? AND source = 'the_odds_api'",
        (sport,),
    ).fetchall()
    hits = []
    for raw_text, reason in rows:
        if home_display.split()[0].lower() in raw_text.lower() or away_display.split()[0].lower() in raw_text.lower():
            hits.append((raw_text, reason))
    return hits


def check_raw_responses_for_team(conn, sport_key, home_aliases, away_aliases):
    """sport_key must be the EXACT the-odds-api sport key (e.g.
    'americanfootball_nfl'), matched as a precise URL path segment --
    NOT a loose substring, since 'americanfootball_nfl' is itself a
    substring of 'americanfootball_nfl_preseason' and a naive LIKE match
    would incorrectly pull in preseason requests when investigating a
    regular-season game."""
    rows = conn.execute(
        "SELECT raw_response_id, response_body FROM raw_responses "
        "WHERE collector_name = 'odds_collector' AND request_url LIKE ?",
        (f"%/sports/{sport_key}/odds%",),
    ).fetchall()
    for raw_response_id, body in rows:
        if body is None:
            continue
        for alias in home_aliases:
            if alias in body:
                for alias2 in away_aliases:
                    if alias2 in body:
                        return True, raw_response_id
    return False, None


def investigate_missing_game(conn, gid, home_id, away_id, commence_time, sport, sport_key):
    home_display = get_team_display_name(conn, home_id)
    away_display = get_team_display_name(conn, away_id)
    hours_to_kickoff = (_parse_iso(commence_time) - _now()).total_seconds() / 3600

    print(f"\n  Game {gid}: {away_display} @ {home_display}")
    print(f"    Kickoff: {commence_time} ({hours_to_kickoff:.1f} hours from now, "
          f"{'future' if hours_to_kickoff > 0 else 'past'})")

    unresolved_hits = check_unresolved_alias_for_team(conn, sport, home_display, away_display)
    if unresolved_hits:
        print("    Classification: IDENTITY/RESOLUTION ISSUE")
        print(f"      Unresolved alias entries potentially related: {unresolved_hits}")
        return

    home_aliases = get_the_odds_api_aliases(conn, home_id)
    away_aliases = get_the_odds_api_aliases(conn, away_id)
    if not home_aliases or not away_aliases:
        print("    Classification: UNKNOWN -- missing a known the-odds-api alias for one or both teams, "
              "cannot search raw responses reliably")
        return

    seen_in_raw, raw_id = check_raw_responses_for_team(conn, sport_key, home_aliases, away_aliases)
    if seen_in_raw:
        print(f"    Classification: SEEN IN RAW RESPONSE BUT NOT STORED (raw_response_id={raw_id}) -- "
              f"a genuine processing question worth a closer, separate look")
    else:
        if hours_to_kickoff > 240:
            print(f"    Classification: NEVER SEEN BY THE API, consistent with 'market not yet posted' "
                  f"given {hours_to_kickoff:.0f}h lead time -- not necessarily a defect")
        else:
            print(f"    Classification: NEVER SEEN BY THE API, and kickoff is only {hours_to_kickoff:.0f}h away -- "
                  f"worth a closer look; could mean this matchup/tier is simply not offered by the tracked "
                  f"books, or a real gap")


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    fbs_allowlist = load_fbs_allowlist(2026) or load_fbs_allowlist(2025)
    if not load_fbs_allowlist(2026):
        print("REMINDER: no 2026 FBS membership data exists yet -- using the 2025 list as an "
              "EXPLORATORY fallback only, per instruction. This is NOT the production 2026 definition "
              "and remains an open item before CFB V2 is finalized.")

    preseason_games = get_nfl_preseason_cohort(conn)
    preseason_ids = {g[0] for g in preseason_games}
    regular_nfl_games = get_nfl_regular_cohort(conn, preseason_ids)
    cfb_games = get_cfb_regular_cohort(conn, fbs_allowlist)

    audit_book_prevalence(conn, "NFL PRESEASON 2026", preseason_games, "nfl_preseason")
    audit_book_prevalence(conn, "NFL REGULAR SEASON 2026", regular_nfl_games, "nfl")
    audit_book_prevalence(conn, "CFB REGULAR SEASON 2026 (FBS-vs-FBS)", cfb_games, "cfb")

    print(f"\n{'=' * 70}\nMISSING-GAME INVESTIGATION\n{'=' * 70}")
    print("CFB conference information: NOT included -- no structured, reliable conference field "
          "exists in the current data/reference layer (fbs_membership.csv's source_note is free-text "
          "provenance commentary, not a queryable conference field). Not fabricated for this audit.")

    missing_nfl = find_games_missing_market_data(conn, regular_nfl_games)
    print(f"\nNFL regular season games with ZERO market observations: {len(missing_nfl)}")
    for gid, home, away, commence_time in missing_nfl:
        investigate_missing_game(conn, gid, home, away, commence_time, "nfl", "americanfootball_nfl")

    missing_cfb = find_games_missing_market_data(conn, cfb_games)
    print(f"\nCFB FBS-vs-FBS games with ZERO market observations: {len(missing_cfb)}")
    for gid, home, away, commence_time in missing_cfb:
        investigate_missing_game(conn, gid, home, away, commence_time, "cfb", "americanfootball_ncaaf")

    conn.close()


if __name__ == "__main__":
    main()
