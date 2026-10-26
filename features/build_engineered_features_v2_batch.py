"""
features/build_engineered_features_v2_batch.py

V2 season-wide batch orchestration. Reuses every already-validated
primitive without reimplementing any of their logic:
  - resolve_named_cutoff, market_state_as_of, persist_market_state_as_of,
    PostKickoffCutoffError (features/market_state_as_of.py)
  - persist_features_v2 (features/build_engineered_features_v2.py)
  - get_books_for_game (reporting/checkpoint_reconstruction.py)
  - load_fbs_allowlist (collectors/run_results_backfill_dryrun.py)

COHORT RULES:

NFL regular season: games_canonical.sport='nfl', within the 2026 season
window, EXCLUDING any game tagged 'nfl_preseason' in market_snapshots,
REQUIRING a confirmed game_results.season_type=2 (pre-filtered in SQL,
same pattern as build_engineered_features_v1.py's get_cohort()).

CFB regular season: same base filters, PLUS FBS-vs-FBS via
load_fbs_allowlist(2026) with NO FALLBACK. If that returns empty, CFB
processing is refused entirely for the whole run -- loudly, not silently
substituting the 2025 list the way exploratory audit scripts do. This is
a genuinely different bar than measurement/audit code; production
persistence does not get the same latitude.

NAMED CUTOFFS: T-24h, T-6h, T-1h, kickoff -- each resolved via
resolve_named_cutoff() into a real per-game timestamp. Because this
runner only processes games with an ALREADY-CONFIRMED regular-season
result, every pregame cutoff is structurally guaranteed to already be in
the past by the time a game qualifies for the cohort at all -- the
require_regular_season=True gate enforces this implicitly. An explicit
resolved_cutoff <= now() check is still applied as disclosed defense-in-
depth, not because it's expected to ever fire under normal operation.

RUN SLATE (not built here): the live use case needs cutoff=now() for a
game that HASN'T been played yet, which require_regular_season=True can
never satisfy (no confirmed result exists pregame). A different,
pregame-appropriate safeguard (most plausibly checking market_snapshots'
sport tag rather than season_type) will be needed for that future
milestone -- flagged, not solved here.

For each (game, resolved cutoff): persists market_state_by_cutoff for
EVERY book observed for that game (never a fixed reference book, never a
consensus), then persists the market-agnostic engineered_features_v2 row.
Both persistence calls are the existing, already-tested Option-C
idempotent wrappers -- nothing here bypasses them with a raw INSERT.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from features.market_state_as_of import (  # noqa: E402
    resolve_named_cutoff, persist_market_state_as_of, PostKickoffCutoffError,
)
from features.build_engineered_features_v2 import persist_features_v2  # noqa: E402
from reporting.checkpoint_reconstruction import get_books_for_game  # noqa: E402
import sqlite3  # noqa: E402

NFL_2026_SEASON_START = "2026-08-01T00:00:00Z"
NFL_2026_SEASON_END = "2027-02-01T00:00:00Z"
CFB_2026_SEASON_START = "2026-08-01T00:00:00Z"
CFB_2026_SEASON_END = "2027-02-01T00:00:00Z"

NAMED_CUTOFFS = ["T-24h", "T-6h", "T-1h", "kickoff"]

TALLY = {
    "market_state_new": 0, "market_state_unchanged": 0,
    "features_new": 0, "features_unchanged": 0, "features_refused_not_regular_season": 0,
    "cutoff_skipped_not_yet_reachable": 0, "cutoff_skipped_post_kickoff_guard": 0,
}


def _reset_tally():
    for k in TALLY:
        TALLY[k] = 0


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def check_2026_fbs_membership_available() -> bool:
    """Strict, no fallback. Returns False (and CFB processing must be
    refused entirely) if no real season=2026 FBS rows exist yet."""
    allowlist = load_fbs_allowlist(2026)
    return len(allowlist) > 0


def get_nfl_regular_cohort_v2(conn):
    return conn.execute(
        "SELECT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc WHERE gc.sport='nfl' "
        "AND gc.commence_time >= ? AND gc.commence_time < ? "
        "AND gc.canonical_game_id NOT IN "
        "  (SELECT DISTINCT canonical_game_id FROM market_snapshots WHERE sport='nfl_preseason') "
        "AND gc.canonical_game_id IN ("
        "  SELECT gr1.canonical_game_id FROM game_results gr1"
        "  WHERE gr1.season_type = 2"
        "  AND gr1.retrieved_at = ("
        "    SELECT MAX(gr2.retrieved_at) FROM game_results gr2"
        "    WHERE gr2.canonical_game_id = gr1.canonical_game_id"
        "  )"
        ")",
        (NFL_2026_SEASON_START, NFL_2026_SEASON_END),
    ).fetchall()


def get_cfb_regular_cohort_v2(conn, fbs_allowlist):
    rows = conn.execute(
        "SELECT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc WHERE gc.sport='cfb' "
        "AND gc.commence_time >= ? AND gc.commence_time < ? "
        "AND gc.canonical_game_id IN ("
        "  SELECT gr1.canonical_game_id FROM game_results gr1"
        "  WHERE gr1.season_type = 2"
        "  AND gr1.retrieved_at = ("
        "    SELECT MAX(gr2.retrieved_at) FROM game_results gr2"
        "    WHERE gr2.canonical_game_id = gr1.canonical_game_id"
        "  )"
        ")",
        (CFB_2026_SEASON_START, CFB_2026_SEASON_END),
    ).fetchall()
    return [r for r in rows if r[1] in fbs_allowlist and r[2] in fbs_allowlist]


def process_game_at_cutoff(conn, canonical_game_id, commence_time, named_cutoff, feature_set_version):
    resolved_cutoff = resolve_named_cutoff(commence_time, named_cutoff)

    if _parse_iso(resolved_cutoff) > _now():
        TALLY["cutoff_skipped_not_yet_reachable"] += 1
        return

    books = get_books_for_game(conn, canonical_game_id)
    for book in books:
        try:
            result = persist_market_state_as_of(conn, canonical_game_id, resolved_cutoff, book)
        except PostKickoffCutoffError:
            TALLY["cutoff_skipped_post_kickoff_guard"] += 1
            continue
        if result in ("new", "new_null"):
            TALLY["market_state_new"] += 1
        else:
            TALLY["market_state_unchanged"] += 1

    try:
        result = persist_features_v2(
            conn, canonical_game_id, resolved_cutoff, feature_set_version=feature_set_version,
            require_regular_season=True,
        )
    except PostKickoffCutoffError:
        TALLY["cutoff_skipped_post_kickoff_guard"] += 1
        return

    if result == "new":
        TALLY["features_new"] += 1
    elif result == "unchanged":
        TALLY["features_unchanged"] += 1
    elif result == "refused_not_regular_season":
        TALLY["features_refused_not_regular_season"] += 1


def run_batch(conn, feature_set_version="v2"):
    _reset_tally()

    print(f"{'=' * 70}\nV2 SEASON-WIDE BATCH RUN\n{'=' * 70}")

    nfl_cohort = get_nfl_regular_cohort_v2(conn)
    print(f"NFL regular-season cohort (confirmed season_type=2, preseason excluded): {len(nfl_cohort)} games")

    fbs_available = check_2026_fbs_membership_available()
    if not fbs_available:
        print("\nCFB PROCESSING REFUSED: no verified 2026 FBS membership data exists yet. "
              "This is a hard prerequisite for production CFB persistence -- the 2025 fallback "
              "used by exploratory audit scripts is explicitly NOT used here. "
              "Update reference/fbs_membership.csv with real season=2026 rows before CFB can run.")
        cfb_cohort = []
    else:
        cfb_allowlist = load_fbs_allowlist(2026)
        cfb_cohort = get_cfb_regular_cohort_v2(conn, cfb_allowlist)
        print(f"CFB regular-season cohort (verified 2026 FBS-vs-FBS, confirmed season_type=2): "
              f"{len(cfb_cohort)} games")

    all_games = list(nfl_cohort) + list(cfb_cohort)
    print(f"\nProcessing {len(all_games)} total games x {len(NAMED_CUTOFFS)} named cutoffs each...")

    for gid, home, away, commence_time in all_games:
        for named_cutoff in NAMED_CUTOFFS:
            process_game_at_cutoff(conn, gid, commence_time, named_cutoff, feature_set_version)

    print(f"\n{'=' * 70}\nBATCH SUMMARY\n{'=' * 70}")
    for key, value in TALLY.items():
        print(f"  {key}: {value}")

    return dict(TALLY)


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")
    run_batch(conn)
    conn.close()


if __name__ == "__main__":
    main()
