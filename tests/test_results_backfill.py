"""
Tests for collectors/run_results_backfill.py

Run with: python tests/test_results_backfill.py
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.run_results_backfill import (  # noqa: E402
    write_cfb_result_if_new_or_changed, backfill_cfb_window, backfill_nfl,
)
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
from collectors.results_collector import ResultsCollector  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_results_backfill_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.text = json.dumps(json_body) if json_body is not None else ""
        self.headers = headers or {}

    def json(self):
        return self._json_body


def _seed_canonical_game(conn, tr, gr):
    canonical_game_id = gr.resolve_or_create(
        "espn", "evt1", "cfb", "Ohio State Buckeyes", "Michigan Wolverines", "2025-09-06T20:20:00Z",
    )
    return canonical_game_id


def _parsed_result(home_score=27, away_score=20):
    return {
        "game_status": "STATUS_FINAL", "is_final": 1,
        "final_score_home": home_score, "final_score_away": away_score,
        "winner_home": 1, "is_overtime": 0, "is_neutral_site": None,
        "season_year": 2025, "season_type": 2, "week_number": 2,
        "venue_name": "Ohio Stadium", "espn_status_detail": "Final",
    }


def _seed_raw_response(conn):
    cur = conn.execute(
        "INSERT INTO raw_responses (collector_name, source, request_url, retrieved_at, http_status, response_body) "
        "VALUES ('results_collector', 'espn', 'https://example.com', '2026-01-01T00:00:00.000000Z', 200, '{}')"
    )
    conn.commit()
    return cur.lastrowid


def test_write_new_result():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    canonical_game_id = _seed_canonical_game(conn, tr, gr)
    raw_id = _seed_raw_response(conn)

    outcome = write_cfb_result_if_new_or_changed(
        conn, canonical_game_id, "Ohio State Buckeyes", "Michigan Wolverines",
        "2025-09-06T20:20:00Z", "2026-01-01T00:00:00Z", _parsed_result(), raw_id, "results_collector_v1",
    )
    assert outcome == "new"
    row_count = conn.execute(
        "SELECT COUNT(*) FROM game_results WHERE canonical_game_id=?", (canonical_game_id,)
    ).fetchone()[0]
    assert row_count == 1
    link_count = conn.execute(
        "SELECT COUNT(*) FROM game_results_raw_response_links WHERE role='scoreboard'"
    ).fetchone()[0]
    assert link_count == 1
    conn.close()
    print("PASS: a brand-new result is inserted with a 'scoreboard' provenance link")


def test_write_unchanged_result_creates_confirmation_link_only():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    canonical_game_id = _seed_canonical_game(conn, tr, gr)
    raw_id_1 = _seed_raw_response(conn)
    raw_id_2 = _seed_raw_response(conn)

    write_cfb_result_if_new_or_changed(
        conn, canonical_game_id, "Ohio State Buckeyes", "Michigan Wolverines",
        "2025-09-06T20:20:00Z", "2026-01-01T00:00:00Z", _parsed_result(), raw_id_1, "results_collector_v1",
    )
    outcome = write_cfb_result_if_new_or_changed(
        conn, canonical_game_id, "Ohio State Buckeyes", "Michigan Wolverines",
        "2025-09-06T20:20:00Z", "2026-01-02T00:00:00Z", _parsed_result(), raw_id_2, "results_collector_v1",
    )
    assert outcome == "unchanged"
    row_count = conn.execute(
        "SELECT COUNT(*) FROM game_results WHERE canonical_game_id=?", (canonical_game_id,)
    ).fetchone()[0]
    assert row_count == 1, "no duplicate game_results row for an unchanged result"
    links = conn.execute("SELECT role FROM game_results_raw_response_links").fetchall()
    roles = sorted(r[0] for r in links)
    assert roles == ["scoreboard", "scoreboard_confirmation"]
    conn.close()
    print("PASS: an unchanged result creates zero new game_results rows but a real confirmation link")


def test_write_corrected_result_preserves_original():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    canonical_game_id = _seed_canonical_game(conn, tr, gr)
    raw_id_1 = _seed_raw_response(conn)
    raw_id_2 = _seed_raw_response(conn)

    write_cfb_result_if_new_or_changed(
        conn, canonical_game_id, "Ohio State Buckeyes", "Michigan Wolverines",
        "2025-09-06T20:20:00Z", "2026-01-01T00:00:00Z", _parsed_result(home_score=27), raw_id_1, "results_collector_v1",
    )
    outcome = write_cfb_result_if_new_or_changed(
        conn, canonical_game_id, "Ohio State Buckeyes", "Michigan Wolverines",
        "2025-09-06T20:20:00Z", "2026-01-02T00:00:00Z", _parsed_result(home_score=28), raw_id_2, "results_collector_v1",
    )
    assert outcome == "corrected"
    rows = conn.execute(
        "SELECT final_score_home FROM game_results WHERE canonical_game_id=? ORDER BY retrieved_at",
        (canonical_game_id,),
    ).fetchall()
    assert [r[0] for r in rows] == [27, 28], "both original and corrected rows must be preserved"
    conn.close()
    print("PASS: a genuine result change appends a new row and preserves the original")


def test_backfill_cfb_window_writes_only_fbs_vs_fbs():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    allowlist = load_fbs_allowlist(2025)

    events = [
        {
            "id": "e1", "date": "2025-09-06T20:20Z",
            "competitions": [{
                "date": "2025-09-06T20:20Z",
                "competitors": [
                    {"homeAway": "home", "team": {"id": "1", "displayName": "Ohio State Buckeyes"}, "score": "27"},
                    {"homeAway": "away", "team": {"id": "2", "displayName": "Michigan Wolverines"}, "score": "20"},
                ],
                "status": {"period": 4, "type": {"name": "STATUS_FINAL", "completed": True, "detail": "Final"}},
            }],
            "status": {"period": 4, "type": {"name": "STATUS_FINAL", "completed": True, "detail": "Final"}},
            "season": {"year": 2025, "type": 2}, "week": {"number": 2},
        },
        {
            "id": "e2", "date": "2025-09-06T20:20Z",
            "competitions": [{
                "date": "2025-09-06T20:20Z",
                "competitors": [
                    {"homeAway": "home", "team": {"id": "3", "displayName": "Alabama Crimson Tide"}, "score": "45"},
                    {"homeAway": "away", "team": {"id": "4", "displayName": "Some FCS Buy Game Opponent"}, "score": "3"},
                ],
                "status": {"period": 4, "type": {"name": "STATUS_FINAL", "completed": True, "detail": "Final"}},
            }],
            "status": {"period": 4, "type": {"name": "STATUS_FINAL", "completed": True, "detail": "Final"}},
            "season": {"year": 2025, "type": 2}, "week": {"number": 2},
        },
    ]
    session = MagicMock()
    session.request.return_value = FakeResponse(200, {"events": events})
    collector = ResultsCollector(
        conn=conn, team_resolver=tr, game_resolver=gr, session=session, sleep_fn=lambda s: None,
        dry_run=True, min_legitimate_poll_interval_minutes=0,
    )

    result = backfill_cfb_window(conn, tr, gr, collector, allowlist, "20250906-20250913")

    assert result["new"] == 1
    assert result["excluded"] == 1
    assert result["blocking"] == 0

    stored = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    assert stored == 1, "only the FBS-vs-FBS game should be written"
    conn.close()
    print("PASS: backfill_cfb_window writes only the FBS-vs-FBS game, correctly excludes the FCS buy game")


def test_backfill_nfl_writes_via_collector():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    event = {
        "id": "n1", "date": "2025-09-07T17:00Z",
        "competitions": [{
            "date": "2025-09-07T17:00Z",
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "displayName": "Kansas City Chiefs"}, "score": "24"},
                {"homeAway": "away", "team": {"id": "2", "displayName": "Los Angeles Chargers"}, "score": "17"},
            ],
            "status": {"period": 4, "type": {"name": "STATUS_FINAL", "completed": True, "detail": "Final"}},
        }],
        "status": {"period": 4, "type": {"name": "STATUS_FINAL", "completed": True, "detail": "Final"}},
        "season": {"year": 2025, "type": 2}, "week": {"number": 1},
    }
    session = MagicMock()
    session.request.return_value = FakeResponse(200, {"events": [event]})

    import collectors.run_results_backfill as backfill_module

    original_resultscollector = backfill_module.ResultsCollector

    class PatchedCollector(original_resultscollector):
        def __init__(self, *args, **kwargs):
            kwargs["session"] = session
            kwargs["sleep_fn"] = lambda s: None
            super().__init__(*args, **kwargs)

    backfill_module.ResultsCollector = PatchedCollector
    try:
        totals = backfill_nfl(conn, tr, gr)
    finally:
        backfill_module.ResultsCollector = original_resultscollector

    assert totals["new"] >= 1
    stored = conn.execute("SELECT COUNT(*) FROM game_results").fetchone()[0]
    assert stored >= 1
    conn.close()
    print("PASS: backfill_nfl correctly delegates to ResultsCollector and results get written")


if __name__ == "__main__":
    test_write_new_result()
    test_write_unchanged_result_creates_confirmation_link_only()
    test_write_corrected_result_preserves_original()
    test_backfill_cfb_window_writes_only_fbs_vs_fbs()
    test_backfill_nfl_writes_via_collector()
    print("\nAll results backfill tests passed.")
