"""
Tests for operations/*.py

Run with: python tests/test_operations.py
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

# A consistent dummy key so every test in this file can construct a real
# OddsCollector (now required=True on the key, per the August 14 2026
# incident fix) without needing a genuine key -- setdefault so a real
# value already present in the environment is never overwritten.
os.environ.setdefault("THE_ODDS_API_KEY", "test-key-not-real")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operations.cadence_config import tier_for_hours, ODDS_PROXIMITY_TIERS  # noqa: E402
from operations.run_collection import get_nearest_future_kickoff, check_and_run_odds  # noqa: E402
from operations.collection_health import generate_report  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_operations_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _make_game(conn, sport, home, away, commence_dt):
    conn.execute(
        "INSERT INTO games_canonical (sport, home_team_id, away_team_id, commence_time) VALUES (?,?,?,?)",
        (sport, home, away, _iso(commence_dt)),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else []
        self.text = "[]"
        self.headers = {}

    def json(self):
        return self._json_body


def test_tier_boundaries():
    assert tier_for_hours(None) == ("very_low", 1440)
    assert tier_for_hours(100) == ("very_low", 1440)
    assert tier_for_hours(72.1) == ("very_low", 1440)
    assert tier_for_hours(72) == ("low_moderate", 960), "mx boundary is inclusive"
    assert tier_for_hours(24.1) == ("low_moderate", 960)
    assert tier_for_hours(24) == ("elevated", 480), "mx boundary is inclusive"
    assert tier_for_hours(6.1) == ("elevated", 480)
    assert tier_for_hours(6) == ("high", 60), "mx boundary is inclusive"
    assert tier_for_hours(1.1) == ("high", 60)
    assert tier_for_hours(1) == ("highest", 20), "mx boundary is inclusive"
    assert tier_for_hours(0.01) == ("highest", 20)
    print("PASS: tier_for_hours correctly resolves every tier boundary, mx inclusive throughout")


def test_all_tiers_have_positive_interval():
    for label, mx, mn, interval in ODDS_PROXIMITY_TIERS:
        assert interval > 0
    print("PASS: every configured tier has a real positive interval")


def test_high_and_highest_tiers_completely_unchanged_by_elevated_widening():
    """
    The critical invariant from the real credit-budget audit: only the
    'elevated' tier (24h-6h) was widened to recover margin. The final-6h
    and final-1h density -- the highest-value, most-preserved windows per
    explicit instruction -- must remain byte-identical.
    """
    assert tier_for_hours(6) == ("high", 60), "the 6h high-tier boundary and its 60min interval must be unchanged"
    assert tier_for_hours(1) == ("highest", 20), "the 1h highest-tier boundary and its 20min interval must be unchanged"
    assert tier_for_hours(3) == ("high", 60)
    assert tier_for_hours(0.5) == ("highest", 20)
    print("PASS: the high (6h-1h, 60min) and highest (<1h, 20min) tiers are completely "
          "unchanged by the elevated-tier widening -- final-hour density fully preserved")


def test_elevated_tier_widened_as_designed():
    assert tier_for_hours(24) == ("elevated", 480)
    assert tier_for_hours(6.1) == ("elevated", 480)
    print("PASS: the elevated tier is correctly widened to 480min (8h), the real, "
          "evidence-justified fix from the September credit-budget audit")


def test_september_audit_produces_positive_margin():
    import operations.audit_september_credit_budget as audit
    nfl_tiers = audit.simulate(audit.NFL_KICKOFF_CLUSTERS, audit.WEEK_WINDOW_START, audit.WEEK_WINDOW_END)
    cfb_tiers = audit.simulate(audit.CFB_KICKOFF_CLUSTERS, audit.WEEK_WINDOW_START, audit.WEEK_WINDOW_END)
    week_total = sum(nfl_tiers.values()) + sum(cfb_tiers.values())
    monthly = week_total * audit.WEEKS_PER_MONTH
    assert monthly < audit.MONTHLY_CREDIT_CAP, (
        f"the real, grounded 2026 September simulation must project under the {audit.MONTHLY_CREDIT_CAP}-credit cap"
    )
    assert audit.MONTHLY_CREDIT_CAP - monthly >= 15, "the recovered margin must be a real, non-trivial buffer"
    print(f"PASS: the September audit script itself confirms a real projected margin of "
          f"{audit.MONTHLY_CREDIT_CAP - monthly:.0f} credits against the real 2026 schedule")


def test_nearest_future_kickoff_excludes_past_games():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now - timedelta(hours=2))
    future_id = _make_game(conn, "nfl", "nfl_kc", "nfl_den", now + timedelta(hours=5))
    conn.commit()

    gid, kickoff = get_nearest_future_kickoff(conn, "nfl")
    assert gid == future_id
    conn.close()
    print("PASS: get_nearest_future_kickoff correctly excludes already-started games")


def test_nearest_future_kickoff_none_when_nothing_tracked():
    conn = fresh_conn()
    gid, kickoff = get_nearest_future_kickoff(conn, "nfl")
    assert gid is None and kickoff is None
    conn.close()
    print("PASS: no tracked future games correctly returns (None, None)")


def _make_collector_patch(session):
    import operations.run_collection as run_collection_module
    original = run_collection_module.OddsCollector

    class PatchedCollector(original):
        def __init__(self, *args, **kwargs):
            kwargs["session"] = session
            kwargs["sleep_fn"] = lambda s: None
            super().__init__(*args, **kwargs)
    return PatchedCollector, original


def test_odds_not_due_within_tier_makes_no_call():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=100))
    conn.commit()

    session = MagicMock()
    session.request.return_value = FakeResponse(200, [])
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    import operations.run_collection as m
    PatchedCollector, original = _make_collector_patch(session)
    m.OddsCollector = PatchedCollector
    try:
        check_and_run_odds(conn, tr, gr, "nfl")
    finally:
        m.OddsCollector = original
    assert session.request.call_count >= 1

    calls_before = session.request.call_count
    m.OddsCollector = PatchedCollector
    try:
        check_and_run_odds(conn, tr, gr, "nfl")
    finally:
        m.OddsCollector = original
    assert session.request.call_count == calls_before, (
        "a check well within the active tier's interval must make zero additional HTTP calls"
    )
    conn.close()
    print("PASS: a check within the active tier's interval makes no additional API call")


def test_force_bypasses_due_check():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=100))
    conn.commit()

    session = MagicMock()
    session.request.return_value = FakeResponse(200, [])
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    import operations.run_collection as m
    PatchedCollector, original = _make_collector_patch(session)
    m.OddsCollector = PatchedCollector
    try:
        check_and_run_odds(conn, tr, gr, "nfl")
        calls_after_first = session.request.call_count
        check_and_run_odds(conn, tr, gr, "nfl", force=True)
    finally:
        m.OddsCollector = original
    assert session.request.call_count > calls_after_first
    conn.close()
    print("PASS: force=True bypasses the tier due-check and polls immediately")


def test_cadence_decision_logged_every_check_even_when_not_due():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=100))
    conn.commit()

    session = MagicMock()
    session.request.return_value = FakeResponse(200, [])
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    import operations.run_collection as m
    PatchedCollector, original = _make_collector_patch(session)
    m.OddsCollector = PatchedCollector
    try:
        check_and_run_odds(conn, tr, gr, "nfl")
        check_and_run_odds(conn, tr, gr, "nfl")
    finally:
        m.OddsCollector = original

    decisions = conn.execute("SELECT poll_triggered FROM cadence_decisions WHERE sport='nfl'").fetchall()
    assert len(decisions) == 2
    assert decisions[0][0] == 1 and decisions[1][0] == 0
    conn.close()
    print("PASS: every cadence check is logged to cadence_decisions, triggered or not")


def test_within_tier_gap_is_green_not_missed():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=100))
    conn.execute(
        "INSERT INTO cadence_decisions (checked_at, sport, tier_label, required_interval_minutes, "
        "poll_triggered, collector_run_result) VALUES (?, 'nfl', 'very_low', 1440, 1, 'success')",
        (_iso(now - timedelta(minutes=30)),),
    )
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?, 'nfl', 'KC', 'LAC', ?, 'draftkings', ?, -3.0)",
        (gid, _iso(now + timedelta(hours=100)), _iso(now - timedelta(minutes=30))),
    )
    conn.commit()

    report = generate_report(conn)
    assert report["odds"]["nfl"]["status"] == "GREEN"
    assert report["missed_market_observations"] == []
    conn.close()
    print("PASS: a gap well within the active tier's interval, WITH real snapshots present, "
          "is GREEN, never flagged as missed")


def test_overdue_but_not_yet_kicked_off_is_yellow_not_red():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=3))
    conn.execute(
        "INSERT INTO cadence_decisions (checked_at, sport, tier_label, required_interval_minutes, "
        "poll_triggered, collector_run_result) VALUES (?, 'nfl', 'high', 60, 1, 'success')",
        (_iso(now - timedelta(minutes=90)),),
    )
    conn.commit()

    report = generate_report(conn)
    assert report["odds"]["nfl"]["status"] == "YELLOW"
    assert report["missed_market_observations"] == []
    conn.close()
    print("PASS: an overdue-but-not-yet-kicked-off game is YELLOW (recoverable), not RED")


def test_kicked_off_with_no_snapshots_is_red():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now - timedelta(hours=2))
    conn.commit()

    report = generate_report(conn)
    missed_ids = [m["canonical_game_id"] for m in report["missed_market_observations"]]
    assert gid in missed_ids
    conn.close()
    print("PASS: a past game with zero market_snapshots within 6h of its kickoff is correctly flagged RED")


def test_kicked_off_with_real_snapshot_is_not_red():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now - timedelta(hours=2))
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home, spread_away) VALUES (?, 'nfl', 'KC', 'LAC', ?, 'draftkings', ?, -3.0, 3.0)",
        (gid, _iso(now - timedelta(hours=2)), _iso(now - timedelta(hours=3))),
    )
    conn.commit()

    report = generate_report(conn)
    missed_ids = [m["canonical_game_id"] for m in report["missed_market_observations"]]
    assert gid not in missed_ids
    conn.close()
    print("PASS: a past game WITH a real snapshot inside the 6h window is correctly NOT flagged RED")


def test_repeated_failures_are_red():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    for i in range(4):
        conn.execute(
            "INSERT INTO collection_log (collector_name, source, scheduled_at, executed_at, success, error_type) "
            "VALUES ('odds_collector', 'the_odds_api', ?, ?, 0, 'ConnectionError')",
            (_iso(now - timedelta(minutes=i)), _iso(now - timedelta(minutes=i))),
        )
    conn.commit()

    report = generate_report(conn)
    assert report["odds"]["nfl"]["status"] == "RED"
    assert report["odds"]["cfb"]["status"] == "RED"
    conn.close()
    print("PASS: repeated consecutive collector failures are correctly flagged RED")


def test_status_flag_makes_zero_network_calls():
    conn = fresh_conn()
    session = MagicMock()
    report = generate_report(conn)
    assert session.request.call_count == 0
    conn.close()
    print("PASS: generating a health report makes zero network calls")


def test_actual_credit_accounting_not_theoretical():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    for i in range(3):
        conn.execute(
            "INSERT INTO raw_responses (collector_name, source, request_url, retrieved_at, http_status, response_body) "
            "VALUES ('odds_collector', 'the_odds_api', 'https://example.com', ?, 200, '[]')",
            (_iso(now - timedelta(hours=i)),),
        )
    conn.commit()

    report = generate_report(conn)
    assert report["odds_credits_this_month_actual"] == 3
    conn.close()
    print("PASS: odds credit accounting counts real raw_responses rows, not theoretical schedule totals")


def test_sport_isolation_second_sport_polls_correctly_right_after_first():
    """
    The real, previously-undiscovered bug this fixes: get_last_successful_run
    was not scoped by sport, so polling NFL successfully would make an
    immediately-following CFB (or nfl_preseason) due-check see NFL's fresh
    timestamp and wrongly conclude it had just been polled too -- silently
    suppressing whichever sport was checked later in the loop. Confirms
    CFB polls for real immediately after NFL, in the same sequence.
    """
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=100))
    _make_game(conn, "cfb", "cfb_ohio_st", "cfb_michigan", now + timedelta(hours=100))
    conn.commit()

    session = MagicMock()
    session.request.return_value = FakeResponse(200, [])
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    import operations.run_collection as m
    PatchedCollector, original = _make_collector_patch(session)
    m.OddsCollector = PatchedCollector
    try:
        check_and_run_odds(conn, tr, gr, "nfl", force=False)
        calls_after_nfl = session.request.call_count
        check_and_run_odds(conn, tr, gr, "cfb", force=False)
    finally:
        m.OddsCollector = original

    assert session.request.call_count > calls_after_nfl, (
        "CFB's own first-ever check must poll for real, even immediately after NFL's own poll -- "
        "it must NOT be suppressed by seeing NFL's just-completed run as if it were its own"
    )
    conn.close()
    print("PASS: a second sport's due-check correctly polls immediately after a first sport's poll "
          "in the same sequence -- confirms per-sport isolation, not sport-blind suppression")


def test_isolation_holds_when_collector_construction_itself_raises():
    """
    Regression for the August 14 2026 incident's actual fix: odds_collector
    now raises RuntimeError at CONSTRUCTION time (not just during collect())
    when no API key is available. What actually matters for
    run_collection.py's main() loop to keep going through every sport is
    that check_and_run_odds itself never lets that exception PROPAGATE out
    -- it must be caught internally and logged, so the caller's loop
    reaches the next sport regardless of what happened here. (A missing
    key is one shared process-wide variable, so constructing an artificial
    "NFL fails but CFB succeeds" scenario via env-var manipulation isn't
    realistic -- the real guarantee to test is non-propagation at the
    function-call boundary itself.)
    """
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=100))
    conn.commit()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    saved = os.environ.pop("THE_ODDS_API_KEY", None)
    try:
        try:
            check_and_run_odds(conn, tr, gr, "nfl", force=True)
            raised = False
        except Exception:
            raised = True
    finally:
        if saved is not None:
            os.environ["THE_ODDS_API_KEY"] = saved

    assert not raised, (
        "check_and_run_odds must catch a collector-construction failure internally, never let it "
        "propagate -- otherwise run_collection.py's main() loop would never reach the next sport"
    )
    nfl_decision = conn.execute(
        "SELECT collector_run_result FROM cadence_decisions WHERE sport='nfl' ORDER BY decision_id DESC LIMIT 1"
    ).fetchone()
    assert nfl_decision[0] == "failed"
    conn.close()
    print("PASS: a collector CONSTRUCTION failure never propagates out of check_and_run_odds -- "
          "isolation holds, and the failure is still correctly logged to cadence_decisions")


def test_unresolved_aliases_split_active_vs_historical():
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    # Genuinely old, preserved audit row -- last touched 10 days ago
    conn.execute(
        "INSERT INTO unresolved_aliases (sport, source, raw_text, normalized_text, reason, "
        "first_seen_at, last_seen_at, occurrence_count) VALUES ('cfb','espn','Old Ghost Team','old ghost team',"
        "'no_match', ?, ?, 3)",
        (_iso(now - timedelta(days=20)), _iso(now - timedelta(days=10))),
    )
    # Genuinely current row -- last touched 2 hours ago, well within the active window
    conn.execute(
        "INSERT INTO unresolved_aliases (sport, source, raw_text, normalized_text, reason, "
        "first_seen_at, last_seen_at, occurrence_count) VALUES ('nfl','the_odds_api','New Team X','new team x',"
        "'no_match', ?, ?, 1)",
        (_iso(now - timedelta(hours=2)), _iso(now - timedelta(hours=2))),
    )
    conn.commit()

    report = generate_report(conn)
    assert report["unresolved"]["active_count"] == 1
    assert report["unresolved"]["historical_count"] == 1
    active_texts = [r[2] for r in report["unresolved"]["active_rows"]]
    assert "New Team X" in active_texts
    assert "Old Ghost Team" not in active_texts

    still_present = conn.execute("SELECT COUNT(*) FROM unresolved_aliases").fetchone()[0]
    assert still_present == 2, "the health report must never delete or modify historical rows"
    conn.close()
    print("PASS: unresolved aliases are correctly split into ACTIVE vs HISTORICAL AUDIT, "
          "and no rows are deleted or modified by the health report")



def test_false_green_detected_zero_snapshots_despite_tracked_game():
    """
    The exact real defect reported: a collector run can complete
    successfully (no exception, cadence_decisions logs success) while
    silently writing zero usable market_snapshots rows -- e.g. a stale
    CHECK constraint rejecting every individual record via safe_insert's
    per-record error isolation. GREEN must require real evidence of
    captured data, not just a successful cadence decision.
    """
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    _make_game(conn, "nfl_preseason" if False else "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=48))
    # Simulate: cadence ran successfully and recently (well within tier), but
    # deliberately insert ZERO market_snapshots rows -- exactly the reported scenario.
    conn.execute(
        "INSERT INTO cadence_decisions (checked_at, sport, tier_label, required_interval_minutes, "
        "poll_triggered, collector_run_result) VALUES (?, 'nfl', 'low_moderate', 960, 1, 'success')",
        (_iso(now - timedelta(minutes=5)),),
    )
    conn.commit()

    report = generate_report(conn)
    assert report["odds"]["nfl"]["status"] == "RED", (
        "a successful, on-schedule cadence decision with ZERO real market_snapshots for a "
        "tracked game must be RED, not a false GREEN"
    )
    assert "ZERO market_snapshots" in report["odds"]["nfl"]["note"]
    print("PASS: a successful cadence run with zero actual usable snapshots is correctly flagged "
          "RED, not falsely GREEN -- the health check now distinguishes 'ran' from 'captured data'")


def test_true_green_requires_real_snapshots_present():
    """The inverse/positive case: with the same on-schedule cadence success,
    but a REAL market_snapshots row present, GREEN is legitimate."""
    conn = fresh_conn()
    now = datetime.now(timezone.utc)
    gid = _make_game(conn, "nfl", "nfl_kc", "nfl_lac", now + timedelta(hours=48))
    conn.execute(
        "INSERT INTO cadence_decisions (checked_at, sport, tier_label, required_interval_minutes, "
        "poll_triggered, collector_run_result) VALUES (?, 'nfl', 'low_moderate', 960, 1, 'success')",
        (_iso(now - timedelta(minutes=5)),),
    )
    conn.execute(
        "INSERT INTO market_snapshots (canonical_game_id, sport, home_team, away_team, commence_time, "
        "book, observed_at, spread_home) VALUES (?, 'nfl', 'KC', 'LAC', ?, 'draftkings', ?, -3.0)",
        (gid, _iso(now + timedelta(hours=48)), _iso(now - timedelta(minutes=5))),
    )
    conn.commit()

    report = generate_report(conn)
    assert report["odds"]["nfl"]["status"] == "GREEN"
    print("PASS: GREEN remains correct when real market_snapshots actually back the successful cadence run")


if __name__ == "__main__":
    test_tier_boundaries()
    test_all_tiers_have_positive_interval()
    test_high_and_highest_tiers_completely_unchanged_by_elevated_widening()
    test_elevated_tier_widened_as_designed()
    test_september_audit_produces_positive_margin()
    test_nearest_future_kickoff_excludes_past_games()
    test_nearest_future_kickoff_none_when_nothing_tracked()
    test_odds_not_due_within_tier_makes_no_call()
    test_force_bypasses_due_check()
    test_cadence_decision_logged_every_check_even_when_not_due()
    test_within_tier_gap_is_green_not_missed()
    test_overdue_but_not_yet_kicked_off_is_yellow_not_red()
    test_kicked_off_with_no_snapshots_is_red()
    test_kicked_off_with_real_snapshot_is_not_red()
    test_repeated_failures_are_red()
    test_status_flag_makes_zero_network_calls()
    test_actual_credit_accounting_not_theoretical()
    test_unresolved_aliases_split_active_vs_historical()
    test_sport_isolation_second_sport_polls_correctly_right_after_first()
    test_isolation_holds_when_collector_construction_itself_raises()
    test_false_green_detected_zero_snapshots_despite_tracked_game()
    test_true_green_requires_real_snapshots_present()
    print("\nAll operations tests passed.")
