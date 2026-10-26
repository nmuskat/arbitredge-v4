"""
Tests for the corrected collectors/run_results_backfill_dryrun.py (Fix A / Fix B).

Run with: python tests/test_backfill_dryrun.py
"""

import json
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.run_results_backfill_dryrun import (  # noqa: E402
    generate_windows, load_fbs_allowlist, analyze_raw_response, classify_side,
    static_alias_tag_report, CFB_WINDOW_START, NFL_WINDOW_START, FBS_MEMBERSHIP_PATH,
)
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_backfill_dryrun_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _seed_raw_response(conn, events):
    body = json.dumps({"events": events})
    cur = conn.execute(
        "INSERT INTO raw_responses (collector_name, source, request_url, retrieved_at, http_status, response_body) "
        "VALUES ('results_collector', 'espn', 'https://example.com', '2026-01-01T00:00:00.000000Z', 200, ?)",
        (body,),
    )
    conn.commit()
    return cur.lastrowid


def _espn_event(event_id, home_name, away_name, home_id="1", away_id="2", status="STATUS_FINAL",
                 completed=True, home_score=27, away_score=20):
    return {
        "id": event_id,
        "date": "2025-09-06T20:20Z",
        "competitions": [{
            "date": "2025-09-06T20:20Z",
            "competitors": [
                {"homeAway": "home", "team": {"id": home_id, "displayName": home_name}, "score": str(home_score)},
                {"homeAway": "away", "team": {"id": away_id, "displayName": away_name}, "score": str(away_score)},
            ],
            "status": {"period": 4, "type": {"name": status, "completed": completed, "detail": "Final"}},
        }],
        "status": {"period": 4, "type": {"name": status, "completed": completed, "detail": "Final"}},
        "season": {"year": 2025, "type": 2},
        "week": {"number": 2},
    }


# ============================================================
# Fix B: window anchor day changed
# ============================================================

def test_cfb_anchor_is_tuesday_not_saturday():
    assert CFB_WINDOW_START.weekday() == 1, "CFB window anchor must be Tuesday (weekday()==1), not Saturday"
    assert NFL_WINDOW_START.weekday() == 1, "NFL anchor is also Tuesday, for consistency"
    print("PASS: CFB window anchor moved off Saturday to Tuesday, matching NFL's scheme")


def test_generate_windows_still_gapless_at_new_anchor():
    windows = generate_windows(CFB_WINDOW_START, 16, span_days=8)
    for i in range(len(windows) - 1):
        assert windows[i][2] == windows[i + 1][1], f"gap between window {i+1} and {i+2}"
    print("PASS: no gaps across the full 16-window CFB span at the new Tuesday anchor")


def test_load_fbs_allowlist_real_file():
    allowlist = load_fbs_allowlist(2025)
    assert len(allowlist) == 136
    assert "cfb_delaware" in allowlist
    assert "cfb_montana_state" not in allowlist
    print(f"PASS: load_fbs_allowlist loads {FBS_MEMBERSHIP_PATH.name}, 136 entries, correct membership")


# ============================================================
# static_alias_tag_report -- informational only, verify it runs and reports honestly
# ============================================================

def test_static_alias_tag_report_runs_and_sums_correctly():
    conn = fresh_conn()
    allowlist = load_fbs_allowlist(2025)
    report = static_alias_tag_report(conn, allowlist)
    assert report["total_checked"] == 136
    assert report["have_espn_tag"] + report["non_espn_tag_only"] + len(report["zero_aliases"]) == 136
    assert len(report["zero_aliases"]) == 0, "every allowlisted team must have SOME alias (proven earlier)"
    conn.close()
    print(f"PASS: static_alias_tag_report runs against real seed data and its buckets sum to 136 "
          f"(espn-tagged={report['have_espn_tag']}, non-espn-only={report['non_espn_tag_only']})")


# ============================================================
# classify_side -- the core Fix A decision primitive
# ============================================================

def test_classify_side_fbs():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    status, tid = classify_side(conn, tr, "espn", "Ohio State Buckeyes", "cfb", allowlist)
    assert status == "fbs" and tid == "cfb_ohio_st"
    conn.close()
    print("PASS: classify_side correctly identifies a resolvable, allowlisted team as 'fbs'")


def test_classify_side_not_fbs_known_team():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    status, tid = classify_side(conn, tr, "espn", "Montana State Bobcats", "cfb", allowlist)
    assert status == "not_fbs" and tid == "cfb_montana_state"
    conn.close()
    print("PASS: a resolvable but non-allowlisted (known FCS) team is correctly 'not_fbs'")


