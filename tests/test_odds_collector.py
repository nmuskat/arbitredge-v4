"""
Tests for collectors/odds_collector.py

Run with: python tests/test_odds_collector.py
"""

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.odds_collector import OddsCollector  # noqa: E402
from db.migrate import run_migration  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_odds_collector_test_workspace"


def fresh_conn() -> sqlite3.Connection:
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text if text is not None else (json.dumps(json_body) if json_body is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._json_body is not None:
            return self._json_body
        return json.loads(self.text)


def future_iso(hours_from_now: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).strftime("%Y-%m-%dT%H:%M:%SZ")


def past_iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_collector(conn, session, dry_run=False, api_key="test-key-not-real", **kwargs):
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    return OddsCollector(
        conn=conn, team_resolver=tr, game_resolver=gr, api_key=api_key,
        session=session, sleep_fn=lambda s: None, dry_run=dry_run, **kwargs,
    )


def sample_game(
    game_id="evt_kc_lac_001",
    home="Kansas City Chiefs",
    away="Los Angeles Chargers",
    commence=None,
    bookmakers=None,
):
    return {
        "id": game_id,
        "sport_key": "americanfootball_nfl",
        "commence_time": commence or future_iso(48),
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers if bookmakers is not None else [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "last_update": "2026-09-08T12:00:00Z",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": home, "price": -150, "point": -3.0},
                            {"name": away, "price": 130, "point": 3.0},
                        ],
                    }
                ],
            },
        ],
    }


# ============================================================
# 1. Normal multi-game/multi-book response
# ============================================================

def test_normal_multi_game_multi_book_response():
    conn = fresh_conn()
    game1 = sample_game(
        game_id="evt_001",
        bookmakers=[
            {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
             "markets": [{"key": "spreads", "outcomes": [
                 {"name": "Kansas City Chiefs", "price": -150, "point": -3.0},
                 {"name": "Los Angeles Chargers", "price": 130, "point": 3.0}]}]},
            {"key": "fanduel", "title": "FanDuel", "last_update": "2026-09-08T12:01:00Z",
             "markets": [{"key": "spreads", "outcomes": [
                 {"name": "Kansas City Chiefs", "price": -145, "point": -2.5},
                 {"name": "Los Angeles Chargers", "price": 125, "point": 2.5}]}]},
        ],
    )
    game2 = sample_game(
        game_id="evt_002", home="Buffalo Bills", away="Miami Dolphins",
        bookmakers=[
            {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:02:00Z",
             "markets": [{"key": "spreads", "outcomes": [
                 {"name": "Buffalo Bills", "price": -200, "point": -5.5},
                 {"name": "Miami Dolphins", "price": 170, "point": 5.5}]}]},
        ],
    )
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game1, game2])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["games_returned"] == 2
    assert summary["records_inserted"] == 3  # 2 books on game1 + 1 book on game2
    assert summary["bookmakers_seen"] == {"draftkings", "fanduel"}
    snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 3
    conn.close()
    print("PASS: normal multi-game/multi-book response inserts the correct number of rows")


# ============================================================
# 2. Both sides of spread preserved correctly
# ============================================================

def test_both_sides_preserved_correctly():
    conn = fresh_conn()
    game = sample_game()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    collector.collect("nfl")

    row = conn.execute(
        "SELECT spread_home, spread_away, spread_price_home, spread_price_away "
        "FROM market_snapshots"
    ).fetchone()
    assert row == (-3.0, 3.0, -150.0, 130.0), f"both sides must be stored as reported, got {row}"
    conn.close()
    print("PASS: both sides of the spread (points and prices) are preserved exactly as reported")


# ============================================================
# 3. Multiple books for same game
# ============================================================

def test_multiple_books_same_game():
    conn = fresh_conn()
    game = sample_game(bookmakers=[
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -150, "point": -3.0},
             {"name": "Los Angeles Chargers", "price": 130, "point": 3.0}]}]},
        {"key": "fanduel", "title": "FanDuel", "last_update": "2026-09-08T12:01:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -140, "point": -2.5},
             {"name": "Los Angeles Chargers", "price": 120, "point": 2.5}]}]},
        {"key": "betmgm", "title": "BetMGM", "last_update": "2026-09-08T12:02:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -155, "point": -3.0},
             {"name": "Los Angeles Chargers", "price": 135, "point": 3.0}]}]},
    ])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 3
    books = [r[0] for r in conn.execute("SELECT book FROM market_snapshots ORDER BY book").fetchall()]
    assert books == ["betmgm", "draftkings", "fanduel"]
    # All three rows must reference the same canonical_game_id
    canonical_ids = conn.execute("SELECT DISTINCT canonical_game_id FROM market_snapshots").fetchall()
    assert len(canonical_ids) == 1
    conn.close()
    print("PASS: multiple books for the same game all preserved under the same canonical game")


