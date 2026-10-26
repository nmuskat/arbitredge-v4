"""
features/build_engineered_features_v1.py

Orchestration script: fetches raw data from the database, calls the pure,
already-tested functions in compute_v1.py, and writes engineered_features
rows for the approved 2025 research cohort (272 NFL + 761 CFB FBS-vs-FBS).

OPTION C SEMANTICS: a row is written only if one doesn't already exist for
(canonical_game_id, feature_set_version). Re-running this script against
unchanged inputs is a no-op on the second run -- not a duplicate row, and
not an overwrite. If the feature DEFINITION ever changes, that becomes a
new feature_set_version (e.g. 'v2'), and old version rows stay untouched
and reproducible.

HISTORICAL-HINDSIGHT CAVEAT, documented per instruction rather than solved
here: this script uses the full completed 2025 games_canonical schedule to
compute competing_games_same_window features. That's correct for a
RETROSPECTIVE backfill, where the whole season's schedule is already known
and fixed. A future LIVE RUN SLATE must NOT reuse this pattern unmodified
-- it must use only the schedule state actually known as of the
signal-time cutoff, accounting for postponements/reschedules not yet known
at that time. Flagged here so that distinction isn't silently lost later.

Usage:
    python features/build_engineered_features_v1.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.migrate import DEFAULT_DB_PATH  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from features.compute_v1 import compute_team_features, compute_slate_features  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
import sqlite3  # noqa: E402

FEATURE_SET_VERSION = "v1"
NFL_SEASON_START = "2025-08-01T00:00:00Z"
NFL_SEASON_END = "2026-02-01T00:00:00Z"
CFB_SEASON_START = "2025-08-01T00:00:00Z"
CFB_SEASON_END = "2026-02-01T00:00:00Z"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_cohort(conn, fbs_allowlist):
    """
    Explicit season_type=2 (regular season) safeguard, per instruction --
    the previous version filtered only by date range, which meant a
    preseason NFL game (August, inside the same date window as the
    regular season) could theoretically be swept into the V1 cohort if
    build_engineered_features_v1.py were ever re-run after preseason
    results existed. Checked against the LATEST game_results row per
    canonical_game_id (matching the project's established "trust the most
    recent row" pattern elsewhere), since season_type could in principle
    be corrected between polls just like any other field.
    """
    nfl = conn.execute(
        "SELECT gc.canonical_game_id, gc.sport, gc.home_team_id, gc.away_team_id, gc.commence_time "
        "FROM games_canonical gc WHERE gc.sport='nfl' "
        "AND gc.commence_time >= ? AND gc.commence_time < ? "
        "AND gc.canonical_game_id IN ("
        "  SELECT gr1.canonical_game_id FROM game_results gr1"
        "  WHERE gr1.season_type = 2"
        "  AND gr1.retrieved_at = ("
        "    SELECT MAX(gr2.retrieved_at) FROM game_results gr2"
        "    WHERE gr2.canonical_game_id = gr1.canonical_game_id"
        "  )"
        ")",
        (NFL_SEASON_START, NFL_SEASON_END),
    ).fetchall()
    cfb_raw = conn.execute(
        "SELECT gc.canonical_game_id, gc.sport, gc.home_team_id, gc.away_team_id, gc.commence_time "
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
        (CFB_SEASON_START, CFB_SEASON_END),
    ).fetchall()
    cfb = [r for r in cfb_raw if r[2] in fbs_allowlist and r[3] in fbs_allowlist]
    return nfl + cfb


def get_latest_results_by_canonical_game(conn) -> dict:
    rows = conn.execute(
        "SELECT canonical_game_id, final_score_home, final_score_away, winner_home, season_year, retrieved_at "
        "FROM game_results ORDER BY retrieved_at ASC"
    ).fetchall()
    latest = {}
    for cgid, home_score, away_score, winner_home, season_year, retrieved_at in rows:
        latest[cgid] = (home_score, away_score, winner_home, season_year)
    return latest


def get_all_games(conn) -> list:
    rows = conn.execute(
        "SELECT canonical_game_id, sport, home_team_id, away_team_id, commence_time FROM games_canonical"
    ).fetchall()
    return [
        {"canonical_game_id": r[0], "sport": r[1], "home_team_id": r[2], "away_team_id": r[3], "commence_time": r[4]}
        for r in rows
    ]


def build_team_index(all_games: list, latest_results: dict) -> dict:
    index = {}
    for g in all_games:
        result_row = latest_results.get(g["canonical_game_id"])
        if result_row is None:
            continue
        home_score, away_score, winner_home, season_year = result_row
        if home_score is None or away_score is None:
            continue

        for team_id, is_home in [(g["home_team_id"], True), (g["away_team_id"], False)]:
            points_for = home_score if is_home else away_score
            points_against = away_score if is_home else home_score
            if winner_home is None:
                outcome = "T"
            elif (winner_home == 1) == is_home:
                outcome = "W"
            else:
                outcome = "L"
            index.setdefault(team_id, []).append({
                "commence_time": g["commence_time"], "result": outcome,
                "points_for": points_for, "points_against": points_against,
                "margin": points_for - points_against, "season_year": season_year,
            })

    for team_id in index:
        index[team_id].sort(key=lambda g: g["commence_time"], reverse=True)
    return index


def get_prior_games(team_index: dict, team_id: str, before_commence_time: str) -> list:
    all_games = team_index.get(team_id, [])
    return [g for g in all_games if g["commence_time"] < before_commence_time]


def build_feature_row(team_index, all_games, canonical_game_id, sport, home_id, away_id, commence_time, latest_results):
    target_result = latest_results.get(canonical_game_id)
    target_season_year = target_result[3] if target_result else None

    home_history = get_prior_games(team_index, home_id, commence_time)
    away_history = get_prior_games(team_index, away_id, commence_time)

    home_features = compute_team_features(home_history, commence_time, target_season_year)
    away_features = compute_team_features(away_history, commence_time, target_season_year)
    slate_features = compute_slate_features(all_games, canonical_game_id, sport, commence_time)

    row = {"canonical_game_id": canonical_game_id}
    for k, v in home_features.items():
        row[f"home_{k}"] = v
    for k, v in away_features.items():
        row[f"away_{k}"] = v
    row.update(slate_features)
    return row


COLUMN_ORDER = [
    "canonical_game_id", "feature_set_version", "computed_at", "point_in_time_cutoff",
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


def write_feature_row(conn, row: dict) -> str:
    existing = conn.execute(
        "SELECT feature_id FROM engineered_features WHERE canonical_game_id = ? AND feature_set_version = ?",
        (row["canonical_game_id"], row["feature_set_version"]),
    ).fetchone()
    if existing is not None:
        return "unchanged"

    if row.get("home_games_played_before_this", 0) > 0 and row.get("away_games_played_before_this", 0) > 0:
        home_days = row.get("home_days_since_last_game")
        away_days = row.get("away_days_since_last_game")
        row["rest_advantage_days"] = (home_days - away_days) if (home_days is not None and away_days is not None) else None
    else:
        row["rest_advantage_days"] = None

    for col in COLUMN_ORDER:
        row.setdefault(col, None)

    placeholders = ", ".join(["?"] * len(COLUMN_ORDER))
    conn.execute(
        f"INSERT INTO engineered_features ({', '.join(COLUMN_ORDER)}) VALUES ({placeholders})",
        [row[c] for c in COLUMN_ORDER],
    )
    conn.commit()
    return "new"


def main():
    conn = sqlite3.connect(str(DEFAULT_DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")

    fbs_allowlist = load_fbs_allowlist(2025)
    cohort = get_cohort(conn, fbs_allowlist)
    print(f"Approved 2025 cohort: {len(cohort)} games")

    latest_results = get_latest_results_by_canonical_game(conn)
    all_games = get_all_games(conn)
    team_index = build_team_index(all_games, latest_results)

    now = _now()
    new_count = 0
    unchanged_count = 0

    for canonical_game_id, sport, home_id, away_id, commence_time in cohort:
        row = build_feature_row(
            team_index, all_games, canonical_game_id, sport, home_id, away_id, commence_time, latest_results,
        )
        row["feature_set_version"] = FEATURE_SET_VERSION
        row["computed_at"] = now
        row["point_in_time_cutoff"] = commence_time

        outcome = write_feature_row(conn, row)
        if outcome == "new":
            new_count += 1
        else:
            unchanged_count += 1

    print(f"New feature rows written: {new_count}")
    print(f"Already existed (idempotent no-op): {unchanged_count}")

    total = conn.execute(
        "SELECT COUNT(*) FROM engineered_features WHERE feature_set_version = ?", (FEATURE_SET_VERSION,)
    ).fetchone()[0]
    print(f"Total {FEATURE_SET_VERSION} rows in engineered_features: {total}")

    conn.close()


if __name__ == "__main__":
    main()
