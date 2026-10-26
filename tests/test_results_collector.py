"""
Tests for collectors/results_collector.py

Run with: python tests/test_results_collector.py
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.results_collector import ResultsCollector  # noqa: E402
from collectors.odds_collector import OddsCollector  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_results_collector_test_workspace"


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
    """Deliberately has NO .url attribute, so results_collector's
    hasattr(response, 'url') check correctly falls back to the base URL,
    matching real requests.Response only when it actually has one."""

    def __init__(self, status_code=200, json_body=None, text=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text if text is not None else (json.dumps(json_body) if json_body is not None else "")
        self.headers = headers or {}

    def json(self):
        if self._json_body is not None:
            return self._json_body
        return json.loads(self.text)


def make_collector(conn, session, **kwargs):
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    return ResultsCollector(
        conn=conn, team_resolver=tr, game_resolver=gr,
        session=session, sleep_fn=lambda s: None, **kwargs,
    )


def espn_competitor(home_away, name, score=None, winner=None, team_id="100"):
    c = {"homeAway": home_away, "team": {"id": team_id, "displayName": name}}
    if score is not None:
        c["score"] = str(score)
    if winner is not None:
        c["winner"] = winner
    return c


def espn_event(
    event_id="401001", home_name="Kansas City Chiefs", away_name="Los Angeles Chargers",
    date="2026-09-10T20:20Z", status_name="STATUS_FINAL", completed=True,
    home_score=27, away_score=20, home_winner=True, away_winner=False,
    period=4, detail="Final", neutral_site=None, season_year=2026, season_type=2,
    week_number=1, venue_name="Arrowhead Stadium", home_team_id="100", away_team_id="101",
):
    competition = {
        "date": date,
        "competitors": [
            espn_competitor("home", home_name, home_score, home_winner, home_team_id),
            espn_competitor("away", away_name, away_score, away_winner, away_team_id),
        ],
        "status": {"period": period, "type": {"name": status_name, "completed": completed, "detail": detail}},
        "venue": {"fullName": venue_name},
    }
    if neutral_site is not None:
        competition["neutralSite"] = neutral_site
    event = {
        "id": event_id,
        "date": date,
        "competitions": [competition],
        "status": {"period": period, "type": {"name": status_name, "completed": completed, "detail": detail}},
        "season": {"year": season_year, "type": season_type},
        "week": {"number": week_number},
    }
    return event


def espn_scoreboard(events):
    return {"events": events}


# ============================================================
# 1. Normal final result, NFL
# ============================================================

def test_normal_final_result_stored():
    conn = fresh_conn()
    event = espn_event()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["results_inserted"] == 1
    row = conn.execute(
        "SELECT game_status, is_final, final_score_home, final_score_away, winner_home "
        "FROM game_results"
    ).fetchone()
    assert row == ("STATUS_FINAL", 1, 27, 20, 1)
    conn.close()
    print("PASS: normal final result stored correctly")


# ============================================================
# 2. In-progress game produces no row
# ============================================================

def test_in_progress_game_produces_no_row():
    conn = fresh_conn()
    event = espn_event(status_name="STATUS_IN_PROGRESS", completed=False, home_score=14, away_score=10)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["results_inserted"] == 0
    assert summary["non_terminal_skipped"] == 1
    row_count = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    assert row_count == 0
    conn.close()
    print("PASS: in-progress (non-terminal) game produces zero stored rows")


# ============================================================
# 3. Postponed game stored, no score forced
# ============================================================

def test_postponed_game_stored_without_scores():
    conn = fresh_conn()
    event = espn_event(status_name="STATUS_POSTPONED", completed=False, home_score=None, away_score=None,
                        home_winner=None, away_winner=None)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["results_inserted"] == 1
    row = conn.execute(
        "SELECT game_status, is_final, final_score_home, final_score_away FROM game_results"
    ).fetchone()
    assert row == ("STATUS_POSTPONED", 0, None, None)
    conn.close()
    print("PASS: postponed game stored with correct status, no score forced")


# ============================================================
# 4. Canceled game stored correctly
# ============================================================

def test_canceled_game_stored():
    conn = fresh_conn()
    event = espn_event(status_name="STATUS_CANCELED", completed=False, home_score=None, away_score=None,
                        home_winner=None, away_winner=None)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["results_inserted"] == 1
    row = conn.execute("SELECT game_status, is_final FROM game_results").fetchone()
    assert row == ("STATUS_CANCELED", 0)
    conn.close()
    print("PASS: canceled game stored correctly")


# ============================================================
# 5. Overtime correctly detected
# ============================================================

def test_overtime_detected():
    conn = fresh_conn()
    event = espn_event(period=5, detail="Final/OT", home_score=30, away_score=27)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    collector.collect("nfl")

    row = conn.execute("SELECT is_overtime, espn_status_detail FROM game_results").fetchone()
    assert row == (1, "Final/OT")
    conn.close()
    print("PASS: overtime correctly detected from period count and status detail")


# ============================================================
# 6. Neutral-site correctly captured
# ============================================================

def test_neutral_site_captured():
    conn = fresh_conn()
    event = espn_event(
        home_name="Ohio State Buckeyes", away_name="Michigan Wolverines",
        home_team_id="194", away_team_id="130", neutral_site=True,
    )
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    collector.collect("cfb")

    row = conn.execute("SELECT is_neutral_site FROM game_results").fetchone()
    assert row == (1,)
    conn.close()
    print("PASS: neutral-site flag correctly captured")


# ============================================================
# 7. Unresolved team alias skipped, doesn't block others
# ============================================================

def test_unresolved_team_alias_skipped_not_blocking():
    conn = fresh_conn()
    bad_event = espn_event(event_id="evt_bad", home_name="Miami", away_name="Ohio State Buckeyes",
                            home_team_id="999", away_team_id="194")
    good_event = espn_event(
        event_id="evt_good", home_name="Ohio State Buckeyes", away_name="Michigan Wolverines",
        home_team_id="194", away_team_id="130",
    )
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([bad_event, good_event]))
    collector = make_collector(conn, session)

    summary = collector.collect("cfb")

    assert summary["results_inserted"] == 1
    assert summary["records_skipped"] >= 1
    unresolved = conn.execute(
        "SELECT source FROM unresolved_aliases WHERE raw_text = 'Miami'"
    ).fetchone()
    assert unresolved is not None and unresolved[0] == "espn"
    conn.close()
    print("PASS: unresolved team alias is skipped and logged without blocking other games")


# ============================================================
# 8. Malformed/missing score fields skipped, not guessed
# ============================================================

def test_malformed_missing_status_skipped():
    conn = fresh_conn()
    event = espn_event()
    del event["competitions"][0]["status"]["type"]["name"]
    del event["status"]["type"]["name"]
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["results_inserted"] == 0
    assert summary["records_skipped"] == 1
    conn.close()
    print("PASS: malformed event (missing status name) is skipped, not guessed")


# ============================================================
# 9. Repeated poll of unchanged final result -> no duplicate row
#    (the explicit test required by this step)
# ============================================================

def test_same_final_result_polled_later_no_duplicate_row():
    conn = fresh_conn()
    event = espn_event()
    session1 = MagicMock()
    session1.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector1 = make_collector(conn, session1, min_legitimate_poll_interval_minutes=0)
    summary1 = collector1.collect("nfl")
    assert summary1["results_inserted"] == 1

    # Simulate polling again HOURS/DAYS later (bypass time-gate via interval=0,
    # which tests this independently of the time-gate layer) with the exact
    # same terminal result.
    session2 = MagicMock()
    session2.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector2 = make_collector(conn, session2, min_legitimate_poll_interval_minutes=0)
    summary2 = collector2.collect("nfl")

    assert summary2["results_inserted"] == 0
    assert summary2["results_unchanged_skipped"] == 1

    total_rows = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    assert total_rows == 1, "an unchanged terminal result polled later must not create a duplicate row"
    conn.close()
    print("PASS: the same final result polled hours/days later produces no duplicate row "
          "(content-level dedup, independent of the time-gate)")


# ============================================================
# 10. Genuine correction -> new row appended, old row preserved
# ============================================================

def test_genuine_correction_appends_new_row_preserves_old():
    conn = fresh_conn()
    event_v1 = espn_event(home_score=27, away_score=20, home_winner=True, away_winner=False)
    session1 = MagicMock()
    session1.request.return_value = FakeResponse(200, espn_scoreboard([event_v1]))
    collector1 = make_collector(conn, session1, min_legitimate_poll_interval_minutes=0)
    collector1.collect("nfl")

    # A rare scorekeeping correction: home score corrected from 27 to 28
    event_v2 = espn_event(home_score=28, away_score=20, home_winner=True, away_winner=False)
    session2 = MagicMock()
    session2.request.return_value = FakeResponse(200, espn_scoreboard([event_v2]))
    collector2 = make_collector(conn, session2, min_legitimate_poll_interval_minutes=0)
    summary2 = collector2.collect("nfl")

    assert summary2["results_inserted"] == 1, "a genuine result change must append a new row"

    rows = conn.execute(
        "SELECT final_score_home, retrieved_at FROM game_results ORDER BY retrieved_at"
    ).fetchall()
    assert len(rows) == 2, "both the original and corrected result must be preserved"
    assert rows[0][0] == 27, "the original (now-superseded) row must remain untouched"
    assert rows[1][0] == 28, "the corrected row is the new, most recent one"
    conn.close()
    print("PASS: a genuine result correction appends a new row and preserves the original, "
          "never overwrites")


# ============================================================
# 11. ESPN failure isolated from odds collection
# ============================================================

def test_espn_results_failure_does_not_block_odds_collection():
    conn = fresh_conn()

    results_session = MagicMock()
    results_session.request.return_value = FakeResponse(200, {"not_events": []})
    results_collector = make_collector(conn, results_session)
    results_collector.collect("nfl")  # must not raise

    results_log = conn.execute(
        "SELECT success FROM collection_log WHERE collector_name='results_collector'"
    ).fetchone()
    assert results_log[0] == 0

    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    odds_session = MagicMock()
    odds_session.request.return_value = FakeResponse(200, [{
        "id": "evt_1", "commence_time": "2099-09-10T20:20:00Z",
        "home_team": "Kansas City Chiefs", "away_team": "Los Angeles Chargers",
        "bookmakers": [{
            "key": "draftkings", "title": "DraftKings", "last_update": "2026-09-08T12:00:00Z",
            "markets": [{"key": "spreads", "outcomes": [
                {"name": "Kansas City Chiefs", "price": -150, "point": -3.0},
                {"name": "Los Angeles Chargers", "price": 130, "point": 3.0},
            ]}],
        }],
    }])
    odds_collector = OddsCollector(
        conn=conn, team_resolver=tr, game_resolver=gr, api_key="test",
        session=odds_session, sleep_fn=lambda s: None,
    )
    odds_summary = odds_collector.collect("nfl")

    assert odds_summary["records_inserted"] == 1
    odds_log = conn.execute(
        "SELECT success FROM collection_log WHERE collector_name='odds_collector'"
    ).fetchone()
    assert odds_log[0] == 1
    conn.close()
    print("PASS: an ESPN results-collector failure is isolated and does not block odds collection")


# ============================================================
# 12. Raw-response link table correctly shared across games
# ============================================================

def test_raw_response_link_shared_across_games():
    conn = fresh_conn()
    event1 = espn_event(event_id="evt_a")
    event2 = espn_event(
        event_id="evt_b", home_name="Buffalo Bills", away_name="Miami Dolphins",
        home_team_id="102", away_team_id="103",
    )
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event1, event2]))
    collector = make_collector(conn, session)

    collector.collect("nfl")

    raw_response_count = conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    assert raw_response_count == 1, "one poll should produce exactly one raw_responses row"
    distinct_links = conn.execute(
        "SELECT COUNT(DISTINCT raw_response_id) FROM game_results_raw_response_links"
    ).fetchone()[0]
    assert distinct_links == 1
    link_count = conn.execute("SELECT COUNT(*) FROM game_results_raw_response_links").fetchone()[0]
    assert link_count == 2, "both games' results should each link to the same shared raw response"
    conn.close()
    print("PASS: raw response is stored once and correctly referenced by both games' result rows")


# ============================================================
# 13. Meta-test: no rolling/derived features anywhere
# ============================================================

def test_no_derived_features_stored_anywhere():
    conn = fresh_conn()
    columns = [
        row[1] for row in conn.execute("PRAGMA table_info(game_results)").fetchall()
    ]
    forbidden_substrings = ["streak", "average", "avg", "ats", "cover", "rolling", "last3", "last5", "trend"]
    for col in columns:
        for forbidden in forbidden_substrings:
            assert forbidden not in col.lower(), (
                f"game_results must never contain a derived/rolling feature column; found {col!r}"
            )
    conn.close()
    print("PASS: game_results schema contains no rolling/derived feature or ATS-grading columns")


# ============================================================
# 14. Option A provenance: first poll creates result + 'scoreboard' link;
#     identical later poll creates ZERO new game_results rows but DOES
#     create a second raw-response link to the EXISTING result, tagged
#     'scoreboard_confirmation'.
# ============================================================

def test_first_poll_creates_result_and_scoreboard_link():
    conn = fresh_conn()
    event = espn_event()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session, min_legitimate_poll_interval_minutes=0)

    summary = collector.collect("nfl")

    assert summary["results_inserted"] == 1
    result_id = conn.execute("SELECT result_id FROM game_results").fetchone()[0]
    links = conn.execute(
        "SELECT role, raw_response_id FROM game_results_raw_response_links WHERE result_id = ?",
        (result_id,),
    ).fetchall()
    assert len(links) == 1
    assert links[0][0] == "scoreboard"
    conn.close()
    print("PASS: first final poll creates exactly one game_results row with one "
          "'scoreboard' raw-response link")


def test_identical_later_poll_links_confirmation_no_new_result_row():
    conn = fresh_conn()
    event = espn_event()

    session1 = MagicMock()
    session1.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector1 = make_collector(conn, session1, min_legitimate_poll_interval_minutes=0)
    summary1 = collector1.collect("nfl")
    assert summary1["results_inserted"] == 1
    result_id = conn.execute("SELECT result_id FROM game_results").fetchone()[0]

    # Second, identical poll -- new HTTP request, new raw_responses row,
    # but the terminal result itself is unchanged.
    session2 = MagicMock()
    session2.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector2 = make_collector(conn, session2, min_legitimate_poll_interval_minutes=0)
    summary2 = collector2.collect("nfl")

    assert summary2["results_inserted"] == 0, "no new game_results row for an unchanged result"
    assert summary2["results_unchanged_skipped"] == 1
    assert summary2["duplicate_observations_linked"] == 1

    # Exactly one game_results row must still exist, unchanged
    result_count = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    assert result_count == 1

    # Two distinct raw_responses rows must exist (two real HTTP requests)
    raw_response_count = conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    assert raw_response_count == 2

    # The SAME result_id must now have TWO links: the original 'scoreboard'
    # link and a new 'scoreboard_confirmation' link, pointing at two
    # DIFFERENT raw_response_id values.
    links = conn.execute(
        "SELECT role, raw_response_id FROM game_results_raw_response_links "
        "WHERE result_id = ? ORDER BY role",
        (result_id,),
    ).fetchall()
    assert len(links) == 2
    roles = {r[0] for r in links}
    assert roles == {"scoreboard", "scoreboard_confirmation"}
    raw_response_ids = {r[1] for r in links}
    assert len(raw_response_ids) == 2, "the two links must reference two distinct raw_responses rows"

    conn.close()
    print("PASS: an identical later poll creates zero new game_results rows, but DOES "
          "create a second raw-response link (role='scoreboard_confirmation') to the "
          "existing result row, proving the same result was independently re-observed")


if __name__ == "__main__":
    test_normal_final_result_stored()
    test_in_progress_game_produces_no_row()
    test_postponed_game_stored_without_scores()
    test_canceled_game_stored()
    test_overtime_detected()
    test_neutral_site_captured()
    test_unresolved_team_alias_skipped_not_blocking()
    test_malformed_missing_status_skipped()
    test_same_final_result_polled_later_no_duplicate_row()
    test_genuine_correction_appends_new_row_preserves_old()
    test_espn_results_failure_does_not_block_odds_collection()
    test_raw_response_link_shared_across_games()
    test_no_derived_features_stored_anywhere()
    test_first_poll_creates_result_and_scoreboard_link()
    test_identical_later_poll_links_confirmation_no_new_result_row()
    print("\nAll results_collector tests passed.")
