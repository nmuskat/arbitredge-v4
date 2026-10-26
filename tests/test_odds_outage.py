"""
Tests for the Odds API outage patch: redaction, error classification,
credential safety, circuit breaker / recovery, stale-data behaviour, and the
owner-gated credential validator. Fully offline -- every HTTP response is
mocked; no request ever leaves the process.

collectors/config.py is not in the audited source archive. If it cannot be
imported, a TEST-ONLY stand-in exposing the names base_collector imports is
installed (its redact() mirrors the behaviour tests/test_base_collector.py
expects of the real one). Production uses the real module.
"""

import io
import json
import re
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - depends on the environment
    import collectors.config  # noqa: F401
    CONFIG_IS_STUB = False
except ModuleNotFoundError:
    stub = types.ModuleType("collectors.config")
    stub.DEFAULT_BACKOFF_BASE_SECONDS = 0.0
    stub.DEFAULT_BACKOFF_FACTOR = 2.0
    stub.DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
    stub.DEFAULT_MAX_BACKOFF_SECONDS = 0.0
    stub.DEFAULT_MAX_REQUESTS_PER_RUN = 10
    stub.DEFAULT_MAX_RETRIES = 2
    stub.DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 0.0
    stub.DEFAULT_READ_TIMEOUT_SECONDS = 10.0
    stub.RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    stub.redact = lambda s: re.sub(r"(apiKey=)[^&\s]+", r"\1***REDACTED***", str(s))
    stub.get_env = lambda name, required=False: None
    sys.modules["collectors.config"] = stub
    CONFIG_IS_STUB = True

import requests  # noqa: E402

from collectors import provider_errors as pe  # noqa: E402
from collectors.errors import NonRetryableRequestError, RetriesExhaustedError  # noqa: E402
from features import market_freshness as mf  # noqa: E402
from operations import odds_circuit_breaker as cb  # noqa: E402
from operations import validate_odds_credential as vc  # noqa: E402

KEY = "a1b2c3d4e5f60718293a4b5c6d7e8f90"   # fake, 32-hex like a real key


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.text = self.content.decode("utf-8", errors="replace")
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.url = f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds?apiKey={KEY}&regions=us"

    def json(self):
        return json.loads(self.text)


def assert_no_secret(*objs):
    for o in objs:
        text = o if isinstance(o, str) else json.dumps(o, default=str)
        assert KEY not in text and KEY.upper() not in text, text


# ------------------------------------------------------------------ redaction

def test_scrub_removes_secret_in_every_shape():
    samples = [
        f"key {KEY} is invalid",
        f"https://api.the-odds-api.com/v4/x?apiKey={KEY}&regions=us",
        f'{{"apiKey": "{KEY}"}}',
        f"Authorization: Bearer {KEY}",
        f"api_key={KEY}",
        f"x-api-key: {KEY}",
        f"token={KEY[:20]}",
    ]
    for s in samples:
        out = pe.scrub(s, (KEY,))
        assert_no_secret(out)
        assert pe.REDACTED in out
    assert pe.scrub("regions=us&markets=spreads", (KEY,)) == "regions=us&markets=spreads"
    assert pe.scrub("Usage quota has been reached", (KEY,)) == "Usage quota has been reached"


def test_short_secret_is_removed_by_value_even_when_echoed_bare():
    secret = "Sh0rt-Pa55"   # too short for the long-token rule, no key= prefix
    out = pe.scrub(f"credential Sh0rt-Pa55 rejected (Sh0rt%2DPa55)", (secret,))
    assert "Sh0rt-Pa55" not in out and "Sh0rt%2DPa55" not in out
    assert out.count(pe.REDACTED) == 2
    assert pe.scrub("%53%68%30%72%74%2d%50%61%35%35 / SH0RT-PA55", (secret,)).count(pe.REDACTED) == 2


def test_scrub_catches_unknown_long_tokens_even_without_the_secret_list():
    out = pe.scrub(f"your key {KEY} was deactivated")
    assert_no_secret(out)


# ------------------------------------------------------- error classification