def test_classify_side_not_fbs_unrecognized_no_seeding_required():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    # "Delaware State Hornets" was never seeded anywhere -- must classify without requiring seeding
    status, tid = classify_side(conn, tr, "espn", "Delaware State Hornets", "cfb", allowlist)
    assert status == "not_fbs" and tid is None
    canonical_count = conn.execute("SELECT COUNT(*) FROM teams_canonical WHERE display_name LIKE '%Delaware State%'").fetchone()[0]
    assert canonical_count == 0, "classification must not have created a canonical team for this FCS opponent"
    conn.close()
    print("PASS: an entirely unseeded FCS opponent classifies as 'not_fbs' without any canonical seeding "
          "(this is the exact false-blocker scenario from the real dry run, now fixed)")


def _insert_conflicting_alias(conn, alias_text, team_id_a, team_id_b, sport="cfb"):
    """Manually creates a genuine ambiguous alias for testing purposes,
    bypassing seed_team_data.py's conflict checker on purpose -- our real
    seed has zero conflicts by design, so 'ambiguous' resolution can only
    be exercised in tests via a deliberately constructed case like this.
    Uses two distinct sources for the same exact text, since team_aliases
    has a UNIQUE(source, alias_text) constraint -- ambiguity is detected
    by TeamResolver's cross-source exact-text step regardless."""
    conn.execute(
        "INSERT INTO team_aliases (canonical_team_id, source, alias_text) VALUES (?, 'test_source_a', ?)",
        (team_id_a, alias_text),
    )
    conn.execute(
        "INSERT INTO team_aliases (canonical_team_id, source, alias_text) VALUES (?, 'test_source_b', ?)",
        (team_id_b, alias_text),
    )
    conn.commit()


def test_classify_side_ambiguous_is_blocking():
    conn = fresh_conn()
    _insert_conflicting_alias(conn, "Ambiguous University Test", "cfb_montana_state", "cfb_ohio_st")
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    status, tid = classify_side(conn, tr, "espn", "Ambiguous University Test", "cfb", allowlist)
    assert status == "ambiguous" and tid is None
    conn.close()
    print("PASS: a genuinely ambiguous raw string (matches multiple candidates) classifies as 'ambiguous', "
          "not silently excluded")


# ============================================================
# analyze_raw_response -- full per-game verdicts, including the corrected
# "one confirmed non-FBS side rules out the game even if the other side
# is ambiguous" case
# ============================================================

def test_fbs_vs_unseeded_fcs_is_excluded_not_blocking():
    """The exact bug reported: an FBS team vs. a never-seeded FCS opponent
    must be EXCLUDED, not BLOCKING."""
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    events = [_espn_event("e1", "Ohio State Buckeyes", "Delaware State Hornets")]
    raw_id = _seed_raw_response(conn, events)

    stats = analyze_raw_response(conn, tr, raw_id, "cfb", allowlist)
    assert stats["retained"] == 0
    assert stats["excluded"] == 1
    assert len(stats["blocking"]) == 0, "this must NOT be a false blocker"
    conn.close()
    print("PASS: FBS vs. never-seeded FCS opponent is correctly EXCLUDED, not a false blocker "
          "(this is the exact scenario reported as broken)")


def test_fbs_vs_fbs_retained():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    events = [_espn_event("e1", "Ohio State Buckeyes", "Michigan Wolverines")]
    raw_id = _seed_raw_response(conn, events)

    stats = analyze_raw_response(conn, tr, raw_id, "cfb", allowlist)
    assert stats["retained"] == 1
    assert stats["excluded"] == 0
    assert len(stats["blocking"]) == 0
    assert "cfb_ohio_st" in stats["fbs_team_ids_seen"]
    assert "cfb_michigan" in stats["fbs_team_ids_seen"]
    conn.close()
    print("PASS: a real FBS-vs-FBS matchup is retained and both teams are recorded as 'seen'")


