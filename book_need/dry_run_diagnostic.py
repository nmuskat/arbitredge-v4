"""
book_need/dry_run_diagnostic.py

Real-production, read-only dry run of Inferred Public Pressure + Inferred
Book Need against real upcoming NFL/CFB games. NEVER calls persist --
prospective writes require an explicit, separate approval step, not this
script.

Usage:
    python3 book_need/dry_run_diagnostic.py --sport nfl --start-date 2026-08-16 --end-date 2026-09-30
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH
from book_need.run_slate_book_need import run_book_need_slate
from book_need.report import format_report
import sqlite3

BANNER = "BOOK NEED DRY RUN \u2014 READ-ONLY, NO PROSPECTIVE WRITES, WEEK 1 PRIOR METHODOLOGY, NOT A CALIBRATED PROBABILITY"


def print_banner():
    line = "!" * len(BANNER)
    print(f"\n{line}\n{BANNER}\n{line}\n")


def main():
    parser = argparse.ArgumentParser(description="Real-production Book Need dry run")
    parser.add_argument("--sport", required=True, choices=["nfl", "cfb"])
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--as-of", default=None, help="Optional explicit as_of for reproducible testing")
    parser.add_argument("--limit", type=int, default=5, help="Max games to show in detail")
    args = parser.parse_args()

    print_banner()

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    tables = ["book_need_prospective_records", "engineered_features", "engineered_features_v2",
              "market_state_by_cutoff", "market_snapshots", "game_results"]
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    result = run_book_need_slate(conn, args.sport, args.start_date, args.end_date, as_of=args.as_of, persist=False)

    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    print(f"Row counts BEFORE: {before}")
    print(f"Row counts AFTER:  {after}")
    if before == after:
        print("[VERIFIED] Zero writes to any table, including book_need_prospective_records.")
    else:
        print("[FAILURE] Row counts changed -- STOP and report before proceeding.")

    print(f"\nSport: {result['sport']}  As-of: {result['as_of']}")

    outcomes = {"NOT_YET_EVALUABLE": 0, "INSUFFICIENT_EVIDENCE": 0, "NO_CLEAR_NEED": 0, "DIRECTIONAL": 0}
    for r in result["results"]:
        side = r["inferred_book_need_side"]
        if side in outcomes:
            outcomes[side] += 1
        else:
            outcomes["DIRECTIONAL"] += 1
    games_evaluated = outcomes["INSUFFICIENT_EVIDENCE"] + outcomes["NO_CLEAR_NEED"] + outcomes["DIRECTIONAL"]

    print(f"Games evaluated (a real segment was reached and assessed): {games_evaluated}")
    print(f"Not yet evaluable (before T-24h, nothing attempted):        {outcomes['NOT_YET_EVALUABLE']}")
    print(f"  -- of games evaluated --")
    print(f"  Insufficient evidence:      {outcomes['INSUFFICIENT_EVIDENCE']}")
    print(f"  No clear need:              {outcomes['NO_CLEAR_NEED']}")
    print(f"  Directional inferred need:  {outcomes['DIRECTIONAL']}")
    print(f"Excluded by eligibility: {len(result['excluded'])}")

    for r in result["results"][: args.limit]:
        print(f"\n{'=' * 70}")
        print(format_report(r))

    print_banner()
    conn.close()


if __name__ == "__main__":
    main()
