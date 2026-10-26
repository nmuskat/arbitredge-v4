"""
features/audit_2026_coverage.py

READ-ONLY. Measures real 2026 coverage before any V2 design decision is
locked in -- deliberately does NOT introduce any new "as-of" computation
logic (that's part of the gated V2 design, not yet approved). Uses only
already-approved, already-tested primitives: checkpoint_reconstruction.py
for market coverage, direct game_context queries for context coverage,
and load_fbs_allowlist for the CFB cohort.

Three cohorts, deliberately never pooled:
  - NFL preseason (sport='nfl_preseason' in market_snapshots; games_canonical
    itself doesn't tag preseason distinctly -- see the odds_collector fix)
  - NFL regular season (sport='nfl', season window, EXCLUDING anything also
    tagged nfl_preseason in market_snapshots for that game)
  - CFB regular season (sport='cfb', FBS-vs-FBS allowlist, season window)

"usable point-in-time market state" is reported as exactly what it can
honestly mean with today's approved primitives: does the game have ANY
legitimate pregame market_snapshots observation at all (the same
underlying fact as "any pregame market observation" -- reported as its
own labeled row per instruction, not a different computation, since no
new as-of primitive exists yet to measure anything more specific).

Usage:
    python3 features/audit_2026_coverage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from reporting.checkpoint_reconstruction import (  # noqa: E402
    get_books_for_game, get_book_snapshots, reconstruct_checkpoints_for_book,
)
import sqlite3  # noqa: E402

NFL_2026_SEASON_START = "2026-08-01T00:00:00Z"
NFL_2026_SEASON_END = "2027-02-01T00:00:00Z"
CFB_2026_SEASON_START = "2026-08-01T00:00:00Z"
CFB_2026_SEASON_END = "2027-02-01T00:00:00Z"


def get_nfl_preseason_cohort(conn):
    rows = conn.execute(
        "SELECT DISTINCT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc JOIN market_snapshots ms ON gc.canonical_game_id = ms.canonical_game_id "
        "WHERE ms.sport = 'nfl_preseason' ORDER BY gc.commence_time"
    ).fetchall()
    return rows


def get_nfl_regular_cohort(conn, preseason_game_ids):
    rows = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = 'nfl' AND commence_time >= ? AND commence_time < ? ORDER BY commence_time",
        (NFL_2026_SEASON_START, NFL_2026_SEASON_END),
    ).fetchall()
    return [r for r in rows if r[0] not in preseason_game_ids]


def get_cfb_regular_cohort(conn, fbs_allowlist):
    rows = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = 'cfb' AND commence_time >= ? AND commence_time < ? ORDER BY commence_time",
        (CFB_2026_SEASON_START, CFB_2026_SEASON_END),
    ).fetchall()
    return [r for r in rows if r[1] in fbs_allowlist and r[2] in fbs_allowlist]


def audit_cohort(conn, label, games):
    total = len(games)
    print(f"\n{'=' * 70}\n{label} -- {total} games\n{'=' * 70}")
    if total == 0:
        print("  No games in this cohort yet.")
        return

    any_market = 0
    multiple_market = 0
    checkpoint_counts = {"earliest_observed": 0, "T-24h": 0, "T-6h": 0, "T-1h": 0, "last_observed_before_kickoff": 0}
    tv_count = 0
    ranking_count = 0
    record_count = 0

    for gid, home, away, commence_time in games:
        books = get_books_for_game(conn, gid)
        game_has_any = False
        game_has_multiple = False
        game_checkpoints_hit = {k: False for k in checkpoint_counts}
        for book in books:
            snapshots = get_book_snapshots(conn, gid, book)
            pregame_count = sum(1 for s in snapshots if s["observed_at"] < commence_time)
            if pregame_count >= 1:
                game_has_any = True
            if pregame_count >= 2:
                game_has_multiple = True
            checkpoints = reconstruct_checkpoints_for_book(gid, commence_time, book, snapshots)
            for name in game_checkpoints_hit:
                if checkpoints[name] is not None:
                    game_checkpoints_hit[name] = True
        if game_has_any:
            any_market += 1
        if game_has_multiple:
            multiple_market += 1
        for name, hit in game_checkpoints_hit.items():
            if hit:
                checkpoint_counts[name] += 1

        ctx = conn.execute(
            "SELECT tv_network, ranking_home, ranking_away, record_home, record_away "
            "FROM game_context WHERE canonical_game_id = ? AND observed_at < ? "
            "ORDER BY observed_at DESC LIMIT 1",
            (gid, commence_time),
        ).fetchone()
        if ctx:
            if ctx[0] is not None:
                tv_count += 1
            if ctx[1] is not None or ctx[2] is not None:
                ranking_count += 1
            if ctx[3] is not None or ctx[4] is not None:
                record_count += 1

    def pct(n):
        return f"{n}/{total} ({100 * n / total:.1f}%)"

    print(f"  Any pregame market observation:        {pct(any_market)}")
    print(f"  Multiple pregame market observations:  {pct(multiple_market)}")
    print(f"  Usable point-in-time market state:      {pct(any_market)} "
          f"(same underlying fact as 'any pregame observation' -- no new as-of "
          f"primitive exists yet to measure anything more specific, per design)")
    for name, count in checkpoint_counts.items():
        print(f"  {name}: {pct(count)}")
    print(f"  TV network (pregame):                  {pct(tv_count)}")
    print(f"  Rankings (pregame):                    {pct(ranking_count)}")
    print(f"  Records (pregame):                     {pct(record_count)}")


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    fbs_allowlist = load_fbs_allowlist(2026) or load_fbs_allowlist(2025)
    if not fbs_allowlist:
        print("WARNING: no FBS membership data found for 2026 or 2025 -- "
              "CFB cohort will be empty until reference/fbs_membership.csv is updated for 2026.")

    preseason_games = get_nfl_preseason_cohort(conn)
    preseason_ids = {g[0] for g in preseason_games}
    regular_nfl_games = get_nfl_regular_cohort(conn, preseason_ids)
    cfb_games = get_cfb_regular_cohort(conn, fbs_allowlist)

    audit_cohort(conn, "NFL PRESEASON 2026", preseason_games)
    audit_cohort(conn, "NFL REGULAR SEASON 2026", regular_nfl_games)
    audit_cohort(conn, "CFB REGULAR SEASON 2026 (FBS-vs-FBS)", cfb_games)

    print(f"\n{'=' * 70}\nNOTE: early in the collection window -- many later checkpoints "
          f"(T-6h, T-1h, last_observed_before_kickoff) are EXPECTED to still show "
          f"low/zero coverage for games whose kickoff hasn't approached yet. "
          f"This is not a defect.\n{'=' * 70}")

    conn.close()


if __name__ == "__main__":
    main()
