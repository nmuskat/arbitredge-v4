"""
behavioral/run_preseason_mechanics_diagnostic.py

Scores the real NFL preseason slate with HandleIntensityV1 and
RecreationalSidePreferenceV1 -- MECHANICS VALIDATION ONLY. Confirms the
computation runs correctly against real data shape (real coverage gaps,
real slate composition), never a validation or calibration of the scores
themselves, and never a source of tuning for any future weight.

Uses ONLY the active primary (book-agnostic) models -- enrichment is
explicitly inactive (ENRICHMENT_ACTIVE=False in both modules) and is
never invoked here.

Read-only: opens the production database to READ engineered features,
writes nothing anywhere.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from behavioral.handle_intensity_v1 import compute_handle_intensity_v1  # noqa: E402
from behavioral.side_preference_v1 import compute_side_preference_v1  # noqa: E402
from features.compute_v1 import compute_team_features, compute_slate_features  # noqa: E402
from features.build_engineered_features_v1 import get_all_games, build_team_index, get_prior_games  # noqa: E402
import sqlite3  # noqa: E402


def get_regular_and_postseason_results_only(conn):
    """
    V1's own get_latest_results_by_canonical_game() does not even SELECT
    season_type -- it structurally cannot distinguish preseason results
    from regular/postseason ones, so team history built from it pools
    every game type indiscriminately. V1 itself stays completely
    untouched here (this is a NEW function, not a modification) -- this
    exists specifically so the behavioral layer's call to build_team_index
    receives history EXCLUDING preseason results (season_type=1), while
    still allowing regular season (2) and postseason (3) to freely carry
    forward across year boundaries exactly as V1 was already designed
    and approved to do. Preseason games never count as recency signal,
    for any target game -- not as a source of prior form for others, and
    not pooled in when the target itself is a preseason game.
    """
    rows = conn.execute(
        "SELECT canonical_game_id, final_score_home, final_score_away, winner_home, season_year, retrieved_at "
        "FROM game_results WHERE season_type IN (2, 3) ORDER BY retrieved_at ASC"
    ).fetchall()
    latest = {}
    for cgid, home_score, away_score, winner_home, season_year, retrieved_at in rows:
        latest[cgid] = (home_score, away_score, winner_home, season_year)
    return latest

DIAGNOSTIC_BANNER = (
    "EXPERIMENTAL / UNCALIBRATED \u2014 PRESEASON MECHANICS DIAGNOSTIC ONLY \u2014 "
    "NO TUNING FROM THESE RESULTS, NOT A VALIDATION OF EITHER MODEL"
)


def print_banner():
    line = "!" * len(DIAGNOSTIC_BANNER)
    print(f"\n{line}\n{DIAGNOSTIC_BANNER}\n{line}\n")


def get_preseason_slate(conn, start_date, end_date):
    start = f"{start_date}T00:00:00Z"
    end = f"{end_date}T23:59:59Z"
    return conn.execute(
        "SELECT DISTINCT gc.canonical_game_id, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc JOIN market_snapshots ms ON gc.canonical_game_id = ms.canonical_game_id "
        "WHERE ms.sport = 'nfl_preseason' AND gc.commence_time >= ? AND gc.commence_time <= ? "
        "ORDER BY gc.commence_time",
        (start, end),
    ).fetchall()


def get_context_row(conn, canonical_game_id, commence_time):
    row = conn.execute(
        "SELECT ranking_home, ranking_away, tv_network FROM game_context "
        "WHERE canonical_game_id = ? AND observed_at < ? ORDER BY observed_at DESC LIMIT 1",
        (canonical_game_id, commence_time),
    ).fetchone()
    if row is None:
        return {}
    return {"ranking_home": row[0], "ranking_away": row[1], "tv_network": row[2]}


def build_games_for_diagnostic(conn, slate_games):
    all_games = get_all_games(conn)
    latest_results = get_regular_and_postseason_results_only(conn)  # preseason EXCLUDED, see docstring above
    team_index = build_team_index(all_games, latest_results)

    games = []
    for gid, home, away, commence_time in slate_games:
        home_history = get_prior_games(team_index, home, commence_time)
        away_history = get_prior_games(team_index, away, commence_time)
        home_features = compute_team_features(home_history, commence_time, target_season_year=None)
        away_features = compute_team_features(away_history, commence_time, target_season_year=None)
        slate_features = compute_slate_features(all_games, gid, "nfl_preseason", commence_time)
        context = get_context_row(conn, gid, commence_time)

        row = {"canonical_game_id": gid, "_home_id": home, "_away_id": away, "_commence_time": commence_time}
        for k, v in home_features.items():
            row[f"home_{k}"] = v
        for k, v in away_features.items():
            row[f"away_{k}"] = v
        row.update(slate_features)
        row["_context"] = context

        if row.get("home_days_since_last_game") is not None and row.get("away_days_since_last_game") is not None:
            row["rest_advantage_days"] = row["home_days_since_last_game"] - row["away_days_since_last_game"]
        else:
            row["rest_advantage_days"] = None

        games.append(row)
    return games


def print_handle_intensity_report(games, hi_results):
    print(f"\n{'=' * 70}\nHANDLE INTENSITY V1 -- PRESEASON SLATE ({len(games)} games)\n{'=' * 70}")
    ranked = sorted(hi_results, key=lambda r: (r["percentile_rank"] is None, r["percentile_rank"]), reverse=True)
    for r in ranked:
        g = next(x for x in games if x["canonical_game_id"] == r["canonical_game_id"])
        print(f"\n  Game {r['canonical_game_id']}: {g['_home_id']} vs {g['_away_id']} ({g['_commence_time']})")
        print(f"    percentile_rank={r['percentile_rank']} normalized_score={r['normalized_score']:.3f} "
              f"raw_score={r['raw_score']:.3f} coverage={r['coverage_count']}/6 ({r['coverage_fraction']:.0%})")
        print("    Component contributions:")
        for feature, contribution in r["component_contributions"].items():
            print(f"      {feature}: {contribution:+.3f}")


def print_side_preference_report(games, sp_results):
    print(f"\n{'=' * 70}\nRECREATIONAL SIDE PREFERENCE V1 -- PRESEASON SLATE ({len(games)} games)\n{'=' * 70}")
    print(f"  Direction convention: {sp_results[0]['direction_convention'] if sp_results else 'n/a'}")
    for r in sp_results:
        g = next(x for x in games if x["canonical_game_id"] == r["canonical_game_id"])
        print(f"\n  Game {r['canonical_game_id']}: {g['_home_id']} vs {g['_away_id']} ({g['_commence_time']})")
        print(f"    direction={r['direction']} normalized_lean_score={r['normalized_lean_score']:.3f} "
              f"raw_lean_score={r['raw_lean_score']:.3f} coverage={r['coverage_count']}/6 ({r['coverage_fraction']:.0%})")
        print("    Component contributions:")
        for feature, contribution in r["component_contributions"].items():
            print(f"      {feature}: {contribution:+.3f}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Preseason mechanics diagnostic for HandleIntensityV1/SidePreferenceV1")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD, inclusive, UTC")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD, inclusive, UTC")
    args = parser.parse_args()

    print_banner()
    print("Scoring the real NFL preseason slate with the ACTIVE primary models only "
          "(enrichment is inactive and not invoked here). This exercises computation code, "
          "verifies rankings/orderings are mechanically sensible, and verifies coverage/missing-data "
          "behavior against real data. It validates NOTHING about real recreational behavior.")

    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    slate_games = get_preseason_slate(conn, args.start_date, args.end_date)
    print(f"\nPreseason games found in range {args.start_date} to {args.end_date}: {len(slate_games)}")

    if not slate_games:
        print("No preseason games with market data found in this range.")
        conn.close()
        print_banner()
        return

    games = build_games_for_diagnostic(conn, slate_games)
    conn.close()

    hi_results = compute_handle_intensity_v1(games)
    sp_results = compute_side_preference_v1(games)

    print_handle_intensity_report(games, hi_results)
    print_side_preference_report(games, sp_results)

    print_banner()
    print("Reminder: nothing above may be used to tune any weight, threshold, or coefficient. "
          "These are mechanics-validation results against preseason data only.")
    print_banner()


if __name__ == "__main__":
    main()