@pytest.mark.parametrize("code,klass", [
    ("OUT_OF_USAGE_CREDITS", pe.QUOTA_EXHAUSTED),
    ("INVALID_KEY", pe.AUTH_INVALID_KEY),
    ("DEACTIVATED_KEY", pe.AUTH_DEACTIVATED_KEY),
])
def test_documented_provider_codes_are_classified(code, klass):
    r = FakeResponse(401, {"message": "some message", "error_code": code,
                           "details_url": "https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html"},
                     {"x-requests-remaining": "0", "x-requests-used": "500", "x-requests-last": "0"})
    d = pe.extract_provider_error(r, (KEY,))
    assert d["classification"] == klass and d["error_code"] == code
    assert d["details_url"] == "https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html"
    assert d["safe_headers"]["x-requests-remaining"] == "0" and d["credits_remaining"] == 0.0
    s = pe.summarize(d)
    assert f"classification={klass}" in s and f"error_code={code}" in s and "x-requests-remaining=0" in s


def test_unknown_401_and_other_statuses():
    assert pe.extract_provider_error(FakeResponse(401, b"<html>Unauthorized</html>"))["classification"] == pe.AUTH_UNKNOWN
    assert pe.extract_provider_error(FakeResponse(403, b""))["classification"] == pe.AUTH_UNKNOWN
    assert pe.extract_provider_error(FakeResponse(429, {"message": "slow down"}))["classification"] == pe.RATE_LIMITED
    assert pe.extract_provider_error(FakeResponse(502, b"bad gateway"))["classification"] == pe.SERVER_ERROR
    assert pe.extract_provider_error(FakeResponse(422, {"message": "bad param"}))["classification"] == pe.CLIENT_ERROR
    # status alone never invents a provider code
    assert pe.extract_provider_error(FakeResponse(401, b""))["error_code"] is None


def test_hostile_or_echoing_response_cannot_leak_the_key():
    body = {"message": f"Invalid key {KEY}\x00\x1b[31m " + "x" * 1000, "error_code": f"INVALID_KEY;{KEY}",
            "details_url": f"https://the-odds-api.com/account?apiKey={KEY}", "apiKey": KEY}
    r = FakeResponse(401, body, {"x-requests-remaining": KEY, "set-cookie": f"s={KEY}", "x-echo": KEY})
    d = pe.extract_provider_error(r, (KEY,))
    assert_no_secret(d, pe.summarize(d))
    assert d["error_code"] is None                       # malformed code rejected, not "cleaned"
    assert d["details_url"] is None                      # query string -> dropped entirely
    assert "\x00" not in d["message"] and "\x1b" not in d["message"]
    assert len(d["message"]) <= pe.MAX_MESSAGE_CHARS + 1
    assert set(d["safe_headers"]) <= set(pe.SAFE_RESPONSE_HEADERS)   # set-cookie / x-echo never kept
    assert d["body_sha256"] and d["body_length"] == len(r.content)


def test_details_url_allowlist():
    ok = pe._clean_details_url("https://the-odds-api.com/liveapi/guides/v4/api-error-codes.html")
    assert ok
    for bad in ("http://the-odds-api.com/x", "https://evil.example/x", "https://the-odds-api.com/x#frag",
                "https://user:pw@the-odds-api.com/x", 42, None):
        assert pe._clean_details_url(bad) is None


def test_extraction_never_raises():
    class Broken:
        status_code = 401
        headers = None

        @property
        def content(self):
            raise RuntimeError("boom")
    d = pe.extract_provider_error(Broken(), (KEY,))
    assert d["classification"] == pe.AUTH_UNKNOWN and d["body_length"] == 0


# -------------------------------------------- collector integration (mocked)

class MockSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _collector(session):
    from collectors.odds_collector import OddsCollector
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE collection_log (collector_name TEXT, scheduled_at TEXT, executed_at TEXT, source TEXT, "
                 "success INTEGER, records_captured INTEGER, records_skipped INTEGER, error_type TEXT, "
                 "error_detail TEXT, retry_count INTEGER)")
    conn.execute("CREATE TABLE raw_responses (raw_response_id INTEGER PRIMARY KEY, collector_name TEXT, source TEXT, "
                 "request_url TEXT, request_params_redacted TEXT, retrieved_at TEXT, http_status INTEGER, "
                 "response_body TEXT, parser_version TEXT)")
    c = OddsCollector(conn, None, None, api_key=KEY, session=session, sleep_fn=lambda s: None,
                      min_legitimate_poll_interval_minutes=0)
    return c, conn


def _all_db_text(conn):
    out = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        out += [json.dumps(r, default=str) for r in conn.execute(f"SELECT * FROM {t}")]
    return "\n".join(out)


