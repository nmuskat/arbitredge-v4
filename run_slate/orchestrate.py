"""
run_slate/orchestrate.py

The complete live data-serving path: upcoming slate -> eligibility ->
cutoff=now -> point-in-time V2 computation -> structured game output.
Read-only against production data. NEVER calls persist_features_v2 or
persist_market_state_as_of -- this is a live computation path,
structurally separate from retrospective research persistence.

No Handle Intensity, no Side Preference, no Public Pressure, no bet, no
pick, no recommendation, no confidence rating anywhere in this file.
Purely factual/infrastructure output: what's knowable right now, and
what isn't.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_slate.discover_slate import discover_upcoming_slate, _now_str  # noqa: E402
from features.run_slate_eligibility import check_run_slate_eligibility, ELIGIBLE  # noqa: E402
from features.build_engineered_features_v2 import compute_features_v2  # noqa: E402
from features.build_engineered_features_v1 import get_all_games, build_team_index  # noqa: E402
from features.market_state_as_of import market_state_as_of, PostKickoffCutoffError  # noqa: E402
from reporting.checkpoint_reconstruction import get_books_for_game  # noqa: E402


def _get_regular_and_postseason_results_only(conn):
    """
    Duplicated here deliberately rather than importing from
    behavioral/run_preseason_mechanics_diagnostic.py -- that module is
    part of the Behavioral V1 milestone (explicitly frozen and out of
    scope for this one), and RUN SLATE should not depend on it even
    though the underlying query is identical. Same fix, same reasoning:
    V1's own get_latest_results_by_canonical_game() does not even SELECT
    season_type, so it structurally cannot exclude preseason results from
    recency history. Preseason games never count as recency signal.
    """
    rows = conn.execute(
        "SELECT canonical_game_id, final_score_home, final_score_away, winner_home, season_year "
        "FROM game_results WHERE season_type IN (2, 3) ORDER BY retrieved_at ASC"
    ).fetchall()
    return {cgid: (hs, aws, wh, sy) for cgid, hs, aws, wh, sy in rows}


def _build_market_state_section(conn, canonical_game_id, as_of):
    books = get_books_for_game(conn, canonical_game_id)
    per_book = {}
    for book in books:
        try:
            state = market_state_as_of(conn, canonical_game_id, as_of, book)
        except PostKickoffCutoffError as e:
            per_book[book] = {"error": str(e)}
            continue
        per_book[book] = state
    return per_book


def run_slate(conn, sport, start_date, end_date, as_of=None):
    """
    Returns a dict:
      {
        "sport": ..., "start_date": ..., "end_date": ..., "as_of": ...,
        "eligible_games": [ {structured output per game} ... ],
        "excluded_games": [ {canonical_game_id, status, reason} ... ],
      }
    Read-only. No table is ever written to.
    """
    as_of = as_of or _now_str()

    discovered = discover_upcoming_slate(conn, sport, start_date, end_date, as_of=as_of)

    eligible_games = []
    excluded_games = []

    all_games = get_all_games(conn)
    filtered_results = _get_regular_and_postseason_results_only(conn)
    team_index = build_team_index(all_games, filtered_results)

    for gid, home, away, commence_time in discovered:
        eligibility = check_run_slate_eligibility(conn, gid)
        if eligibility["status"] != ELIGIBLE:
            excluded_games.append({
                "canonical_game_id": gid, "home_team_id": home, "away_team_id": away,
                "commence_time": commence_time, "status": eligibility["status"],
                "reason": eligibility["reason"],
            })
            continue

        try:
            v2_features = compute_features_v2(
                conn, gid, as_of, team_index=team_index, all_games=all_games, latest_results=filtered_results,
            )
        except PostKickoffCutoffError as e:
            excluded_games.append({
                "canonical_game_id": gid, "home_team_id": home, "away_team_id": away,
                "commence_time": commence_time, "status": "POST_KICKOFF_CUTOFF_REJECTED",
                "reason": str(e),
            })
            continue

        market_state_section = _build_market_state_section(conn, gid, as_of)

        coverage_fields = [
            "home_wins_last_3", "away_wins_last_3", "home_point_margin_avg_last_3", "away_point_margin_avg_last_3",
            "home_current_win_streak", "away_current_win_streak", "rest_advantage_days",
            "ranking_home", "ranking_away", "tv_network",
        ]
        coverage_count = sum(1 for f in coverage_fields if v2_features.get(f) is not None)

        eligible_games.append({
            "canonical_game_id": gid,
            "home_team_id": home,
            "away_team_id": away,
            "commence_time": commence_time,
            "as_of_cutoff": as_of,
            "eligibility_status": eligibility["status"],
            "v2_feature_coverage_count": coverage_count,
            "v2_feature_coverage_total": len(coverage_fields),
            "v2_features": v2_features,
            "market_state_by_book": market_state_section,
        })

    return {
        "sport": sport, "start_date": start_date, "end_date": end_date, "as_of": as_of,
        "eligible_games": eligible_games,
        "excluded_games": excluded_games,
    }
