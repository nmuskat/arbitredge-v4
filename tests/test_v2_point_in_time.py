"""
Tests for features/market_state_as_of.py, context_state_as_of.py,
build_engineered_features_v2.py

Run with: python tests/test_v2_point_in_time.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.market_state_as_of import (  # noqa: E402
    market_state_as_of, persist_market_state_as_of, resolve_named_cutoff, PostKickoffCutoffError,
)
from features.context_state_as_of import context_state_as_of  # noqa: E402
from features.build_engineered_features_v2 import compute_features_v2, persist_features_v2  # noqa: E402
from features.build_engineered_features_v1 import (  # noqa: E402
    get_all_games, get_latest_results_by_canonical_game, build_team_index,
    build_feature_row, write_feature_row, FEATURE_SET_VERSION as V1_VERSION,
)
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_v2_point_in_time_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _make_game(conn, home="nfl_kc", away="nfl_lac", ct="2026-09-13T20:00:00Z"):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES ('nfl',?,?,?)",
        (home, away, ct),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _snap(conn, gid, book, observed_at, spread_home, price_home=-110, book_last_update=None):
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, book_last_update, spread_home, spread_away, spread_price_home, spread_price_away) "
        "VALUES (?, 'nfl', 'KC', 'LAC', '2026-09-13T20:00:00Z', ?, ?, ?, ?, ?, ?, -110)",
        (gid, book, observed_at, book_last_update, spread_home, -spread_home, price_home),
    )
    conn.commit()


def _ctx(conn, gid, observed_at, ranking_home=None, tv_network=None):
    conn.execute(
        "INSERT INTO game_context (canonical_game_id, observed_at, ranking_home, tv_network, source, retrieved_at) "
        "VALUES (?, ?, ?, ?, 'espn', ?)",
        (gid, observed_at, ranking_home, tv_network, observed_at),
    )
    conn.commit()


def _result(conn, gid, hs, aws, season_type=2, season_year=2026, retrieved_at="2026-09-14T00:00:00Z"):
    wh = 1 if hs > aws else (0 if aws > hs else None)
    conn.execute(
        "INSERT INTO game_results (canonical_game_id, observed_at, game_status, is_final, "
        "final_score_home, final_score_away, winner_home, season_year, season_type, source, retrieved_at) "
        "VALUES (?, ?, 'STATUS_FINAL', 1, ?, ?, ?, ?, ?, 'espn', ?)",
        (gid, retrieved_at, hs, aws, wh, season_year, season_type, retrieved_at),
    )
    conn.commit()


def test_returns_latest_observation_at_or_before_cutoff():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-11T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-09-12T12:00:00Z", -2.5)
    _snap(conn, gid, "draftkings", "2026-09-13T18:00:00Z", -3.0)

    state = market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    assert state["home_spread"] == -2.5
    conn.close()
    print("PASS: market_state_as_of returns the latest observation at or before the exact cutoff")


def test_observation_exactly_at_cutoff_is_included():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-13T00:00:00Z", -3.0)

    state = market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    assert state is not None and state["home_spread"] == -3.0
    conn.close()
    print("PASS: an observation timestamped exactly at the cutoff is correctly included")


def test_observation_after_cutoff_never_leaks_backward():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-11T12:00:00Z", -2.0)
    _snap(conn, gid, "draftkings", "2026-09-13T19:00:00Z", -10.0)

    state = market_state_as_of(conn, gid, "2026-09-12T00:00:00Z", "draftkings")
    assert state["home_spread"] == -2.0
    conn.close()
    print("PASS: an observation after the requested cutoff never leaks backward into the result")


def test_book_last_update_preferred_as_effective_time():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-12T00:00:00Z", -3.0, book_last_update="2026-09-13T19:00:00Z")

    state = market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    assert state is None
    conn.close()
    print("PASS: book_last_update is correctly used as the effective time for the cutoff comparison")


def test_post_kickoff_cutoff_is_hard_rejected():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    try:
        market_state_as_of(conn, gid, "2026-09-13T20:00:01Z", "draftkings")
        assert False
    except PostKickoffCutoffError:
        pass
    conn.close()
    print("PASS: a cutoff even 1 second after kickoff is hard-rejected, not merely warned about")


def test_cutoff_exactly_at_kickoff_is_allowed():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    _snap(conn, gid, "draftkings", "2026-09-13T19:00:00Z", -3.0)
    state = market_state_as_of(conn, gid, "2026-09-13T20:00:00Z", "draftkings")
    assert state is not None
    conn.close()
    print("PASS: a cutoff exactly equal to commence_time is allowed, not rejected")


def test_context_state_as_of_also_rejects_post_kickoff():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    try:
        context_state_as_of(conn, gid, "2026-09-14T00:00:00Z")
        assert False
    except PostKickoffCutoffError:
        pass
    conn.close()
    print("PASS: context_state_as_of enforces the identical post-kickoff rejection")


def test_no_observations_returns_none():
    conn = fresh_conn()
    gid = _make_game(conn)
    state = market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    assert state is None
    conn.close()
    print("PASS: zero observations for a book correctly returns None, not a fabricated value")


def test_no_observations_before_cutoff_returns_none():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-13T19:00:00Z", -3.0)
    state = market_state_as_of(conn, gid, "2026-09-11T00:00:00Z", "draftkings")
    assert state is None
    conn.close()
    print("PASS: observations existing only AFTER the cutoff correctly produce None, not a leaked value")


def test_context_missing_returns_none():
    conn = fresh_conn()
    gid = _make_game(conn)
    assert context_state_as_of(conn, gid, "2026-09-13T00:00:00Z") is None
    conn.close()
    print("PASS: missing context data correctly returns None")


def test_different_books_remain_independent():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-12T12:00:00Z", -3.0)
    _snap(conn, gid, "fanduel", "2026-09-12T12:00:00Z", -2.5)

    dk_state = market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    fd_state = market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "fanduel")
    assert dk_state["home_spread"] == -3.0
    assert fd_state["home_spread"] == -2.5
    conn.close()
    print("PASS: different books produce independent, distinct results -- never blended")


def test_book_is_required_no_default():
    import inspect
    sig = inspect.signature(market_state_as_of)
    assert sig.parameters["book"].default is inspect.Parameter.empty
    print("PASS: market_state_as_of's book parameter has no default -- every caller must choose explicitly")


def test_named_cutoff_resolution():
    commence_time = "2026-09-13T20:00:00Z"
    assert resolve_named_cutoff(commence_time, "kickoff") == commence_time
    assert resolve_named_cutoff(commence_time, "T-24h") == "2026-09-12T20:00:00.000000Z"
    assert resolve_named_cutoff(commence_time, "T-6h") == "2026-09-13T14:00:00.000000Z"
    assert resolve_named_cutoff(commence_time, "T-1h") == "2026-09-13T19:00:00.000000Z"
    print("PASS: named cutoffs (T-24h/T-6h/T-1h/kickoff) resolve to correct real timestamps")


def test_named_cutoffs_never_enter_schema_as_labels():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    resolved = resolve_named_cutoff("2026-09-13T20:00:00Z", "T-24h")
    persist_market_state_as_of(conn, gid, resolved, "draftkings")
    stored = conn.execute("SELECT point_in_time_cutoff FROM market_state_by_cutoff WHERE canonical_game_id=?",
                           (gid,)).fetchone()[0]
    assert stored == resolved
    assert "T-24h" not in stored
    conn.close()
    print("PASS: only the resolved real timestamp is ever persisted -- the symbolic label never enters the schema")


def test_invalid_named_cutoff_raises():
    try:
        resolve_named_cutoff("2026-09-13T20:00:00Z", "T-99h")
        assert False
    except ValueError:
        pass
    print("PASS: an unrecognized named cutoff is rejected explicitly")


def test_v1_completely_unaffected_by_v2_operations():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    _result(conn, gid, 24, 17)

    all_games = get_all_games(conn)
    latest_results = get_latest_results_by_canonical_game(conn)
    team_index = build_team_index(all_games, latest_results)
    v1_row = build_feature_row(team_index, all_games, gid, "nfl", "nfl_kc", "nfl_lac", "2026-09-13T20:00:00Z", latest_results)
    v1_row.update({"feature_set_version": V1_VERSION, "computed_at": "2026-09-14T00:00:00Z",
                   "point_in_time_cutoff": "2026-09-13T20:00:00Z"})
    write_feature_row(conn, v1_row)

    v1_count_before = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]
    v1_snapshot_before = conn.execute(
        "SELECT home_wins_last_3, point_in_time_cutoff FROM engineered_features WHERE canonical_game_id=?", (gid,)
    ).fetchone()

    persist_features_v2(conn, gid, "2026-09-13T20:00:00Z", require_regular_season=True)
    persist_market_state_as_of(conn, gid, "2026-09-13T20:00:00Z", "draftkings")

    v1_count_after = conn.execute("SELECT COUNT(*) FROM engineered_features").fetchone()[0]
    v1_snapshot_after = conn.execute(
        "SELECT home_wins_last_3, point_in_time_cutoff FROM engineered_features WHERE canonical_game_id=?", (gid,)
    ).fetchone()

    assert v1_count_before == v1_count_after == 1
    assert v1_snapshot_before == v1_snapshot_after
    conn.close()
    print("PASS: running V2 computations does not add, remove, or modify anything in the V1 engineered_features table")


def test_preseason_game_refused_by_default_safeguard():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-08-15T20:00:00Z")
    _result(conn, gid, 20, 13, season_type=1)

    outcome = persist_features_v2(conn, gid, "2026-08-15T20:00:00Z")
    assert outcome == "refused_not_regular_season"
    count = conn.execute("SELECT COUNT(*) FROM engineered_features_v2").fetchone()[0]
    assert count == 0
    conn.close()
    print("PASS: a preseason game (season_type=1) is refused by the default require_regular_season safeguard")


def test_preseason_allowed_only_with_explicit_opt_out():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-08-15T20:00:00Z")
    _result(conn, gid, 20, 13, season_type=1)

    outcome = persist_features_v2(conn, gid, "2026-08-15T20:00:00Z", require_regular_season=False)
    assert outcome == "new"
    count = conn.execute("SELECT COUNT(*) FROM engineered_features_v2").fetchone()[0]
    assert count == 1
    conn.close()
    print("PASS: preseason data can only enter engineered_features_v2 via an explicit, visible opt-out")


def test_no_result_yet_also_refused_by_default():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    outcome = persist_features_v2(conn, gid, "2026-09-13T20:00:00Z")
    assert outcome == "refused_not_regular_season"
    conn.close()
    print("PASS: a game with no result yet (unknown season_type) is refused by default, not assumed safe")


def test_slate_features_use_real_commence_time_not_cutoff():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-10T20:00:00Z")  # a Thursday
    _result(conn, gid, 24, 17)

    cutoff = resolve_named_cutoff("2026-09-10T20:00:00Z", "T-24h")  # falls on a Wednesday
    row = compute_features_v2(conn, gid, cutoff)
    assert row["day_of_week"] == "Thursday"
    conn.close()
    print("PASS: slate features (day_of_week) correctly use the game's real commence_time, "
          "not the point-in-time cutoff -- confirms the audit finding was fixed correctly")


def test_persist_market_state_idempotent():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-12T12:00:00Z", -3.0)

    r1 = persist_market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    r2 = persist_market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")
    assert r1 == "new" and r2 == "unchanged"
    count = conn.execute("SELECT COUNT(*) FROM market_state_by_cutoff").fetchone()[0]
    assert count == 1
    conn.close()
    print("PASS: persist_market_state_as_of is idempotent -- no duplicate rows on rerun")


def test_market_state_by_cutoff_append_only():
    conn = fresh_conn()
    gid = _make_game(conn)
    _snap(conn, gid, "draftkings", "2026-09-12T12:00:00Z", -3.0)
    persist_market_state_as_of(conn, gid, "2026-09-13T00:00:00Z", "draftkings")

    try:
        conn.execute("UPDATE market_state_by_cutoff SET home_spread = 99 WHERE canonical_game_id=?", (gid,))
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised

    try:
        conn.execute("DELETE FROM market_state_by_cutoff WHERE canonical_game_id=?", (gid,))
        conn.commit()
        raised_delete = False
    except sqlite3.IntegrityError:
        raised_delete = True
    assert raised_delete
    conn.close()
    print("PASS: market_state_by_cutoff append-only triggers block UPDATE and DELETE")


def test_engineered_features_v2_append_only():
    conn = fresh_conn()
    gid = _make_game(conn, ct="2026-09-13T20:00:00Z")
    _result(conn, gid, 24, 17)
    persist_features_v2(conn, gid, "2026-09-13T20:00:00Z")

    try:
        conn.execute("UPDATE engineered_features_v2 SET home_wins_last_3=99 WHERE canonical_game_id=?", (gid,))
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    conn.close()
    print("PASS: engineered_features_v2 append-only trigger blocks UPDATE")


if __name__ == "__main__":
    test_returns_latest_observation_at_or_before_cutoff()
    test_observation_exactly_at_cutoff_is_included()
    test_observation_after_cutoff_never_leaks_backward()
    test_book_last_update_preferred_as_effective_time()
    test_post_kickoff_cutoff_is_hard_rejected()
    test_cutoff_exactly_at_kickoff_is_allowed()
    test_context_state_as_of_also_rejects_post_kickoff()
    test_no_observations_returns_none()
    test_no_observations_before_cutoff_returns_none()
    test_context_missing_returns_none()
    test_different_books_remain_independent()
    test_book_is_required_no_default()
    test_named_cutoff_resolution()
    test_named_cutoffs_never_enter_schema_as_labels()
    test_invalid_named_cutoff_raises()
    test_v1_completely_unaffected_by_v2_operations()
    test_preseason_game_refused_by_default_safeguard()
    test_preseason_allowed_only_with_explicit_opt_out()
    test_no_result_yet_also_refused_by_default()
    test_slate_features_use_real_commence_time_not_cutoff()
    test_persist_market_state_idempotent()
    test_market_state_by_cutoff_append_only()
    test_engineered_features_v2_append_only()
    print("\nAll V2 point-in-time tests passed.")