def test_401_is_logged_with_provider_diagnostics_and_no_secret(caplog):
    body = {"message": f"Usage quota has been reached. key={KEY}", "error_code": "OUT_OF_USAGE_CREDITS"}
    session = MockSession([FakeResponse(401, body, {"x-requests-remaining": "0", "x-requests-used": "500"})])
    c, conn = _collector(session)
    with caplog.at_level("INFO"):
        c.collect("nfl")
    assert len(session.calls) == 1                         # 401 is never retried
    _, url, kw = session.calls[0]
    assert KEY not in url and kw["params"]["apiKey"] == KEY   # key only ever in params
    success, etype, detail = conn.execute("SELECT success, error_type, error_detail FROM collection_log").fetchone()
    assert success == 0 and etype == "NonRetryableRequestError"   # unchanged error_type for existing tooling
    assert "classification=QUOTA_EXHAUSTED" in detail and "error_code=OUT_OF_USAGE_CREDITS" in detail
    assert "x-requests-remaining=0" in detail and detail.startswith("Non-retryable HTTP 401")
    assert conn.execute("SELECT COUNT(*) FROM raw_responses").fetchone()[0] == 0   # failures still not stored as data
    assert_no_secret(_all_db_text(conn), caplog.text)


def test_exception_carries_sanitized_diagnostics():
    session = MockSession([FakeResponse(401, {"message": f"bad {KEY}", "error_code": "INVALID_KEY"})])
    c, _ = _collector(session)
    run = c.new_run()
    with pytest.raises(NonRetryableRequestError) as ei:
        c.request_with_retry(run, "GET", "https://api.the-odds-api.com/v4/sports/x/odds", params={"apiKey": KEY})
    assert ei.value.diagnostics["classification"] == pe.AUTH_INVALID_KEY
    assert_no_secret(str(ei.value), ei.value.diagnostics)


def test_retries_exhausted_keeps_last_response_diagnostics():
    resp = lambda: FakeResponse(429, {"message": "Too many requests", "error_code": "EXCEEDED_FREQ_LIMIT"},  # noqa: E731
                                {"retry-after": "1"})
    session = MockSession([resp(), resp(), resp()])
    c, _ = _collector(session)
    with pytest.raises(RetriesExhaustedError) as ei:
        c.request_with_retry(c.new_run(), "GET", "https://api.the-odds-api.com/v4/x", params={"apiKey": KEY})
    assert ei.value.diagnostics["classification"] == pe.RATE_LIMITED
    assert "error_code=EXCEEDED_FREQ_LIMIT" in str(ei.value)


def test_connection_error_text_containing_the_key_is_scrubbed():
    err = requests.exceptions.ConnectionError(
        f"HTTPSConnectionPool(host='api.the-odds-api.com'): Max retries exceeded with url: /v4/x?apiKey={KEY}&regions=us")
    session = MockSession([err, err, err])
    c, conn = _collector(session)
    c.collect("cfb")
    etype, detail = conn.execute("SELECT error_type, error_detail FROM collection_log").fetchone()
    assert etype == "RetriesExhaustedError" and detail.startswith("Exhausted retries after 3 attempt(s)")
    assert len(session.calls) == 3
    assert_no_secret(_all_db_text(conn))


def test_successful_response_path_unchanged():
    session = MockSession([FakeResponse(200, [], {"x-requests-remaining": "321"})])
    c, conn = _collector(session)
    summary = c.collect("nfl")
    assert summary["usage_info"] == {"x-requests-remaining": "321"} and summary["games_returned"] == 0
    assert conn.execute("SELECT success FROM collection_log").fetchone()[0] == 1
    stored = conn.execute("SELECT request_url FROM raw_responses").fetchone()[0]
    assert KEY not in stored


# ------------------------------------------------- circuit breaker + recovery

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def row(minutes_ago, success, detail=None, etype=None):
    t = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return (t, success, etype if etype else (None if success else "NonRetryableRequestError"), detail)


AUTH = "Non-retryable HTTP 401 from x; HTTP 401 classification={} error_code=none"


def test_breaker_closed_below_threshold():
    d = cb.evaluate([row(10, 0, AUTH.format("AUTH_UNKNOWN")), row(30, 0, AUTH.format("AUTH_UNKNOWN")), row(60, 1)], NOW)
    assert d.allow and d.state == "CLOSED" and d.consecutive_auth_failures == 2