# ============================================================
# 4. Canonical NFL identity
# ============================================================

def test_canonical_nfl_identity_resolved():
    conn = fresh_conn()
    game = sample_game(home="Kansas City Chiefs", away="Los Angeles Chargers")
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    collector.collect("nfl")

    row = conn.execute(
        "SELECT home_team_id, away_team_id FROM games_canonical"
    ).fetchone()
    assert row == ("nfl_kc", "nfl_lac")
    conn.close()
    print("PASS: NFL team names resolve to correct canonical team IDs")


# ============================================================
# 5. Unresolved CFB alias -> skipped/logged, not guessed
# ============================================================

def test_unresolved_cfb_alias_skipped_not_guessed():
    conn = fresh_conn()
    game = sample_game(
        game_id="evt_cfb_001", home="Miami", away="Ohio State",  # "Miami" deliberately ambiguous
        commence=future_iso(48),
    )
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    summary = collector.collect("cfb")

    assert summary["records_inserted"] == 0
    assert summary["records_skipped"] >= 1
    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert canonical_count == 0, "no canonical game should be created for an unresolved team"
    unresolved_row = conn.execute(
        "SELECT reason FROM unresolved_aliases WHERE raw_text = 'Miami'"
    ).fetchone()
    assert unresolved_row is not None, "the ambiguous alias must be logged for later review"
    conn.close()
    print("PASS: unresolved CFB team alias is skipped and logged, never guessed")


# ============================================================
# 6. Malformed price
# ============================================================

def test_malformed_price_is_skipped():
    conn = fresh_conn()
    game = sample_game(bookmakers=[
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": "not-a-number", "point": -3.0},
             {"name": "Los Angeles Chargers", "price": 130, "point": 3.0}]}]},
    ])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 0
    assert summary["records_skipped"] == 1
    snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 0
    conn.close()
    print("PASS: malformed price is rejected and skipped, not inserted or guessed")


# ============================================================
# 7. Malformed/missing point
# ============================================================

def test_malformed_missing_point_is_skipped():
    conn = fresh_conn()
    game = sample_game(bookmakers=[
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -150},  # missing "point"
             {"name": "Los Angeles Chargers", "price": 130, "point": 3.0}]}]},
    ])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 0
    assert summary["records_skipped"] == 1
    conn.close()
    print("PASS: missing point is rejected and skipped")


# ============================================================
# 8. Unexpected outcome name
# ============================================================

def test_unexpected_outcome_name_is_skipped():
    conn = fresh_conn()
    game = sample_game(bookmakers=[
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -150, "point": -3.0},
             {"name": "Some Other Team Entirely", "price": 130, "point": 3.0}]}]},
    ])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 0
    assert summary["records_skipped"] == 1
    conn.close()
    print("PASS: an outcome name that doesn't match home/away team is rejected, not guessed")


def test_both_outcomes_same_team_is_skipped():
    conn = fresh_conn()
    game = sample_game(bookmakers=[
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Kansas City Chiefs", "price": -150, "point": -3.0},
             {"name": "Kansas City Chiefs", "price": 130, "point": 3.0}]}]},
    ])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 0
    assert summary["records_skipped"] == 1
    conn.close()
    print("PASS: both outcomes referencing the same team is rejected")


# ============================================================
# 9. Already-started game
# ============================================================

