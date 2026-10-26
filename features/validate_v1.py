"""
features/validate_v1.py

MANUAL, LOCAL-ONLY, READ-ONLY VALIDATION SCRIPT. Writes nothing to
engineered_features except via section 8, which deliberately RE-RUNS the
real build process to prove it's a no-op (exactly what "New rows
written: 0" should look like) -- Option C semantics already tested, not a
new write path.

Reuses the already-tested functions from build_engineered_features_v1.py
(get_cohort, get_all_games, get_latest_results_by_canonical_game,
build_team_index, get_prior_games) rather than re-deriving this logic, so
section 5's "actual prior games used" reconstruction is guaranteed
consistent with what the real build script did.

Usage:
    python features/validate_v1.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from features.build_engineered_features_v1 import (  # noqa: E402
    get_cohort, get_all_games, get_latest_results_by_canonical_game,
    build_team_index, get_prior_games, FEATURE_SET_VERSION, main as run_build,
)
import sqlite3  # noqa: E402

ALL_FEATURE_COLUMNS = [
    "home_games_played_before_this", "away_games_played_before_this",
    "home_last3_sample_size", "away_last3_sample_size",
    "home_last5_sample_size", "away_last5_sample_size",
    "home_current_season_games_before_this", "away_current_season_games_before_this",
    "home_last_game_commence_time", "away_last_game_commence_time",
    "home_wins_last_3", "away_wins_last_3", "home_wins_last_5", "away_wins_last_5",
    "home_points_for_avg_last_3", "away_points_for_avg_last_3",
    "home_points_against_avg_last_3", "away_points_against_avg_last_3",
    "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
    "home_point_margin_avg_last_5", "away_point_margin_avg_last_5",
    "home_current_win_streak", "away_current_win_streak",
    "home_current_loss_streak", "away_current_loss_streak",
    "home_days_since_last_game", "away_days_since_last_game",
    "rest_advantage_days",
    "home_last_game_result", "away_last_game_result",
    "home_last_game_point_margin", "away_last_game_point_margin",
    "home_last_game_points_for", "away_last_game_points_for",
    "home_last_game_points_against", "away_last_game_points_against",
    "home_last_game_blowout_win", "away_last_game_blowout_win",
    "home_last_game_blowout_loss", "away_last_game_blowout_loss",
    "home_last3_prior_season_games", "away_last3_prior_season_games",
    "home_last5_prior_season_games", "away_last5_prior_season_games",
    "home_last_game_same_season", "away_last_game_same_season",
    "competing_games_same_window_same_sport", "competing_games_same_window_all_sports",
    "is_standalone_same_sport", "is_standalone_all_sports", "day_of_week",
    "ranking_home", "ranking_away", "tv_network", "favorite_side", "spread_magnitude",
]

GATED_2025_NULL_COLUMNS = ["ranking_home", "ranking_away", "tv_network", "favorite_side", "spread_magnitude"]

FORBIDDEN_SCORING_SUBSTRINGS = [
    "pressure", "handle", "sharp", "square", "book_need", "bookneed",
    "probability", "ticket_pct", "ticket_percent", "simulated",
]


def section_1_coverage(conn):
    print(f"\n{'=' * 70}\n1. COVERAGE / NULL RATES PER FEATURE\n{'=' * 70}")
    for label, sport_filter in [("NFL (272)", "nfl"), ("CFB (761)", "cfb"), ("COMBINED (1033)", None)]:
        print(f"\n--- {label} ---")
        if sport_filter:
            total = conn.execute(
                "SELECT COUNT(*) FROM engineered_features ef "
                "JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
                "WHERE gc.sport = ? AND ef.feature_set_version = ?",
                (sport_filter, FEATURE_SET_VERSION),
            ).fetchone()[0]
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM engineered_features WHERE feature_set_version = ?", (FEATURE_SET_VERSION,)
            ).fetchone()[0]

        if total == 0:
            print("  No rows found.")
            continue

        for col in ALL_FEATURE_COLUMNS:
            if sport_filter:
                q = (
                    f"SELECT COUNT(*) FROM engineered_features ef "
                    f"JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
                    f"WHERE gc.sport = ? AND ef.feature_set_version = ? AND ef.{col} IS NOT NULL"
                )
                non_null = conn.execute(q, (sport_filter, FEATURE_SET_VERSION)).fetchone()[0]
            else:
                q = f"SELECT COUNT(*) FROM engineered_features WHERE feature_set_version = ? AND {col} IS NOT NULL"
                non_null = conn.execute(q, (FEATURE_SET_VERSION,)).fetchone()[0]
            pct = 100 * non_null / total
            print(f"  {col:45} {non_null:5}/{total} ({pct:5.1f}%)")


def section_2_gated_fields_and_no_scoring(conn):
    print(f"\n{'=' * 70}\n2. GATED-FIELD NULL CONFIRMATION + NO SCORING FIELDS\n{'=' * 70}")
    for col in GATED_2025_NULL_COLUMNS:
        non_null_count = conn.execute(
            f"SELECT COUNT(*) FROM engineered_features WHERE feature_set_version = ? AND {col} IS NOT NULL",
            (FEATURE_SET_VERSION,),
        ).fetchone()[0]
        status = "PASS (100% NULL)" if non_null_count == 0 else f"FAIL -- {non_null_count} non-NULL values found"
        print(f"  {col:20} {status}")

    all_columns = [r[1] for r in conn.execute("PRAGMA table_info(engineered_features)").fetchall()]
    violations = [c for c in all_columns for f in FORBIDDEN_SCORING_SUBSTRINGS if f in c.lower()]
    print(f"\n  Forbidden scoring-field substring check: {len(violations)} violation(s) "
          f"({'PASS -- none found' if not violations else violations})")


def section_3_season_openers(conn, limit=5):
    print(f"\n{'=' * 70}\n3. SEASON-OPENER CHECKS\n{'=' * 70}")
    for sport in ["nfl", "cfb"]:
        print(f"\n--- {sport.upper()} openers (home OR away games_played_before_this = 0) ---")
        rows = conn.execute(
            "SELECT ef.canonical_game_id, gc.commence_time, gc.home_team_id, gc.away_team_id, "
            "ef.home_games_played_before_this, ef.away_games_played_before_this, "
            "ef.home_current_season_games_before_this, ef.away_current_season_games_before_this, "
            "ef.home_last3_prior_season_games, ef.away_last3_prior_season_games, "
            "ef.home_days_since_last_game, ef.away_days_since_last_game "
            "FROM engineered_features ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
            "WHERE gc.sport = ? AND ef.feature_set_version = ? "
            "AND (ef.home_games_played_before_this = 0 OR ef.away_games_played_before_this = 0) "
            "ORDER BY gc.commence_time LIMIT ?",
            (sport, FEATURE_SET_VERSION, limit),
        ).fetchall()
        for r in rows:
            print(f"  game={r[0]} kickoff={r[1]} {r[2]} vs {r[3]}")
            print(f"    home_games_played_before_this={r[4]} away_games_played_before_this={r[5]}")
            print(f"    home_current_season_games_before_this={r[6]} away_current_season_games_before_this={r[7]}")
            print(f"    home_last3_prior_season_games={r[8]} (NULL expected if that side has 0 prior games) "
                  f"away_last3_prior_season_games={r[9]}")
            print(f"    home_days_since_last_game={r[10]} away_days_since_last_game={r[11]}")


def section_4_carryover_examples(conn, limit=5):
    print(f"\n{'=' * 70}\n4. EARLY-SEASON CARRYOVER CHECKS\n{'=' * 70}")
    rows = conn.execute(
        "SELECT ef.canonical_game_id, gc.sport, gc.commence_time, "
        "ef.home_last3_prior_season_games, ef.away_last3_prior_season_games, "
        "ef.home_last5_prior_season_games, ef.away_last5_prior_season_games, "
        "ef.home_last_game_same_season, ef.away_last_game_same_season "
        "FROM engineered_features ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
        "WHERE ef.feature_set_version = ? "
        "AND ((ef.home_last3_prior_season_games IS NOT NULL AND ef.home_last3_prior_season_games > 0) "
        "OR (ef.away_last3_prior_season_games IS NOT NULL AND ef.away_last3_prior_season_games > 0)) "
        "ORDER BY gc.commence_time LIMIT ?",
        (FEATURE_SET_VERSION, limit),
    ).fetchall()
    if not rows:
        print("  None found. EXPECTED for the 2025-only cohort: there is no prior season in the database "
              "yet, so no last-3/last-5 window can cross a season boundary. This section will populate "
              "naturally once 2026 data is collected and V1 features are computed for early-2026 games.")
    for r in rows:
        print(f"  game={r[0]} sport={r[1]} kickoff={r[2]}")
        print(f"    home_last3_prior_season_games={r[3]} away_last3_prior_season_games={r[4]}")
        print(f"    home_last5_prior_season_games={r[5]} away_last5_prior_season_games={r[6]}")
        print(f"    home_last_game_same_season={r[7]} away_last_game_same_season={r[8]}")


def section_5_and_6_lookahead_and_arithmetic(conn, team_index):
    print(f"\n{'=' * 70}\n5 & 6. LOOK-AHEAD SPOT CHECKS + MANUAL ARITHMETIC VERIFICATION\n{'=' * 70}")

    def pick_examples():
        examples = []
        for sport in ("nfl", "cfb"):
            rows = conn.execute(
                "SELECT ef.canonical_game_id, gc.commence_time, gc.home_team_id, gc.away_team_id "
                "FROM engineered_features ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
                "WHERE gc.sport=? AND ef.feature_set_version=? AND ef.home_games_played_before_this >= 3 "
                "ORDER BY gc.commence_time", (sport, FEATURE_SET_VERSION),
            ).fetchall()
            if rows:
                examples.append((f"{sport.upper()} early", rows[0]))
                examples.append((f"{sport.upper()} midseason", rows[len(rows) // 2]))
        return examples

    for label, (canonical_game_id, commence_time, home_id, away_id) in pick_examples():
        print(f"\n--- {label}: game={canonical_game_id} kickoff={commence_time} {home_id} vs {away_id} ---")

        home_history = get_prior_games(team_index, home_id, commence_time)
        away_history = get_prior_games(team_index, away_id, commence_time)

        print(f"  HOME ({home_id}) last-3 games actually used:")
        for g in home_history[:3]:
            leak_ok = "OK (strictly before target)" if g["commence_time"] < commence_time else "!!! LEAKAGE !!!"
            print(f"    {g['commence_time']} result={g['result']} pf={g['points_for']} "
                  f"pa={g['points_against']} -- {leak_ok}")
        print(f"  AWAY ({away_id}) last-3 games actually used:")
        for g in away_history[:3]:
            leak_ok = "OK (strictly before target)" if g["commence_time"] < commence_time else "!!! LEAKAGE !!!"
            print(f"    {g['commence_time']} result={g['result']} pf={g['points_for']} "
                  f"pa={g['points_against']} -- {leak_ok}")

        stored = conn.execute(
            "SELECT home_wins_last_3, home_point_margin_avg_last_3, home_points_for_avg_last_3, "
            "home_current_win_streak, home_current_loss_streak, home_days_since_last_game, "
            "away_days_since_last_game, rest_advantage_days "
            "FROM engineered_features WHERE canonical_game_id=? AND feature_set_version=?",
            (canonical_game_id, FEATURE_SET_VERSION),
        ).fetchone()

        manual_wins_last_3 = sum(1 for g in home_history[:3] if g["result"] == "W")
        manual_margin_avg = sum(g["margin"] for g in home_history[:3]) / len(home_history[:3])
        manual_pf_avg = sum(g["points_for"] for g in home_history[:3]) / len(home_history[:3])
        manual_win_streak = 0
        for g in home_history:
            if g["result"] == "W":
                manual_win_streak += 1
            else:
                break

        print("  ARITHMETIC CHECK (home side):")
        print(f"    wins_last_3:          stored={stored[0]}  manual={manual_wins_last_3}  "
              f"{'MATCH' if stored[0] == manual_wins_last_3 else 'MISMATCH'}")
        print(f"    point_margin_avg_3:   stored={stored[1]}  manual={manual_margin_avg}  "
              f"{'MATCH' if stored[1] == manual_margin_avg else 'MISMATCH'}")
        print(f"    points_for_avg_3:     stored={stored[2]}  manual={manual_pf_avg}  "
              f"{'MATCH' if stored[2] == manual_pf_avg else 'MISMATCH'}")
        print(f"    current_win_streak:   stored={stored[3]}  manual={manual_win_streak}  "
              f"{'MATCH' if stored[3] == manual_win_streak else 'MISMATCH'}")
        if stored[5] is not None and stored[6] is not None:
            manual_rest_adv = stored[5] - stored[6]
            print(f"    rest_advantage_days:  stored={stored[7]}  manual(home-away days_since)={manual_rest_adv}  "
                  f"{'MATCH' if stored[7] == manual_rest_adv else 'MISMATCH'}")


def section_7_slate_examples(conn, limit=3):
    print(f"\n{'=' * 70}\n7. SAME-WINDOW / STANDALONE CHECKS\n{'=' * 70}")

    print("\n--- Games with the MOST competing games (busy slate) ---")
    rows = conn.execute(
        "SELECT ef.canonical_game_id, gc.commence_time, gc.sport, "
        "ef.competing_games_same_window_same_sport, ef.competing_games_same_window_all_sports "
        "FROM engineered_features ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
        "WHERE ef.feature_set_version = ? ORDER BY ef.competing_games_same_window_all_sports DESC LIMIT ?",
        (FEATURE_SET_VERSION, limit),
    ).fetchall()
    for r in rows:
        print(f"  game={r[0]} {r[1]} sport={r[2]} same_sport={r[3]} all_sports={r[4]}")

    print("\n--- Standalone games (zero same-sport competitors) ---")
    rows2 = conn.execute(
        "SELECT ef.canonical_game_id, gc.commence_time, gc.sport, "
        "ef.competing_games_same_window_same_sport, ef.competing_games_same_window_all_sports, "
        "ef.is_standalone_same_sport, ef.is_standalone_all_sports "
        "FROM engineered_features ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
        "WHERE ef.feature_set_version = ? AND ef.is_standalone_same_sport = 1 LIMIT ?",
        (FEATURE_SET_VERSION, limit),
    ).fetchall()
    for r in rows2:
        print(f"  game={r[0]} {r[1]} sport={r[2]} same_sport={r[3]} all_sports={r[4]} "
              f"standalone_same_sport={r[5]} standalone_all_sports={r[6]}")

    print("\n--- Games where same-sport and all-sports counts DIFFER (cross-sport neighbor) ---")
    rows3 = conn.execute(
        "SELECT ef.canonical_game_id, gc.commence_time, gc.sport, "
        "ef.competing_games_same_window_same_sport, ef.competing_games_same_window_all_sports "
        "FROM engineered_features ef JOIN games_canonical gc ON ef.canonical_game_id = gc.canonical_game_id "
        "WHERE ef.feature_set_version = ? "
        "AND ef.competing_games_same_window_same_sport != ef.competing_games_same_window_all_sports LIMIT ?",
        (FEATURE_SET_VERSION, limit),
    ).fetchall()
    if not rows3:
        print("  None found -- NFL/CFB kickoff windows apparently never overlapped within +/-60min in this cohort.")
    for r in rows3:
        print(f"  game={r[0]} {r[1]} sport={r[2]} same_sport={r[3]} all_sports={r[4]}")


def section_9_anomalies(conn):
    print(f"\n{'=' * 70}\n9. ANOMALY CHECKS\n{'=' * 70}")

    dupes = conn.execute(
        "SELECT canonical_game_id, COUNT(*) c FROM engineered_features WHERE feature_set_version=? "
        "GROUP BY canonical_game_id HAVING c > 1", (FEATURE_SET_VERSION,),
    ).fetchall()
    print(f"  Duplicate (canonical_game_id, version) rows: {len(dupes)} (expect 0)")

    sample_size_violations = conn.execute(
        "SELECT COUNT(*) FROM engineered_features WHERE feature_set_version=? "
        "AND home_last3_sample_size > home_games_played_before_this", (FEATURE_SET_VERSION,),
    ).fetchone()[0]
    print(f"  Rows where last3_sample_size exceeds games_played_before_this: {sample_size_violations} (expect 0)")

    null_contradiction = conn.execute(
        "SELECT COUNT(*) FROM engineered_features WHERE feature_set_version=? "
        "AND home_games_played_before_this = 0 AND home_wins_last_3 IS NOT NULL", (FEATURE_SET_VERSION,),
    ).fetchone()[0]
    print(f"  Rows with 0 prior games but a non-NULL recency field: {null_contradiction} (expect 0)")

    print("\n  See sections 1-8 for any MISMATCH/FAIL/LEAKAGE flags -- those are the actual anomaly signal.")


def section_8_idempotency():
    print(f"\n{'=' * 70}\n8. IDEMPOTENCY CHECK (re-running the real build)\n{'=' * 70}")
    run_build()


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    fbs_allowlist = load_fbs_allowlist(2025)
    cohort = get_cohort(conn, fbs_allowlist)
    print(f"Cohort size confirmed: {len(cohort)} (expect 1033)")

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)

    section_1_coverage(conn)
    section_2_gated_fields_and_no_scoring(conn)
    section_3_season_openers(conn)
    section_4_carryover_examples(conn)
    section_5_and_6_lookahead_and_arithmetic(conn, team_index)
    section_7_slate_examples(conn)
    section_9_anomalies(conn)

    conn.close()

    section_8_idempotency()

    print(f"\n{'=' * 70}\nDone. Review every MATCH/MISMATCH, PASS/FAIL, and leakage flag above.\n{'=' * 70}")


if __name__ == "__main__":
    main()