def test_breaker_opens_and_paces_probes_for_unknown_auth():
    rows = [row(10 + 20 * i, 0, AUTH.format("AUTH_UNKNOWN")) for i in range(5)]
    d = cb.evaluate(rows, NOW)
    assert not d.allow and d.state == "OPEN" and d.consecutive_auth_failures == 5
    later = NOW + timedelta(hours=cb.PROBE_INTERVAL_HOURS)
    assert cb.evaluate(rows, later).state == "HALF_OPEN_PROBE"


def test_breaker_respects_documented_monthly_reset_for_quota():
    rows = [row(10 + 20 * i, 0, AUTH.format("QUOTA_EXHAUSTED")) for i in range(3)]
    d = cb.evaluate(rows, NOW)
    assert not d.allow and d.next_attempt_at == "2026-11-01T00:00:00Z"
    assert not cb.evaluate(rows, datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)).allow
    assert cb.evaluate(rows, datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)).allow


def test_pre_patch_log_rows_are_understood():
    legacy = "Non-retryable HTTP 401 from https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
    rows = [row(10 + 20 * i, 0, legacy) for i in range(4)]
    d = cb.evaluate(rows, NOW)
    assert not d.allow and d.last_classification == "AUTH_UNKNOWN"


def test_non_auth_failures_and_rate_limits_do_not_open_the_breaker():
    rows = [row(10, 0, "Exhausted retries ...; HTTP 429 classification=RATE_LIMITED", "RetriesExhaustedError"),
            row(30, 0, "ParsingError: bad json", "ParsingError"),
            row(50, 0, AUTH.format("AUTH_UNKNOWN")), row(70, 0, AUTH.format("AUTH_UNKNOWN"))]
    assert cb.evaluate(rows, NOW).allow


def test_recovery_success_closes_breaker_and_skips_are_ignored():
    rows = [row(5, 1), row(10, 1, etype="SkippedTooSoon")] + [row(30 + 20 * i, 0, AUTH.format("AUTH_INVALID_KEY")) for i in range(6)]
    assert cb.evaluate(rows, NOW).state == "CLOSED"
    rows2 = [row(10, 1, etype="SkippedTooSoon")] + [row(30 + 20 * i, 0, AUTH.format("AUTH_INVALID_KEY")) for i in range(6)]
    assert cb.evaluate(rows2, NOW).state == "OPEN"


def test_operator_reset_marker(tmp_path):
    rows = [row(30 + 20 * i, 0, AUTH.format("AUTH_DEACTIVATED_KEY")) for i in range(6)]
    assert not cb.evaluate(rows, NOW).allow
    assert cb.evaluate(rows, NOW, reset_at=NOW - timedelta(minutes=5)).allow
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE collection_log (collector_name TEXT, executed_at TEXT, success INTEGER, "
                 "error_type TEXT, error_detail TEXT)")
    conn.executemany("INSERT INTO collection_log VALUES ('odds_collector', ?, ?, ?, ?)", rows)
    marker = tmp_path / "odds_breaker_reset"
    assert not cb.decide(conn, NOW, marker).allow          # no marker
    marker.write_text("reset by owner")
    assert cb.decide(conn, datetime.now(timezone.utc) + timedelta(minutes=1), marker).allow


def test_scheduler_checks_breaker_before_building_a_collector():
    src = (ROOT / "operations" / "run_collection.py").read_text()
    body = src[src.index("def check_and_run_odds"):src.index("def check_and_run_context")]
    assert body.index("odds_circuit_breaker.decide(conn)") < body.index("OddsCollector(")
    assert body.index("if not breaker.allow") < body.index("OddsCollector(")


# ---------------------------------------------------------- stale-data rules

