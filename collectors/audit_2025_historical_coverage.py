"""
collectors/audit_2025_historical_coverage.py

MANUAL, LOCAL-ONLY, READ-ONLY AUDIT SCRIPT. Writes nothing. Does not touch
game_results (frozen). Reports historical input coverage for the approved
2025 research cohort (272 NFL + 761 CFB FBS-vs-FBS games).

CRITICAL DISTINCTION enforced throughout: a market_snapshot or game_context
row only counts as usable pregame data if its own real-world timestamp
(observed_at) is BEFORE the target game's commence_time. Our odds/context
collectors have only ever been run in CURRENT/live mode (never pointed at
historical 2025 dates) -- so this script is expected to reveal whether
that leaves the 2025 cohort's market/context coverage at or near zero,
rather than assuming it.

Usage:
    python collectors/audit_2025_historical_coverage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
import sqlite3  # noqa: E402

NFL_SEASON_START = "2025-08-01T00:00:00Z"
NFL_SEASON_END = "2026-02-01T00:00:00Z"
CFB_SEASON_START = "2025-08-01T00:00:00Z"
CFB_SEASON_END = "2026-02-01T00:00:00Z"


def get_cohort(conn, sport, fbs_allowlist=None):
    if sport == "nfl":
        rows = conn.execute(
            "SELECT canonical_game_id, commence_time FROM games_canonical "
            "WHERE sport='nfl' AND commence_time >= ? AND commence_time < ? "
            "AND canonical_game_id IN (SELECT canonical_game_id FROM game_results)",
            (NFL_SEASON_START, NFL_SEASON_END),
        ).fetchall()
        return rows
    else:
        rows = conn.execute(
            "SELECT gc.canonical_game_id, gc.commence_time, gc.home_team_id, gc.away_team_id "
            "FROM games_canonical gc WHERE gc.sport='cfb' "
            "AND gc.commence_time >= ? AND gc.commence_time < ? "
            "AND gc.canonical_game_id IN (SELECT canonical_game_id FROM game_results)",
            (CFB_SEASON_START, CFB_SEASON_END),
        ).fetchall()
        return [(r[0], r[1]) for r in rows if r[2] in fbs_allowlist and r[3] in fbs_allowlist]


def audit_sport(conn, sport, cohort):
    total = len(cohort)
    game_ids = [c[0] for c in cohort]
    if not game_ids:
        print(f"No cohort games for {sport}.")
        return

    placeholders = ",".join("?" * len(game_ids))
    commence_by_id = {c[0]: c[1] for c in cohort}

    ms_rows = conn.execute(
        f"SELECT canonical_game_id, observed_at, spread_home FROM market_snapshots "
        f"WHERE canonical_game_id IN ({placeholders})",
        game_ids,
    ).fetchall()
    pregame_ms_by_game = {}
    postgame_or_sameday_ms_count = 0
    for cgid, observed_at, spread_home in ms_rows:
        if observed_at < commence_by_id[cgid]:
            pregame_ms_by_game.setdefault(cgid, []).append(spread_home)
        else:
            postgame_or_sameday_ms_count += 1

    games_with_pregame_snapshot = len(pregame_ms_by_game)
    games_with_multiple_pregame_snapshots = sum(1 for v in pregame_ms_by_game.values() if len(v) > 1)
    games_with_usable_spread = sum(
        1 for v in pregame_ms_by_game.values() if any(s is not None for s in v)
    )

    ctx_rows = conn.execute(
        f"SELECT canonical_game_id, observed_at, ranking_home, ranking_away, "
        f"record_home, record_away, tv_network, kickoff_window FROM game_context "
        f"WHERE canonical_game_id IN ({placeholders})",
        game_ids,
    ).fetchall()
    pregame_ctx_by_game = {}
    postgame_or_sameday_ctx_count = 0
    for row in ctx_rows:
        cgid, observed_at = row[0], row[1]
        if observed_at < commence_by_id[cgid]:
            pregame_ctx_by_game.setdefault(cgid, []).append(row[2:])
        else:
            postgame_or_sameday_ctx_count += 1

    games_with_context = len(pregame_ctx_by_game)
    games_with_ranking = sum(
        1 for v in pregame_ctx_by_game.values() if any(r[0] is not None or r[1] is not None for r in v)
    )
    games_with_records = sum(
        1 for v in pregame_ctx_by_game.values() if any(r[2] is not None or r[3] is not None for r in v)
    )
    games_with_tv = sum(1 for v in pregame_ctx_by_game.values() if any(r[4] is not None for r in v))
    games_with_kickoff_window = sum(1 for v in pregame_ctx_by_game.values() if any(r[5] is not None for r in v))

    games_with_result = conn.execute(
        f"SELECT COUNT(DISTINCT canonical_game_id) FROM game_results WHERE canonical_game_id IN ({placeholders})",
        game_ids,
    ).fetchone()[0]

    def pct(n):
        return f"{n}/{total} ({100 * n / total:.1f}%)"

    print(f"\n{'=' * 70}\n{sport.upper()} -- {total} cohort games\n{'=' * 70}")
    print(f" 1. Total games in cohort:                        {total}")
    print(f" 2. Games with >=1 PREGAME market_snapshot:        {pct(games_with_pregame_snapshot)}")
    print(f" 3. Games with MULTIPLE pregame market_snapshots:  {pct(games_with_multiple_pregame_snapshots)}")
    print(f" 4. Games with usable pregame spread:              {pct(games_with_usable_spread)}")
    print(f" 5. Games with PREGAME game_context:                {pct(games_with_context)}")
    print(f" 6. Games with PREGAME ranking data:               {pct(games_with_ranking)}")
    print(f" 7. Games with PREGAME record data:                {pct(games_with_records)}")
    print(f" 8. Games with PREGAME TV network:                 {pct(games_with_tv)}")
    print(f" 9. Games with PREGAME kickoff_window:             {pct(games_with_kickoff_window)}")
    print(f"10. Games with final result:                       {pct(games_with_result)} (expect 100%)")
    print(f"\n    market_snapshots rows that exist but are NOT usable pregame data "
          f"(observed_at >= commence_time -- current/live lookups, not historical): "
          f"{postgame_or_sameday_ms_count}")
    print(f"    game_context rows that exist but are NOT usable pregame data: "
          f"{postgame_or_sameday_ctx_count}")

    earliest_latest = conn.execute(
        "SELECT MIN(observed_at), MAX(observed_at) FROM market_snapshots"
    ).fetchone()
    print(f"\n11. Overall market_snapshots observed_at range (ALL rows, not cohort-scoped): "
          f"{earliest_latest[0]} to {earliest_latest[1]}")
    earliest_latest_ctx = conn.execute(
        "SELECT MIN(observed_at), MAX(observed_at) FROM game_context"
    ).fetchone()
    print(f"    Overall game_context observed_at range (ALL rows, not cohort-scoped): "
          f"{earliest_latest_ctx[0]} to {earliest_latest_ctx[1]}")
    season_start = NFL_SEASON_START if sport == "nfl" else CFB_SEASON_START
    season_end = NFL_SEASON_END if sport == "nfl" else CFB_SEASON_END
    print(f"    Cohort season window: {season_start} to {season_end}")
    print(f"    (If the observed_at range above falls entirely AFTER the season window, "
          f"every row we have is current/live data collected after the fact -- not true "
          f"historical pregame observations, regardless of raw row counts elsewhere.)")


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")
    fbs_allowlist = load_fbs_allowlist(2025)

    nfl_cohort = get_cohort(conn, "nfl")
    cfb_cohort = get_cohort(conn, "cfb", fbs_allowlist)

    audit_sport(conn, "nfl", nfl_cohort)
    audit_sport(conn, "cfb", cfb_cohort)

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    print(f"Approved cohort size: {len(nfl_cohort)} NFL + {len(cfb_cohort)} CFB = "
          f"{len(nfl_cohort) + len(cfb_cohort)} total")
    print("See per-sport sections above for exact pregame coverage percentages on "
          "market_snapshots, game_context, rankings, records, TV, and kickoff_window.")

    conn.close()


if __name__ == "__main__":
    main()