def test_already_started_game_is_excluded_but_not_destructive():
    conn = fresh_conn()
    # First: collect a legitimate pregame observation
    game_future = sample_game(game_id="evt_started_001", commence=future_iso(2))
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game_future])
    collector = make_collector(conn, session, min_legitimate_poll_interval_minutes=0)
    collector.collect("nfl")
    pregame_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert pregame_count == 1

    # Second poll: same game has now started. Use interval=0 to bypass the
    # too-soon guard for this test's purpose (the guard itself is tested
    # separately below) -- it is DB-backed via collection_log, not reset by
    # creating a new collector instance, which is the correct/intended
    # behavior for surviving process restarts.
    game_started = sample_game(game_id="evt_started_001", commence=past_iso(1))
    session2 = MagicMock()
    session2.request.return_value = FakeResponse(200, [game_started])
    collector2 = make_collector(conn, session2, min_legitimate_poll_interval_minutes=0)
    summary2 = collector2.collect("nfl")

    assert summary2["already_started_skipped"] == 1
    assert summary2["records_inserted"] == 0
    # Prior pregame row must still exist, untouched
    post_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert post_count == 1, "already-started filtering must never delete earlier pregame observations"
    conn.close()
    print("PASS: already-started games are excluded from new collection without touching prior pregame data")


# ============================================================
# 10. Unchanged odds observed in separate legitimate polls
# ============================================================

def test_unchanged_odds_in_separate_legitimate_polls_both_preserved():
    conn = fresh_conn()
    game = sample_game()
    session1 = MagicMock()
    session1.request.return_value = FakeResponse(200, [game])
    collector1 = make_collector(conn, session1, min_legitimate_poll_interval_minutes=0)
    summary1 = collector1.collect("nfl")
    assert summary1["records_inserted"] == 1

    # Simulate a legitimate LATER scheduled poll (min interval set to 0 so
    # the too-soon guard doesn't interfere with this test's intent) with
    # identical odds -- both observations are real information and must
    # both be preserved.
    session2 = MagicMock()
    session2.request.return_value = FakeResponse(200, [game])
    collector2 = make_collector(conn, session2, min_legitimate_poll_interval_minutes=0)
    summary2 = collector2.collect("nfl")
    assert summary2["records_inserted"] == 1

    total = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert total == 2, "two legitimate scheduled polls with unchanged odds must both be preserved"
    conn.close()
    print("PASS: unchanged odds across two legitimate scheduled polls are both preserved as real observations")


# ============================================================
# 11. Accidental duplicate rerun / idempotency behavior
# ============================================================

def test_accidental_immediate_rerun_is_skipped_before_hitting_api():
    conn = fresh_conn()
    game = sample_game()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session, min_legitimate_poll_interval_minutes=5)

    summary1 = collector.collect("nfl")
    assert summary1["records_inserted"] == 1
    calls_after_first = session.request.call_count

    # Immediately call again with the SAME collector instance (simulating an
    # accidental rerun/retry within the same process)
    summary2 = collector.collect("nfl")

    assert summary2["skipped_too_soon"] is True
    assert summary2["records_inserted"] == 0
    assert session.request.call_count == calls_after_first, (
        "an accidental immediate rerun must be blocked BEFORE making another "
        "HTTP request, protecting API credits"
    )
    total_snapshots = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert total_snapshots == 1, "no duplicate row should be created"
    conn.close()
    print("PASS: an accidental immediate rerun is detected and skipped before spending an API credit, "
          "with no duplicate row created")


# ============================================================
# 12. Raw-response FK integrity
# ============================================================

def test_raw_response_fk_integrity():
    conn = fresh_conn()
    game1 = sample_game(game_id="evt_a")
    game2 = sample_game(game_id="evt_b", home="Buffalo Bills", away="Miami Dolphins")
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game1, game2])
    collector = make_collector(conn, session)

    collector.collect("nfl")

    raw_response_count = conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    assert raw_response_count == 1, "one HTTP request should produce exactly one raw_responses row"
    distinct_refs = conn.execute(
        "SELECT COUNT(DISTINCT raw_response_id) FROM market_snapshots"
    ).fetchone()[0]
    assert distinct_refs == 1
    stored_url = conn.execute("SELECT request_url FROM raw_responses").fetchone()[0]
    assert "apiKey" not in stored_url, "the stored raw_responses URL must never contain the API key"
    conn.close()
    print("PASS: raw response is stored once and correctly referenced by all rows from that poll; no API key stored")


# ============================================================
# 13. API usage-header extraction
# ============================================================