def _market_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE games_canonical (canonical_game_id INTEGER PRIMARY KEY, sport TEXT, commence_time TEXT)")
    conn.execute("CREATE TABLE market_snapshots (canonical_game_id INTEGER, sport TEXT, book TEXT, observed_at TEXT)")
    conn.execute("CREATE TABLE collection_log (collector_name TEXT, executed_at TEXT, success INTEGER, "
                 "error_type TEXT, error_detail TEXT)")
    return conn


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_game_freshness_uses_kickoff_proximity_cadence():
    conn = _market_db()
    last_ok = datetime(2026, 10, 25, 0, 55, tzinfo=timezone.utc)
    conn.execute("INSERT INTO games_canonical VALUES (1, 'cfb', ?)", (_ts(datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)),))
    conn.execute("INSERT INTO games_canonical VALUES (2, 'nfl', ?)", (_ts(datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)),))
    conn.execute("INSERT INTO games_canonical VALUES (3, 'nfl', ?)", (_ts(datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)),))
    conn.executemany("INSERT INTO market_snapshots VALUES (?, 'x', 'dk', ?)", [(1, _ts(last_ok)), (2, _ts(last_ok))])
    # 20 minutes after the last good poll, 45 min before kickoff: 20-min tier -> limit 55 min -> FRESH
    f = mf.game_freshness(conn, 1, last_ok + timedelta(minutes=20))
    assert f["status"] == mf.FRESH and f["expected_interval_minutes"] == 20
    # 1 hour after: still pre-kickoff, highest tier -> STALE
    assert mf.game_freshness(conn, 1, last_ok + timedelta(minutes=60))["status"] == mf.STALE
    # A game a week out (daily tier): 1 day old is fresh, 3 days old is stale
    assert mf.game_freshness(conn, 2, last_ok + timedelta(days=1))["status"] == mf.FRESH
    assert mf.game_freshness(conn, 2, last_ok + timedelta(days=3))["status"] == mf.STALE
    assert mf.game_freshness(conn, 3, last_ok)["status"] == mf.NO_DATA


def test_freshness_ignores_observations_after_now():
    conn = _market_db()
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    conn.execute("INSERT INTO games_canonical VALUES (1, 'nfl', ?)", (_ts(now + timedelta(days=6)),))
    conn.execute("INSERT INTO market_snapshots VALUES (1, 'nfl', 'dk', ?)", (_ts(now + timedelta(hours=1)),))
    assert mf.game_freshness(conn, 1, now)["status"] == mf.NO_DATA


def test_sport_freshness_reports_outage():
    conn = _market_db()
    last_ok = datetime(2026, 10, 25, 0, 55, tzinfo=timezone.utc)
    conn.execute("INSERT INTO market_snapshots VALUES (1, 'cfb', 'dk', ?)", (_ts(last_ok),))
    conn.execute("INSERT INTO collection_log VALUES ('odds_collector', ?, 1, NULL, NULL)", (_ts(last_ok),))
    for i in range(1, 6):
        conn.execute("INSERT INTO collection_log VALUES ('odds_collector', ?, 0, 'NonRetryableRequestError', 'HTTP 401')",
                     (_ts(last_ok + timedelta(minutes=20 * i)),))
    conn.execute("INSERT INTO collection_log VALUES ('odds_collector', ?, 1, 'SkippedTooSoon', NULL)",
                 (_ts(last_ok + timedelta(hours=3)),))
    s = mf.sport_freshness(conn, last_ok + timedelta(days=1))
    assert s["last_successful_odds_poll"] == "2026-10-25T00:55:00Z"
    assert s["failed_odds_runs_since_last_success"] == 5
    assert s["by_sport"]["cfb"]["age_minutes"] == 1440.0


# ----------------------------------------------- owner-gated validator

class TTY(io.StringIO):
    def isatty(self):
        return True


class NoNetwork:
    def request(self, *a, **k):
        raise AssertionError("no request may be sent")


def test_validator_dry_run_never_touches_network(capsys, monkeypatch):
    monkeypatch.setattr(requests.Session, "request", NoNetwork.request)
    lines = []
    assert vc.dry_run(KEY, out=lines.append) == 0
    text = "\n".join(lines)
    assert_no_secret(text)
    assert vc.fingerprint(KEY) in text and vc.approval_token(KEY) in text
    assert vc.dry_run(None, out=lines.append) == 2


@pytest.mark.parametrize("token,stdin,code", [
    (None, TTY(vc.CONFIRM_PHRASE + "\n"), 3),
    ("wrong", TTY(vc.CONFIRM_PHRASE + "\n"), 3),
    ("TOKEN", io.StringIO(vc.CONFIRM_PHRASE + "\n"), 4),      # not interactive (cron/systemd)
    ("TOKEN", TTY("yes\n"), 5),                                # wrong phrase
])
def test_validator_refuses_without_every_gate(token, stdin, code):
    t = vc.approval_token(KEY) if token == "TOKEN" else token
    out = []
    assert vc.live_check(KEY, t, session=NoNetwork(), stdin=stdin, out=out.append) == code
    assert_no_secret("\n".join(out))


