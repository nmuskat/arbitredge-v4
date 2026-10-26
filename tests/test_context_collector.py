"""
Tests for collectors/context_collector.py

Run with: python tests/test_context_collector.py
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.context_collector import ContextCollector  # noqa: E402
from collectors.odds_collector import OddsCollector  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_context_collector_test_workspace"


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


def make_collector(conn, session, **kwargs):
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    return ContextCollector(
        conn=conn, team_resolver=tr, game_resolver=gr,
        session=session, sleep_fn=lambda s: None, **kwargs,
    )


def espn_competitor(home_away, name, record_summary="3-1", team_id="100"):
    comp = {
        "homeAway": home_away,
        "team": {"id": team_id, "displayName": name},
    }
    if record_summary is not None:
        comp["records"] = [{"type": "total", "summary": record_summary}]
    return comp


def espn_event(
    event_id="401001", home_name="Kansas City Chiefs", away_name="Los Angeles Chargers",
    date="2026-09-10T20:20Z", broadcasts=None, home_team_id="100", away_team_id="101",
    home_record="3-1", away_record="2-2",
):
    competition = {
        "date": date,
        "competitors": [
            espn_competitor("home", home_name, home_record, home_team_id),
            espn_competitor("away", away_name, away_record, away_team_id),
        ],
    }
    if broadcasts is not None:
        competition["broadcasts"] = broadcasts
    return {"id": event_id, "date": date, "competitions": [competition]}


def espn_scoreboard(events):
    return {"events": events}


def espn_rankings(ranks):
    """ranks: list of (espn_team_id, rank_number)"""
    return {
        "rankings": [
            {
                "name": "AP Top 25",
                "shortName": "AP Poll",
                "type": "ap",
                "ranks": [{"team": {"id": tid}, "current": rank} for tid, rank in ranks],
            }
        ]
    }


# ============================================================
# 1. Normal NFL response
# ============================================================

def test_normal_nfl_response():
    conn = fresh_conn()
    event = espn_event(broadcasts=[{"names": ["CBS"]}])
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["games_returned"] == 1
    assert summary["records_inserted"] == 1
    row = conn.execute(
        "SELECT ranking_home, ranking_away, record_home, record_away, tv_network, "
        "is_primetime, is_rivalry, playoff_relevance FROM game_context"
    ).fetchone()
    assert row[0] is None and row[1] is None, "NFL rankings must stay NULL"
    assert row[2] == "3-1" and row[3] == "2-2"
    assert row[4] == "CBS"
    assert row[5] is None, "is_primetime must never be derived/populated"
    assert row[6] is None and row[7] is None, "is_rivalry/playoff_relevance must stay NULL"
    conn.close()
    print("PASS: normal NFL response inserts correct context row, rankings + is_primetime NULL as required")


# ============================================================
# 2. Normal CFB response
# ============================================================

def test_normal_cfb_response():
    conn = fresh_conn()
    event = espn_event(
        home_name="Ohio State Buckeyes", away_name="Michigan Wolverines",
        home_team_id="194", away_team_id="130",
    )
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(200, espn_scoreboard([event])),
        FakeResponse(200, espn_rankings([("194", 2), ("130", 3)])),
    ]
    collector = make_collector(conn, session)

    summary = collector.collect("cfb")

    assert summary["records_inserted"] == 1
    assert summary["rankings_available"] is True
    row = conn.execute("SELECT ranking_home, ranking_away FROM game_context").fetchone()
    assert row == (2, 3)
    conn.close()
    print("PASS: normal CFB response inserts context with correct rankings for both teams")


def test_cfb_rankings_explicitly_select_ap_poll():
    rankings = {
        "rankings": [
            {
                "name": "AFCA Coaches Poll",
                "shortName": "AFCA Coaches Poll",
                "type": "usa",
                "ranks": [{"team": {"id": "194"}, "current": 9}],
            },
            {
                "name": "AP Top 25",
                "shortName": "AP Poll",
                "type": "ap",
                "ranks": [{"team": {"id": "194"}, "current": 2}],
            },
        ]
    }

    parsed = ContextCollector._parse_rankings(rankings)

    assert parsed == {"194": 2}
    print("PASS: CFB rankings explicitly select AP poll regardless of ESPN poll ordering")


# ============================================================
# 3. Ranked vs unranked CFB teams
# ============================================================

def test_ranked_vs_unranked_cfb_teams():
    conn = fresh_conn()
    event = espn_event(
        home_name="Ohio State Buckeyes", away_name="Rice Owls",
        home_team_id="194", away_team_id="242",
    )
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(200, espn_scoreboard([event])),
        FakeResponse(200, espn_rankings([("194", 2)])),  # Rice not ranked
    ]
    collector = make_collector(conn, session)

    collector.collect("cfb")

    row = conn.execute("SELECT ranking_home, ranking_away FROM game_context").fetchone()
    assert row == (2, None), "unranked team must be NULL, not 0 or guessed"
    conn.close()
    print("PASS: ranked vs unranked CFB teams handled correctly (unranked -> NULL)")


# ============================================================
# 4. Missing TV network
# ============================================================

def test_missing_tv_network():
    conn = fresh_conn()
    event = espn_event(broadcasts=None)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 1, "missing TV network must not block insertion"
    row = conn.execute("SELECT tv_network FROM game_context").fetchone()
    assert row == (None,)
    conn.close()
    print("PASS: missing TV network stored as NULL, row still inserted")


# ============================================================
# 5. Missing records
# ============================================================

def test_missing_records():
    conn = fresh_conn()
    event = espn_event(home_record=None, away_record=None)
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    summary = collector.collect("nfl")

    assert summary["records_inserted"] == 1, "missing records must not block insertion"
    row = conn.execute("SELECT record_home, record_away FROM game_context").fetchone()
    assert row == (None, None)
    conn.close()
    print("PASS: missing records stored as NULL, row still inserted")


# ============================================================
# 6. Malformed ESPN response
# ============================================================

def test_malformed_espn_response():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, {"not_events": []})  # missing "events" key
    collector = make_collector(conn, session)

    collector.collect("nfl")  # must not raise -- CollectionRun catches internally, check log instead

    log_row = conn.execute(
        "SELECT success, error_type FROM collection_log WHERE collector_name='context_collector'"
    ).fetchone()
    assert log_row[0] == 0
    assert log_row[1] == "ParsingError"
    row_count = conn.execute("SELECT COUNT(*) FROM game_context").fetchone()[0]
    assert row_count == 0
    conn.close()
    print("PASS: malformed ESPN response (missing 'events') is caught, logged as ParsingError, no rows inserted")


# ============================================================
# 7. Unknown team alias
# ============================================================

def test_unknown_team_alias():
    conn = fresh_conn()
    event = espn_event(home_name="Totally Fictional University", away_name="Ohio State Buckeyes")
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(200, espn_scoreboard([event])),
        FakeResponse(200, espn_rankings([])),
    ]
    collector = make_collector(conn, session)

    summary = collector.collect("cfb")

    assert summary["records_inserted"] == 0
    assert summary["records_skipped"] >= 1
    unresolved = conn.execute(
        "SELECT source FROM unresolved_aliases WHERE raw_text = 'Totally Fictional University'"
    ).fetchone()
    assert unresolved is not None
    assert unresolved[0] == "espn"
    conn.close()
    print("PASS: unknown team alias from ESPN is skipped and logged with source='espn', never guessed")


# ============================================================
# 8. Changed ranking/record on a later poll creates a new row
# ============================================================

def test_changed_data_creates_new_row_not_update():
    conn = fresh_conn()
    event_v1 = espn_event(
        home_name="Ohio State Buckeyes", away_name="Michigan Wolverines",
        home_team_id="194", away_team_id="130", home_record="3-1",
    )
    session1 = MagicMock()
    session1.request.side_effect = [
        FakeResponse(200, espn_scoreboard([event_v1])),
        FakeResponse(200, espn_rankings([("194", 5), ("130", 3)])),
    ]
    collector1 = make_collector(conn, session1, min_legitimate_poll_interval_minutes=0)
    collector1.collect("cfb")

    event_v2 = espn_event(
        home_name="Ohio State Buckeyes", away_name="Michigan Wolverines",
        home_team_id="194", away_team_id="130", home_record="4-1",  # record changed
    )
    session2 = MagicMock()
    session2.request.side_effect = [
        FakeResponse(200, espn_scoreboard([event_v2])),
        FakeResponse(200, espn_rankings([("194", 2), ("130", 3)])),  # ranking changed
    ]
    collector2 = make_collector(conn, session2, min_legitimate_poll_interval_minutes=0)
    collector2.collect("cfb")

    rows = conn.execute(
        "SELECT ranking_home, record_home, retrieved_at FROM game_context ORDER BY retrieved_at"
    ).fetchall()
    assert len(rows) == 2, "a changed observation must be a NEW row, not an update to the old one"
    assert rows[0][0] == 5 and rows[0][1] == "3-1"
    assert rows[1][0] == 2 and rows[1][1] == "4-1"
    assert rows[0][2] != rows[1][2], "each row must have its own retrieved_at"
    conn.close()
    print("PASS: changed ranking/record on a later poll creates a new append-only row, not an update")


# ============================================================
# 9. ESPN failure leaves Odds API collection unaffected
# ============================================================

def test_espn_failure_does_not_block_odds_collection():
    conn = fresh_conn()

    # ESPN context collector: will fail (malformed response)
    context_session = MagicMock()
    context_session.request.return_value = FakeResponse(200, {"not_events": []})
    context_collector = make_collector(conn, context_session)
    context_summary_ok = True
    try:
        context_collector.collect("nfl")
    except Exception:
        context_summary_ok = False  # collect() should NOT raise -- CollectionRun catches internally

    # Odds collector: independent instance, should succeed normally regardless
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

    assert context_summary_ok, "context_collector.collect() must not raise, even on internal failure"
    context_log = conn.execute(
        "SELECT success FROM collection_log WHERE collector_name='context_collector'"
    ).fetchone()
    assert context_log[0] == 0, "context collector run should be logged as a failure"

    assert odds_summary["records_inserted"] == 1, "odds collection must succeed independently of ESPN's failure"
    odds_log = conn.execute(
        "SELECT success FROM collection_log WHERE collector_name='odds_collector'"
    ).fetchone()
    assert odds_log[0] == 1

    conn.close()
    print("PASS: an ESPN context-collector failure is fully isolated and does not block odds collection")


# ============================================================
# 10. Raw-response / provenance linkage
# ============================================================

def test_raw_response_provenance_linkage():
    conn = fresh_conn()
    event = espn_event(
        home_name="Ohio State Buckeyes", away_name="Michigan Wolverines",
        home_team_id="194", away_team_id="130",
    )
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(200, espn_scoreboard([event])),
        FakeResponse(200, espn_rankings([("194", 2), ("130", 3)])),
    ]
    collector = make_collector(conn, session)

    collector.collect("cfb")

    raw_response_count = conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    assert raw_response_count == 2, "scoreboard + rankings should each produce their own raw_responses row"

    context_row = conn.execute("SELECT context_id, parser_version FROM game_context").fetchone()
    context_id, parser_version = context_row
    assert parser_version == "context_collector_v1"

    links = conn.execute(
        "SELECT role, raw_response_id FROM context_raw_response_links "
        "WHERE context_id = ? ORDER BY role",
        (context_id,),
    ).fetchall()
    assert len(links) == 2, "this CFB observation should link to exactly 2 raw responses"
    roles = {role for role, _ in links}
    assert roles == {"scoreboard", "rankings"}

    # Confirm each link points at a real, distinct raw_responses row with
    # the correct collector/source attribution
    for role, raw_response_id in links:
        linked = conn.execute(
            "SELECT collector_name, source FROM raw_responses WHERE raw_response_id = ?",
            (raw_response_id,),
        ).fetchone()
        assert linked == ("context_collector", "espn")

    conn.close()
    print("PASS: game_context observation cleanly links to multiple raw responses "
          "via context_raw_response_links, each tagged with its role")


def test_nfl_context_links_only_scoreboard_no_rankings():
    conn = fresh_conn()
    event = espn_event()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, espn_scoreboard([event]))
    collector = make_collector(conn, session)

    collector.collect("nfl")

    context_id = conn.execute("SELECT context_id FROM game_context").fetchone()[0]
    links = conn.execute(
        "SELECT role FROM context_raw_response_links WHERE context_id = ?", (context_id,)
    ).fetchall()
    assert [r[0] for r in links] == ["scoreboard"], "NFL has no rankings source, so only a scoreboard link should exist"
    conn.close()
    print("PASS: NFL context rows link only to the scoreboard response (no rankings source exists)")


if __name__ == "__main__":
    test_normal_nfl_response()
    test_normal_cfb_response()
    test_ranked_vs_unranked_cfb_teams()
    test_missing_tv_network()
    test_missing_records()
    test_malformed_espn_response()
    test_unknown_team_alias()
    test_changed_data_creates_new_row_not_update()
    test_espn_failure_does_not_block_odds_collection()
    test_raw_response_provenance_linkage()
    test_nfl_context_links_only_scoreboard_no_rankings()
    print("\nAll context_collector tests passed.")