def test_api_usage_header_extraction():
    conn = fresh_conn()
    game = sample_game()
    session = MagicMock()
    session.request.return_value = FakeResponse(
        200, [game],
        headers={"x-requests-remaining": "487", "x-requests-used": "13", "x-requests-last": "1"},
    )
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["usage_info"] == {
        "x-requests-remaining": "487", "x-requests-used": "13", "x-requests-last": "1",
    }
    conn.close()
    print("PASS: API usage/credit headers are correctly extracted and surfaced")


# ============================================================
# 14. API key absent from stored request URL/logs
# ============================================================

def test_api_key_never_stored_anywhere():
    conn = fresh_conn()
    game = sample_game()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session, api_key="THIS-IS-THE-SECRET-KEY-999")

    collector.collect("nfl")

    raw_url = conn.execute("SELECT request_url FROM raw_responses").fetchone()[0]
    assert "THIS-IS-THE-SECRET-KEY-999" not in raw_url
    log_rows = conn.execute("SELECT error_detail FROM collection_log").fetchall()
    for (detail,) in log_rows:
        assert detail is None or "THIS-IS-THE-SECRET-KEY-999" not in detail
    conn.close()
    print("PASS: API key never appears in stored raw_responses URL or collection_log")


# ============================================================
# 15. Partial success: one bad record, several good records
# ============================================================

def test_partial_success_one_bad_several_good():
    conn = fresh_conn()
    good1 = sample_game(game_id="evt_good_1")
    good2 = sample_game(game_id="evt_good_2", home="Buffalo Bills", away="Miami Dolphins")
    bad = sample_game(game_id="evt_bad", home="Green Bay Packers", away="Chicago Bears", bookmakers=[
        {"key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
         "markets": [{"key": "spreads", "outcomes": [
             {"name": "Green Bay Packers", "price": "garbage", "point": -3.0},
             {"name": "Chicago Bears", "price": 130, "point": 3.0}]}]},
    ])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [good1, bad, good2])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 2
    assert summary["records_skipped"] == 1
    assert summary["games_returned"] == 3
    snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 2
    conn.close()
    print("PASS: one bad record among several good ones is skipped without blocking the good records")


# ============================================================
# Dry-run mode
# ============================================================

def test_dry_run_does_not_write_market_snapshots_but_still_resolves_identity():
    conn = fresh_conn()
    game = sample_game()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session, dry_run=True)

    summary = collector.collect("nfl")

    assert summary["dry_run"] is True
    assert len(summary["dry_run_would_insert"]) == 1
    snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 0, "dry run must not write market_snapshots"
    # Raw response IS still stored -- inspecting real API shape is the point of dry run
    raw_count = conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    assert raw_count == 1
    # Identity resolution still happened (canonical game exists)
    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert canonical_count == 1
    conn.close()
    print("PASS: dry run resolves identity and stores the raw response for inspection, "
          "but writes zero market_snapshots rows")


def test_dry_run_still_surfaces_unresolved_cfb_aliases_for_discovery():
    conn = fresh_conn()
    game = sample_game(home="Miami", away="Ohio State")  # ambiguous, deliberately unresolved
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [game])
    collector = make_collector(conn, session, dry_run=True)

    collector.collect("cfb")

    unresolved = conn.execute("SELECT raw_text FROM unresolved_aliases").fetchall()
    assert ("Miami",) in unresolved
    print("PASS: dry run doubles as a discovery pass, surfacing unresolved aliases without writing data")
    conn.close()


def test_preseason_uses_correct_url_and_identity_scope_but_distinct_market_tag():
    """
    Confirms the exact fix: collect('nfl_preseason') must (1) request the
    americanfootball_nfl_preseason sport key, (2) resolve team/game
    identity against the ordinary 'nfl' universe (reusing existing NFL
    team aliases -- there are no 'nfl_preseason'-scoped aliases and there
    shouldn't be), and (3) still tag the resulting market_snapshots rows
    with sport='nfl_preseason', not 'nfl' -- so the two remain explicitly
    distinguishable at the raw observation layer while sharing the same
    canonical team/game identity.
    """
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, [sample_game()])
    collector = make_collector(conn, session)

    summary = collector.collect("nfl_preseason")

    called_with_preseason_key = any(
        "americanfootball_nfl_preseason" in str(c) for c in session.request.call_args_list
    )
    assert called_with_preseason_key, "collect('nfl_preseason') must request the americanfootball_nfl_preseason URL"
    assert summary["records_inserted"] == 1

    row = conn.execute(
        "SELECT sport FROM market_snapshots WHERE canonical_game_id = "
        "(SELECT canonical_game_id FROM games_canonical LIMIT 1)"
    ).fetchone()
    assert row[0] == "nfl_preseason", "market_snapshots must preserve the explicit preseason tag"

    canonical_sport = conn.execute("SELECT sport FROM games_canonical LIMIT 1").fetchone()[0]
    assert canonical_sport == "nfl", (
        "games_canonical/team identity must stay 'nfl' -- preseason and regular season "
        "share the same team universe, identity is untouched by this change"
    )
    conn.close()
    print("PASS: preseason collection requests the correct URL, resolves identity against the "
          "shared 'nfl' team universe, and still tags market_snapshots with the explicit "
          "'nfl_preseason' marker -- confirming identity and market classification are properly decoupled")


