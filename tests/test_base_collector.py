"""
Tests for collectors/base_collector.py

Uses a fake requests.Session-like object (no real network calls) so tests
are fast and deterministic. sleep_fn is stubbed to a no-op so backoff
delays don't slow down the test suite.

Run with: python tests/test_base_collector.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.base_collector import BaseCollector  # noqa: E402
from collectors.errors import (  # noqa: E402
    NonRetryableRequestError,
    RequestBudgetExceededError,
    RetriesExhaustedError,
)
from collectors.config import redact  # noqa: E402
from db.migrate import run_migration  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

import requests

TEST_DIR = Path(__file__).parent / "_base_collector_test_workspace"


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
    def __init__(self, status_code=200, text="{}", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.text)


def make_collector(conn, session=None, **kwargs):
    return BaseCollector(
        conn=conn,
        collector_name="test_collector",
        source="test_source",
        session=session or MagicMock(),
        sleep_fn=lambda s: None,  # no real sleeping in tests
        **kwargs,
    )


# ============================================================
# 1. Normal successful request
# ============================================================

def test_normal_successful_request():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, '{"ok": true}')
    collector = make_collector(conn, session=session)

    with collector.new_run() as run:
        response = collector.request_with_retry(run, "GET", "https://example.com/odds")
        assert response.status_code == 200
        run.records_captured = 1

    assert run.success is True
    assert run.retry_count == 0
    log_row = conn.execute(
        "SELECT success, records_captured, retry_count FROM collection_log"
    ).fetchone()
    assert log_row == (1, 1, 0)
    conn.close()
    print("PASS: normal successful request logs success with correct counts")


# ============================================================
# 2. Timeout -> retry -> success
# ============================================================

def test_timeout_then_retry_then_success():
    conn = fresh_conn()
    session = MagicMock()
    session.request.side_effect = [
        requests.exceptions.Timeout("read timed out"),
        FakeResponse(200, "{}"),
    ]
    collector = make_collector(conn, session=session, max_retries=3)

    with collector.new_run() as run:
        response = collector.request_with_retry(run, "GET", "https://example.com/odds")
        assert response.status_code == 200

    assert run.success is True
    assert run.retry_count == 1, "one retry should have been recorded"
    assert session.request.call_count == 2
    conn.close()
    print("PASS: timeout followed by success is recorded as one retry and an overall success")


# ============================================================
# 3. Timeout -> retries exhausted
# ============================================================

def test_timeout_retries_exhausted():
    conn = fresh_conn()
    session = MagicMock()
    session.request.side_effect = requests.exceptions.Timeout("read timed out")
    collector = make_collector(conn, session=session, max_retries=2)

    with collector.new_run() as run:
        collector.request_with_retry(run, "GET", "https://example.com/odds")

    assert run.success is False
    assert run.error_type == "RetriesExhaustedError"
    assert session.request.call_count == 3, "initial attempt + 2 retries = 3 total"
    log_row = conn.execute("SELECT success, error_type FROM collection_log").fetchone()
    assert log_row == (0, "RetriesExhaustedError")
    conn.close()
    print("PASS: exhausted retries on repeated timeout logs a clean failure, no crash")


# ============================================================
# 4. HTTP 429 with retry
# ============================================================

def test_http_429_retries_then_succeeds():
    conn = fresh_conn()
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(429, "rate limited", headers={"Retry-After": "0"}),
        FakeResponse(200, "{}"),
    ]
    collector = make_collector(conn, session=session, max_retries=3)

    with collector.new_run() as run:
        response = collector.request_with_retry(run, "GET", "https://example.com/odds")
        assert response.status_code == 200

    assert run.success is True
    assert run.retry_count == 1
    conn.close()
    print("PASS: HTTP 429 triggers a retry (respecting Retry-After) and then succeeds")


# ============================================================
# 5. HTTP 500 transient failure
# ============================================================

def test_http_500_is_retried():
    conn = fresh_conn()
    session = MagicMock()
    session.request.side_effect = [
        FakeResponse(500, "server error"),
        FakeResponse(200, "{}"),
    ]
    collector = make_collector(conn, session=session, max_retries=3)

    with collector.new_run() as run:
        response = collector.request_with_retry(run, "GET", "https://example.com/odds")
        assert response.status_code == 200
    assert run.success is True
    assert run.retry_count == 1
    conn.close()
    print("PASS: HTTP 500 (in the retryable allow-list) is retried and then succeeds")


# ============================================================
# 6. HTTP 404 / non-retryable failure
# ============================================================

def test_http_404_is_not_retried():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(404, "not found")
    collector = make_collector(conn, session=session, max_retries=3)

    with collector.new_run() as run:
        collector.request_with_retry(run, "GET", "https://example.com/odds")

    assert run.success is False
    assert run.error_type == "NonRetryableRequestError"
    assert session.request.call_count == 1, "404 must not be retried at all"
    conn.close()
    print("PASS: HTTP 404 fails immediately with no retries")


# ============================================================
# 7. Successful response with zero records
# ============================================================

def test_successful_response_zero_records_is_distinct_from_failure():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, "[]")
    collector = make_collector(conn, session=session)

    with collector.new_run() as run:
        response = collector.request_with_retry(run, "GET", "https://example.com/odds")
        data = response.json()
        assert data == []
        # records_captured intentionally left at 0 -- zero applicable records,
        # not a failure

    assert run.success is True
    assert run.records_captured == 0
    assert run.records_skipped == 0
    conn.close()
    print("PASS: a successful request returning zero applicable records logs success, "
          "distinct from a collector failure")


# ============================================================
# 8. Malformed response / parsing failure
# ============================================================

def test_malformed_response_parsing_failure_is_logged():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, "not valid json{{{")
    collector = make_collector(conn, session=session)

    with collector.new_run() as run:
        response = collector.request_with_retry(run, "GET", "https://example.com/odds")
        response.json()  # raises json.JSONDecodeError inside the `with` block

    assert run.success is False
    assert "JSONDecodeError" in run.error_type
    conn.close()
    print("PASS: a parsing failure on an otherwise-successful response is logged as a run failure")


# ============================================================
# 9. Partial bad record while other records are valid
# ============================================================

def test_partial_bad_record_does_not_block_good_records():
    conn = fresh_conn()
    collector = make_collector(conn)

    with collector.new_run() as run:
        good_records = [("nfl_kc", "Kansas City Chiefs"), ("nfl_lac", "Los Angeles Chargers")]
        bad_record = ("nfl_kc", None)  # will violate NOT NULL on display_name via a bogus table op

        for canonical_id, display_name in good_records:
            def _insert(c, cid=canonical_id, dn=display_name):
                c.execute(
                    "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
                    "VALUES (?, 'nfl', ?)",
                    (cid + "_dup", dn),
                )
            collector.safe_insert(run, _insert, context_note=f"team {canonical_id}")

        def _bad_insert(c):
            # Intentionally violates the sport CHECK constraint
            c.execute(
                "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
                "VALUES ('bogus_team', 'not_a_real_sport', 'Bogus')"
            )
        collector.safe_insert(run, _bad_insert, context_note="bad record")

    assert run.success is True, "the run as a whole succeeded even though one record failed"
    assert run.records_captured == 2
    assert run.records_skipped == 1

    good_count = conn.execute(
        "SELECT COUNT(*) FROM teams_canonical WHERE canonical_team_id IN ('nfl_kc_dup', 'nfl_lac_dup')"
    ).fetchone()[0]
    assert good_count == 2, "good records must be committed despite the later bad record"
    bad_count = conn.execute(
        "SELECT COUNT(*) FROM teams_canonical WHERE canonical_team_id = 'bogus_team'"
    ).fetchone()[0]
    assert bad_count == 0, "the bad record must not have been inserted"
    conn.close()
    print("PASS: a bad record is skipped and logged without blocking or rolling back good records")


# ============================================================
# 10. Unresolved team alias
# ============================================================

def test_unresolved_team_alias_skips_game_not_whole_run():
    conn = fresh_conn()
    collector = make_collector(conn)
    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)

    with collector.new_run() as run:
        # First game: unresolvable team ("Miami" is deliberately ambiguous)
        result1 = collector.resolve_game_or_skip(
            run, game_resolver, "common", "evt_bad", "cfb",
            "Miami", "Ohio State", "2026-09-05T19:00:00Z",
        )
        # Second game: resolvable, should proceed normally
        result2 = collector.resolve_game_or_skip(
            run, game_resolver, "the_odds_api", "evt_good", "nfl",
            "Kansas City Chiefs", "Los Angeles Chargers", "2026-09-10T20:20:00Z",
        )

    assert result1 is None
    assert result2 is not None
    assert run.records_skipped == 1
    assert run.success is True, "an unresolved game must not fail the whole run"
    conn.close()
    print("PASS: unresolved team alias skips only that game, run continues and succeeds")


# ============================================================
# 11. Database insert failure
# ============================================================

def test_database_insert_failure_is_caught_and_logged():
    conn = fresh_conn()
    collector = make_collector(conn)

    with collector.new_run() as run:
        def _insert_into_nonexistent_table(c):
            c.execute("INSERT INTO this_table_does_not_exist (x) VALUES (1)")
        result = collector.safe_insert(run, _insert_into_nonexistent_table, context_note="bad table")

    assert result is False
    assert run.records_skipped == 1
    assert run.success is True, "a caught per-record DB error should not fail the whole run"
    conn.close()
    print("PASS: a database insert failure is caught, logged, and does not crash the run")


# ============================================================
# 12. API credential redaction in logs/errors
# ============================================================

def test_secret_redaction():
    url_with_key = "https://api.the-odds-api.com/v4/sports/nfl/odds?apiKey=super-secret-value-123&regions=us"
    redacted = redact(url_with_key)
    assert "super-secret-value-123" not in redacted
    assert "apiKey=***REDACTED***" in redacted
    assert "regions=us" in redacted, "non-secret params should be preserved"

    # Also confirm redaction happens automatically in a real failure path
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(404, "not found")
    collector = make_collector(conn, session=session)

    with collector.new_run() as run:
        collector.request_with_retry(
            run, "GET",
            "https://api.the-odds-api.com/v4/sports/nfl/odds?apiKey=super-secret-value-123",
        )

    assert "super-secret-value-123" not in (run.error_detail or "")
    log_row = conn.execute("SELECT error_detail FROM collection_log").fetchone()
    assert "super-secret-value-123" not in (log_row[0] or "")
    conn.close()
    print("PASS: API keys are redacted from both in-memory error details and the collection_log row")


# ============================================================
# 13. Repeated runs remain safe
# ============================================================

def test_repeated_runs_remain_safe():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, "{}")
    collector = make_collector(conn, session=session)

    for _ in range(3):
        with collector.new_run() as run:
            collector.request_with_retry(run, "GET", "https://example.com/odds")
            run.records_captured = 1

    log_count = conn.execute("SELECT COUNT(*) FROM collection_log").fetchone()[0]
    assert log_count == 3, "each run must produce exactly one collection_log row"
    successes = conn.execute("SELECT COUNT(*) FROM collection_log WHERE success = 1").fetchone()[0]
    assert successes == 3
    conn.close()
    print("PASS: repeated collector runs each log independently and safely")


# ============================================================
# 14. collection_log written correctly for success AND every failure class
#     (aggregate check across the scenarios above, plus the request-budget
#     safeguard specifically)
# ============================================================

def test_request_budget_ceiling_is_enforced_and_logged():
    conn = fresh_conn()
    session = MagicMock()
    session.request.return_value = FakeResponse(200, "{}")
    collector = make_collector(conn, session=session, max_requests_per_run=2)

    with collector.new_run() as run:
        collector.request_with_retry(run, "GET", "https://example.com/odds")  # 1
        collector.request_with_retry(run, "GET", "https://example.com/odds")  # 2
        collector.request_with_retry(run, "GET", "https://example.com/odds")  # 3rd exceeds budget

    assert run.success is False
    assert run.error_type == "RequestBudgetExceededError"
    log_row = conn.execute("SELECT success, error_type FROM collection_log").fetchone()
    assert log_row == (0, "RequestBudgetExceededError")
    conn.close()
    print("PASS: per-run request budget ceiling is enforced and a breach is clearly logged")


def test_raw_response_stored_once_and_referenced_not_duplicated():
    conn = fresh_conn()
    collector = make_collector(conn)

    body = '{"games": [{"id": "g1"}, {"id": "g2"}, {"id": "g3"}]}'
    raw_response_id = collector.store_raw_response(
        url="https://api.the-odds-api.com/v4/sports/nfl/odds?apiKey=secret123",
        http_status=200,
        response_body=body,
    )

    # Simulate parsing 3 games out of that ONE response into 3 market_snapshots
    # rows, all referencing the same raw_response_id -- not storing body 3x.
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)
    canonical_game_id = gr.resolve_or_create(
        "the_odds_api", "g1", "nfl", "Kansas City Chiefs", "Los Angeles Chargers",
        "2026-09-10T20:20:00Z",
    )
    with collector.new_run() as run:
        for book in ["draftkings", "fanduel", "betmgm"]:
            def _insert(c, b=book):
                c.execute(
                    "INSERT INTO market_snapshots "
                    "(canonical_game_id, sport, home_team, away_team, commence_time, book, "
                    "observed_at, spread_home, parser_version, raw_response_id) "
                    "VALUES (?, 'nfl', 'Kansas City Chiefs', 'Los Angeles Chargers', "
                    "'2026-09-10T20:20:00Z', ?, '2026-09-08T12:00:00Z', -3.0, ?, ?)",
                    (canonical_game_id, b, collector.PARSER_VERSION, raw_response_id),
                )
            collector.safe_insert(run, _insert, context_note=book)

    raw_response_count = conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0]
    snapshot_count = conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    distinct_raw_ids_referenced = conn.execute(
        "SELECT COUNT(DISTINCT raw_response_id) FROM market_snapshots"
    ).fetchone()[0]

    assert raw_response_count == 1, "the raw response body must be stored exactly once"
    assert snapshot_count == 3, "three parsed rows should still be created"
    assert distinct_raw_ids_referenced == 1, "all three rows should reference the same raw_response_id"

    stored_url = conn.execute("SELECT request_url FROM raw_responses").fetchone()[0]
    assert "secret123" not in stored_url, "the stored URL must have its API key redacted"

    conn.close()
    print("PASS: raw response is stored exactly once per request and referenced (not duplicated) "
          "by every parsed row, with the URL's API key redacted before storage")


if __name__ == "__main__":
    test_normal_successful_request()
    test_timeout_then_retry_then_success()
    test_timeout_retries_exhausted()
    test_http_429_retries_then_succeeds()
    test_http_500_is_retried()
    test_http_404_is_not_retried()
    test_successful_response_zero_records_is_distinct_from_failure()
    test_malformed_response_parsing_failure_is_logged()
    test_partial_bad_record_does_not_block_good_records()
    test_unresolved_team_alias_skips_game_not_whole_run()
    test_database_insert_failure_is_caught_and_logged()
    test_secret_redaction()
    test_repeated_runs_remain_safe()
    test_request_budget_ceiling_is_enforced_and_logged()
    test_raw_response_stored_once_and_referenced_not_duplicated()
    print("\nAll base_collector tests passed.")