def test_validator_sends_exactly_one_safe_request_when_approved():
    s = MockSession([FakeResponse(401, {"message": f"Invalid key {KEY}", "error_code": "INVALID_KEY"},
                                  {"x-requests-remaining": "0"})])
    out = []
    code = vc.live_check(KEY, vc.approval_token(KEY), session=s, stdin=TTY(vc.CONFIRM_PHRASE + "\n"), out=out.append)
    assert code == 1 and len(s.calls) == 1
    method, url, kw = s.calls[0]
    assert method == "GET" and url == vc.SPORTS_URL and kw["allow_redirects"] is False
    result = json.loads(out[-1])
    assert result["result"] == pe.AUTH_INVALID_KEY
    assert_no_secret("\n".join(out))
    ok = MockSession([FakeResponse(200, [{"key": "americanfootball_nfl"}], {"x-requests-remaining": "0"})])
    out = []
    assert vc.live_check(KEY, vc.approval_token(KEY), session=ok, stdin=TTY(vc.CONFIRM_PHRASE + "\n"), out=out.append) == 0
    r = json.loads(out[-1])
    assert r["result"] == "KEY_ACCEPTED" and r["safe_headers"]["x-requests-remaining"] == "0"


# ------------------------------------------- dashboard surfaces staleness

def test_dashboard_api_reports_stale_market_data(monkeypatch, tmp_path):
    pytest.importorskip("flask")
    import types as _t
    db_pkg, migrate, connection = _t.ModuleType("db"), _t.ModuleType("db.migrate"), _t.ModuleType("db.connection")
    migrate.DEFAULT_DB_PATH = str(tmp_path / "web.db")
    conn = sqlite3.connect(migrate.DEFAULT_DB_PATH)
    for ddl in ("CREATE TABLE games_canonical (canonical_game_id INTEGER PRIMARY KEY, sport TEXT, commence_time TEXT)",
                "CREATE TABLE market_snapshots (canonical_game_id INTEGER, sport TEXT, book TEXT, observed_at TEXT)",
                "CREATE TABLE collection_log (collector_name TEXT, executed_at TEXT, success INTEGER, error_type TEXT, error_detail TEXT)",
                "CREATE TABLE teams_canonical (canonical_team_id TEXT, display_name TEXT)"):
        conn.execute(ddl)
    connection.get_connection = lambda path: sqlite3.connect(path)
    monkeypatch.setitem(sys.modules, "db", db_pkg)
    monkeypatch.setitem(sys.modules, "db.migrate", migrate)
    monkeypatch.setitem(sys.modules, "db.connection", connection)
    engine = _t.ModuleType("book_need.run_slate_book_need")   # web layer under test, engine stubbed
    engine.run_book_need_slate = lambda *a, **k: {"results": [], "excluded": []}
    engine._cfb_v3_top25_eligible = lambda *a, **k: True
    monkeypatch.setitem(sys.modules, "book_need.run_slate_book_need", engine)
    monkeypatch.delitem(sys.modules, "web.app", raising=False)
    import importlib
    app_mod = importlib.import_module("web.app")
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO games_canonical VALUES (7, 'nfl', ?)", (_ts(now + timedelta(hours=3)),))
    conn.execute("INSERT INTO market_snapshots VALUES (7, 'nfl', 'dk', ?)", (_ts(now - timedelta(days=1)),))
    conn.execute("INSERT INTO collection_log VALUES ('odds_collector', ?, 1, NULL, NULL)", (_ts(now - timedelta(days=1)),))
    conn.execute("INSERT INTO collection_log VALUES ('odds_collector', ?, 0, 'NonRetryableRequestError', 'HTTP 401')",
                 (_ts(now - timedelta(hours=20)),))
    conn.commit()
    fake = {"canonical_game_id": 7, "sport": "nfl", "home_team_id": "nfl_phi", "away_team_id": "nfl_dal",
            "commence_time": _ts(now + timedelta(hours=3)), "as_of": _ts(now), "public_pressure": None,
            "current_segment_name": None, "segments": [], "inferred_book_need_side": "INSUFFICIENT_EVIDENCE",
            "need_strength": None, "data_quality": "LOW", "contradiction_flag": False, "fallback_occurred": False}
    monkeypatch.setattr(app_mod, "run_book_need_slate", lambda *a, **k: {"results": [fake], "excluded": []})
    app_mod._cache.clear()
    body = app_mod.app.test_client().get("/api/slate?sport=nfl").get_json()
    game = body["games"][0]
    assert game["market_data_stale"] is True and game["market_freshness"]["status"] == mf.STALE
    assert body["stale_market_games"] == 1
    assert body["market_freshness"]["failed_odds_runs_since_last_success"] == 1
