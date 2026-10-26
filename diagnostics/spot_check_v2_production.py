"""
diagnostics/spot_check_v2_production.py

Real-production spot-check for the V2 point-in-time primitives, per the
approved smallest-next-step design. Reads real production data
(games_canonical, market_snapshots, game_context, game_results) and
writes ONLY to the two new V2 tables (market_state_by_cutoff,
engineered_features_v2), exclusively through the already-tested
persistence wrappers -- never a raw INSERT of its own. Touches nothing
else: no collectors, no cadence, no launchd, no checkpoint reconstruction,
no identity tables, no existing raw data.

Output is explicitly categorized as one of:
  PASS               -- a real assertion held
  EXPECTED REFUSAL    -- a safeguard correctly refused something it should
  MISSING AS EXPECTED -- a legitimate absence of data correctly returned None
  FAIL                -- an actual defect

Idempotent: every persistence call that's expected to succeed is made
twice within the same run, demonstrating "new" then "unchanged" directly,
and the script can be re-run entirely without creating duplicate rows
(the underlying persist_* wrappers already guarantee this; nothing here
bypasses them).

Usage:
    python3 diagnostics/spot_check_v2_production.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from features.market_state_as_of import (  # noqa: E402
    market_state_as_of, resolve_named_cutoff, PostKickoffCutoffError,
)
from features.context_state_as_of import context_state_as_of  # noqa: E402
from features.build_engineered_features_v2 import persist_features_v2  # noqa: E402
import sqlite3  # noqa: E402

TALLY = {"PASS": 0, "EXPECTED REFUSAL": 0, "MISSING AS EXPECTED": 0, "FAIL": 0}


def report(status, message):
    TALLY[status] = TALLY.get(status, 0) + 1
    print(f"[{status}] {message}")


def _now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_preseason_sample_games(conn, limit=3):
    return conn.execute(
        "SELECT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time, "
        "COUNT(DISTINCT ms.book) as book_count "
        "FROM games_canonical gc JOIN market_snapshots ms ON gc.canonical_game_id = ms.canonical_game_id "
        "WHERE ms.sport = 'nfl_preseason' GROUP BY gc.canonical_game_id "
        "ORDER BY book_count DESC LIMIT ?",
        (limit,),
    ).fetchall()


def get_regular_season_sample_games(conn, sport, limit=3):
    """Excludes any game already known to be NFL preseason (via the
    market_snapshots.sport='nfl_preseason' tag) -- games_canonical.sport
    stays uniformly 'nfl' for both, by design, so this exclusion is
    required or a preseason game could be mistaken for a regular-season
    sample here."""
    preseason_ids = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT canonical_game_id FROM market_snapshots WHERE sport = 'nfl_preseason'"
        ).fetchall()
    }
    rows = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = ? AND commence_time > ? ORDER BY commence_time ASC",
        (sport, _now_str()),
    ).fetchall()
    filtered = [r for r in rows if r[0] not in preseason_ids]
    return filtered[:limit]


def get_permanently_missed_aug13_14_games(conn):
    """Targeted to the known incident window specifically, rather than
    relying on collection_health.py's recent-lookback default, which may
    no longer cover Aug 13-14 by the time this runs."""
    window_start = "2026-08-13T00:00:00Z"
    window_end = "2026-08-15T00:00:00Z"
    games = conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE commence_time >= ? AND commence_time < ? AND sport = 'nfl'",
        (window_start, window_end),
    ).fetchall()
    missed = []
    for gid, home, away, commence_time in games:
        count = conn.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE canonical_game_id = ?", (gid,)
        ).fetchone()[0]
        if count == 0:
            missed.append((gid, home, away, commence_time))
    return missed


def get_v1_snapshot(conn):
    count = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]
    content = conn.execute(
        "SELECT canonical_game_id, feature_set_version, point_in_time_cutoff, home_wins_last_3, "
        "away_wins_last_3, home_last_game_result FROM engineered_features ORDER BY feature_id"
    ).fetchall()
    return count, content


def check_preseason_games(conn, games):
    print(f"\n{'=' * 70}\nPRESEASON SPOT-CHECK ({len(games)} real games with multi-book data)\n{'=' * 70}")
    for gid, home, away, commence_time, book_count in games:
        print(f"\n--- Game {gid}: {home} vs {away} (kickoff {commence_time}, {book_count} books) ---")

        books = conn.execute(
            "SELECT DISTINCT book FROM market_snapshots WHERE canonical_game_id = ? AND sport='nfl_preseason'",
            (gid,),
        ).fetchall()
        books = [b[0] for b in books]

        results_by_book = {}
        for book in books:
            state = market_state_as_of(conn, gid, commence_time, book)
            results_by_book[book] = state
            if state is not None:
                report("PASS", f"market_state_as_of({gid}, kickoff, {book}) returned real data: "
                                f"home_spread={state['home_spread']} favorite_side={state['favorite_side']}")

                expected_staleness = (
                    datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
                    - datetime.fromisoformat(state["market_observation_timestamp"].replace("Z", "+00:00"))
                ).total_seconds()
                if abs(state["staleness_seconds"] - expected_staleness) < 1.0:
                    report("PASS", f"staleness_seconds for {book} matches independently recomputed value "
                                    f"({state['staleness_seconds']:.0f}s)")
                else:
                    report("FAIL", f"staleness_seconds MISMATCH for {book}: reported "
                                     f"{state['staleness_seconds']}, recomputed {expected_staleness}")
            else:
                report("MISSING AS EXPECTED", f"market_state_as_of({gid}, kickoff, {book}) returned None "
                                                f"(no pregame observation for this book at/before kickoff)")

        real_results = {b: s for b, s in results_by_book.items() if s is not None}
        if len(real_results) >= 2:
            books_list = list(real_results.keys())
            b1, b2 = books_list[0], books_list[1]
            if real_results[b1] is not real_results[b2]:
                report("PASS", f"{b1} and {b2} produced independently computed result objects "
                                f"(never the same object)")
            else:
                report("FAIL", f"{b1} and {b2} produced the SAME object reference -- a real collapsing defect")
            print(f"    Cross-book comparison: {b1}={real_results[b1]['home_spread']} "
                  f"vs {b2}={real_results[b2]['home_spread']} (independent values, no consensus computed)")

        ctx = context_state_as_of(conn, gid, commence_time)
        if ctx is not None:
            report("PASS", f"context_state_as_of({gid}, kickoff) returned real data: tv_network={ctx['tv_network']}")
        else:
            report("MISSING AS EXPECTED", f"context_state_as_of({gid}, kickoff) returned None "
                                            f"(no context observation exists at/before kickoff)")

        far_before_cutoff = "2020-01-01T00:00:00Z"
        if books:
            early_state = market_state_as_of(conn, gid, far_before_cutoff, books[0])
            if early_state is None:
                report("MISSING AS EXPECTED", f"market_state_as_of({gid}, far-before-any-real-observation, "
                                                f"{books[0]}) correctly returned None")
            else:
                report("FAIL", f"market_state_as_of returned data for an impossible pre-existence cutoff "
                                 f"-- real defect")

        refused = persist_features_v2(conn, gid, commence_time)  # require_regular_season defaults True
        if refused == "refused_not_regular_season":
            report("EXPECTED REFUSAL", f"persist_features_v2({gid}) correctly refused by default "
                                         f"(preseason, season_type != 2)")
        else:
            report("FAIL", f"persist_features_v2({gid}) did NOT refuse a preseason game under the default "
                             f"safeguard -- got {refused!r} instead of 'refused_not_regular_season'")

        allowed = persist_features_v2(conn, gid, commence_time, require_regular_season=False)
        allowed_again = persist_features_v2(conn, gid, commence_time, require_regular_season=False)
        # allowed may legitimately be "new" (first-ever run) or "unchanged" (script rerun against
        # an already-populated database) -- either is correct. What must ALWAYS hold is that the
        # immediately-following second call is "unchanged", proving idempotency regardless of
        # whether this is the script's first run or a rerun.
        if allowed in ("new", "unchanged") and allowed_again == "unchanged":
            report("PASS", f"persist_features_v2({gid}, require_regular_season=False) is idempotent: "
                            f"first call={allowed!r}, second call='unchanged' as required")
        else:
            report("FAIL", f"persist_features_v2 preseason opt-out path did not behave idempotently: "
                             f"first={allowed!r} second={allowed_again!r}")


def check_regular_season_games(conn, sport, games):
    print(f"\n{'=' * 70}\n{sport.upper()} REGULAR-SEASON SPOT-CHECK ({len(games)} real tracked games, "
          f"live 'as of now' cutoff)\n{'=' * 70}")
    now = _now_str()
    for gid, home, away, commence_time in games:
        print(f"\n--- Game {gid}: {home} vs {away} (kickoff {commence_time}) ---")

        books = conn.execute(
            "SELECT DISTINCT book FROM market_snapshots WHERE canonical_game_id = ? AND sport = ?",
            (gid, sport),
        ).fetchall()
        books = [b[0] for b in books]

        if not books:
            report("MISSING AS EXPECTED", f"no market_snapshots yet for game {gid} -- expected this far from kickoff")
        else:
            for book in books:
                try:
                    state = market_state_as_of(conn, gid, now, book)
                except PostKickoffCutoffError:
                    report("FAIL", "unexpected PostKickoffCutoffError for a game weeks in the future")
                    continue
                if state is not None:
                    report("PASS", f"market_state_as_of({gid}, now, {book}) returned real live-as-of-now "
                                    f"data: home_spread={state['home_spread']}")
                else:
                    report("MISSING AS EXPECTED", f"market_state_as_of({gid}, now, {book}) returned None "
                                                    f"(no observation yet at this early point in the season)")

        refused = persist_features_v2(conn, gid, commence_time)
        if refused == "refused_not_regular_season":
            report("EXPECTED REFUSAL", f"persist_features_v2({gid}) correctly refused -- no confirmed "
                                         f"season_type=2 result exists yet this early in the season")
        else:
            report("FAIL", f"persist_features_v2({gid}) unexpectedly proceeded ({refused!r}) despite no "
                             f"regular-season result existing yet")


def check_permanently_missed_games(conn, missed_games):
    print(f"\n{'=' * 70}\nPERMANENTLY MISSED AUG 13/14 GAMES "
          f"({len(missed_games)} found -- confirming None, never reconstructing)\n{'=' * 70}")
    if not missed_games:
        print("  None found in this database -- not treated as a failure, just noted.")
        return
    for gid, home, away, commence_time in missed_games:
        print(f"\n--- Game {gid}: {home} vs {away} (kickoff {commence_time}) ---")
        state = market_state_as_of(conn, gid, commence_time, "draftkings")
        if state is None:
            report("MISSING AS EXPECTED", f"market_state_as_of({gid}, kickoff, draftkings) correctly "
                                            f"returned None -- this observation window was permanently "
                                            f"lost in the launchd authentication incident, and nothing "
                                            f"here attempts to reconstruct it")
        else:
            report("FAIL", f"market_state_as_of returned NON-None data for a game with confirmed zero "
                             f"observations -- this would mean fabricated data, a serious defect")


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    v1_count_before, v1_content_before = get_v1_snapshot(conn)
    print(f"V1 snapshot before: {v1_count_before} rows in engineered_features")

    preseason_games = get_preseason_sample_games(conn, limit=3)
    nfl_regular_games = get_regular_season_sample_games(conn, "nfl", limit=3)
    cfb_regular_games = get_regular_season_sample_games(conn, "cfb", limit=3)
    missed_games = get_permanently_missed_aug13_14_games(conn)

    if preseason_games:
        check_preseason_games(conn, preseason_games)
    else:
        print("\nNo preseason games with market data found -- skipping preseason section.")

    if nfl_regular_games:
        check_regular_season_games(conn, "nfl", nfl_regular_games)
    if cfb_regular_games:
        check_regular_season_games(conn, "cfb", cfb_regular_games)

    check_permanently_missed_games(conn, missed_games)

    v1_count_after, v1_content_after = get_v1_snapshot(conn)
    if v1_count_before == v1_count_after and v1_content_before == v1_content_after:
        report("PASS", f"V1 (engineered_features) is completely unchanged: {v1_count_before} rows "
                        f"before and after, identical content")
    else:
        report("FAIL", f"V1 CHANGED during this spot-check: {v1_count_before} rows before vs "
                         f"{v1_count_after} after -- a serious defect, V1 must never be touched by V2 operations")

    conn.close()

    print(f"\n{'=' * 70}\nSUMMARY: {TALLY}\n{'=' * 70}")
    if TALLY["FAIL"] > 0:
        print("ATTENTION: real failures found above -- review each [FAIL] line before proceeding further.")
    else:
        print("No real failures. All EXPECTED REFUSAL and MISSING AS EXPECTED results are correct, "
              "designed behavior, not defects.")


if __name__ == "__main__":
    main()