def test_ambiguous_side_with_confirmed_non_fbs_other_side_is_excluded_not_blocking():
    """The refined insight: if one side is ALREADY confirmed not-FBS, the
    game is excludable regardless of the other side's ambiguity -- no
    need to block on it."""
    conn = fresh_conn()
    _insert_conflicting_alias(conn, "Ambiguous University Test", "cfb_boise_state", "cfb_kansas")
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    # ambiguous side, but "Montana State Bobcats" is confirmed not-FBS already
    events = [_espn_event("e1", "Ambiguous University Test", "Montana State Bobcats")]
    raw_id = _seed_raw_response(conn, events)

    stats = analyze_raw_response(conn, tr, raw_id, "cfb", allowlist)
    assert stats["excluded"] == 1
    assert len(stats["blocking"]) == 0, (
        "must NOT block -- Montana State alone already rules out FBS-vs-FBS regardless of the other side's ambiguity"
    )
    conn.close()
    print("PASS: an ambiguous side paired with an already-confirmed-non-FBS side is excluded, not blocked "
          "(one confirmed non-FBS side is sufficient to rule out the game)")


def test_ambiguous_side_with_confirmed_fbs_other_side_is_blocking():
    conn = fresh_conn()
    _insert_conflicting_alias(conn, "Ambiguous University Test", "cfb_boise_state", "cfb_kansas")
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    # ambiguous side, other side IS confirmed FBS -- genuinely can't tell, must block
    events = [_espn_event("e1", "Ambiguous University Test", "Ohio State Buckeyes")]
    raw_id = _seed_raw_response(conn, events)

    stats = analyze_raw_response(conn, tr, raw_id, "cfb", allowlist)
    assert stats["retained"] == 0
    assert stats["excluded"] == 0
    assert len(stats["blocking"]) == 1
    conn.close()
    print("PASS: an ambiguous side paired with a confirmed-FBS side is correctly BLOCKING "
          "(the only case where the verdict is genuinely undetermined)")


def test_non_terminal_game_not_counted():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    events = [_espn_event("e1", "Ohio State Buckeyes", "Michigan Wolverines",
                           status="STATUS_IN_PROGRESS", completed=False)]
    raw_id = _seed_raw_response(conn, events)

    stats = analyze_raw_response(conn, tr, raw_id, "cfb", allowlist)
    assert stats["terminal"] == 0
    assert stats["retained"] == 0
    conn.close()
    print("PASS: non-terminal games remain excluded from all downstream counts")


# ============================================================
# Fix B: overlap-duplicate accounting
# ============================================================

def test_overlap_duplicate_counted_once_in_unique_set_but_twice_in_raw_sum():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    allowlist = load_fbs_allowlist(2025)
    # Simulate the SAME real game appearing in two consecutive windows'
    # raw responses (the boundary-day overlap scenario)
    events = [_espn_event("boundary_game_1", "Ohio State Buckeyes", "Michigan Wolverines")]
    raw_id_week1 = _seed_raw_response(conn, events)
    raw_id_week2 = _seed_raw_response(conn, events)  # identical event, second "window"

    stats_week1 = analyze_raw_response(conn, tr, raw_id_week1, "cfb", allowlist)
    stats_week2 = analyze_raw_response(conn, tr, raw_id_week2, "cfb", allowlist)

    raw_sum = stats_week1["retained"] + stats_week2["retained"]
    unique_ids = set(stats_week1["retained_espn_game_ids"]) | set(stats_week2["retained_espn_game_ids"])

    assert raw_sum == 2, "raw per-window sum double-counts the boundary game, as expected/disclosed"
    assert len(unique_ids) == 1, "the UNIQUE set must collapse it to one real game"
    conn.close()
    print("PASS: a boundary-overlap game inflates the raw per-window sum but collapses correctly "
          "to one game in the authoritative unique set")


if __name__ == "__main__":
    test_cfb_anchor_is_tuesday_not_saturday()
    test_generate_windows_still_gapless_at_new_anchor()
    test_load_fbs_allowlist_real_file()
    test_static_alias_tag_report_runs_and_sums_correctly()
    test_classify_side_fbs()
    test_classify_side_not_fbs_known_team()
    test_classify_side_not_fbs_unrecognized_no_seeding_required()
    test_classify_side_ambiguous_is_blocking()
    test_fbs_vs_unseeded_fcs_is_excluded_not_blocking()
    test_fbs_vs_fbs_retained()
    test_ambiguous_side_with_confirmed_non_fbs_other_side_is_excluded_not_blocking()
    test_ambiguous_side_with_confirmed_fbs_other_side_is_blocking()
    test_non_terminal_game_not_counted()
    test_overlap_duplicate_counted_once_in_unique_set_but_twice_in_raw_sum()
    print("\nAll corrected backfill dry-run tests passed.")