def test_401_unauthorized_correctly_classified_and_logged():
    """
    Focused regression reproducing the real production incident: a 401
    from the-odds-api must be classified as NonRetryableRequestError (not
    silently swallowed, not misclassified as retryable), logged to
    collection_log with success=0 and a real error_type/error_detail, not
    retried, and must never write any market_snapshots rows.
    """
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(401, {"message": "Invalid API key"})
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 0, "a 401 must never result in any inserted market_snapshots row"

    log_row = conn.execute(
        "SELECT success, error_type, error_detail FROM collection_log "
        "WHERE collector_name='odds_collector' ORDER BY log_id DESC LIMIT 1"
    ).fetchone()
    success, error_type, error_detail = log_row
    assert success == 0
    assert error_type == "NonRetryableRequestError"
    assert "401" in error_detail

    assert session.request.call_count == 1, "a 401 (client auth error) must be treated as non-retryable, not retried"

    snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert snapshot_count == 0
    conn.close()
    print("PASS: a 401 is correctly classified as NonRetryableRequestError, logged with real error detail, "
          "not retried, and results in zero market_snapshots rows -- reproduces the real production incident")


def test_missing_api_key_raises_loudly_not_silent_empty_string():
    """
    Direct regression for the root cause of the August 14 2026 incident:
    with no explicit api_key AND no THE_ODDS_API_KEY environment variable,
    construction must raise immediately and clearly -- never silently
    fall back to an empty string that would produce a confusing 401 from
    the remote API instead of an obvious local error.
    """
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    saved = os.environ.pop("THE_ODDS_API_KEY", None)
    try:
        try:
            OddsCollector(conn=conn, team_resolver=tr, game_resolver=gr, session=MagicMock())
            assert False, "must raise when no key is available from any source"
        except RuntimeError as e:
            assert "THE_ODDS_API_KEY" in str(e)
    finally:
        if saved is not None:
            os.environ["THE_ODDS_API_KEY"] = saved
    conn.close()
    print("PASS: a missing API key raises a clear RuntimeError at construction time, "
          "never silently degrading to an empty apiKey that would produce a confusing remote 401")


if __name__ == "__main__":
    test_normal_multi_game_multi_book_response()
    test_both_sides_preserved_correctly()
    test_multiple_books_same_game()
    test_canonical_nfl_identity_resolved()
    test_unresolved_cfb_alias_skipped_not_guessed()
    test_malformed_price_is_skipped()
    test_malformed_missing_point_is_skipped()
    test_unexpected_outcome_name_is_skipped()
    test_both_outcomes_same_team_is_skipped()
    test_already_started_game_is_excluded_but_not_destructive()
    test_unchanged_odds_in_separate_legitimate_polls_both_preserved()
    test_accidental_immediate_rerun_is_skipped_before_hitting_api()
    test_raw_response_fk_integrity()
    test_api_usage_header_extraction()
    test_api_key_never_stored_anywhere()
    test_partial_success_one_bad_several_good()
    test_dry_run_does_not_write_market_snapshots_but_still_resolves_identity()
    test_dry_run_still_surfaces_unresolved_cfb_aliases_for_discovery()
    test_preseason_uses_correct_url_and_identity_scope_but_distinct_market_tag()
    test_401_unauthorized_correctly_classified_and_logged()
    test_missing_api_key_raises_loudly_not_silent_empty_string()
    print("\nAll odds_collector tests passed.")
