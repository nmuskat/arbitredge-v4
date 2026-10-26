"""
Tests for identity/team_resolver.py and identity/game_resolver.py.

Run with: python tests/test_identity_resolution.py
"""

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402

TEST_DIR = Path(__file__).parent / "_identity_test_workspace"


def fresh_conn() -> sqlite3.Connection:
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ============================================================
# TeamResolver tests
# ============================================================

def test_nfl_chargers_variants_resolve_to_same_team():
    conn = fresh_conn()
    tr = TeamResolver(conn)

    r1 = tr.resolve("the_odds_api", "Los Angeles Chargers", "nfl")
    r2 = tr.resolve("vegasinsider", "LA Chargers", "nfl")
    r3 = tr.resolve("common", "LAC", "nfl")

    assert r1 == r2 == r3 == "nfl_lac"
    conn.close()
    print("PASS: LA Chargers / Los Angeles Chargers / LAC all resolve to nfl_lac")


def test_nfl_common_abbreviation_variants():
    conn = fresh_conn()
    tr = TeamResolver(conn)

    assert tr.resolve("common", "KC", "nfl") == "nfl_kc"
    assert tr.resolve("the_odds_api", "Kansas City Chiefs", "nfl") == "nfl_kc"
    assert tr.resolve("common", "Chiefs", "nfl") == "nfl_kc"
    assert tr.resolve("common", "SF", "nfl") == "nfl_sf"
    assert tr.resolve("common", "49ers", "nfl") == "nfl_sf"
    assert tr.resolve("common", "GB", "nfl") == "nfl_gb"
    conn.close()
    print("PASS: common NFL abbreviation/name variants resolve correctly")


def test_cfb_punctuation_and_abbreviation_variants():
    conn = fresh_conn()
    tr = TeamResolver(conn)

    # Punctuation differences (period on "St.")
    assert tr.resolve("common", "Ohio State", "cfb") == "cfb_ohio_st"
    assert tr.resolve("common", "Ohio St.", "cfb") == "cfb_ohio_st"
    # Case/whitespace-only variant, exercised through normalization (step 3),
    # not present verbatim in the seed data
    assert tr.resolve("common", "ohio state", "cfb") == "cfb_ohio_st"
    assert tr.resolve("common", "  Ohio   State  ", "cfb") == "cfb_ohio_st"
    # Abbreviation
    assert tr.resolve("common", "NC State", "cfb") == "cfb_nc_state"
    assert tr.resolve("common", "North Carolina State", "cfb") == "cfb_nc_state"
    # Ampersand normalization
    assert tr.resolve("common", "Texas A&M", "cfb") == "cfb_texas_am"
    conn.close()
    print("PASS: CFB punctuation/abbreviation variants resolve correctly")


def test_cfb_similar_names_not_merged():
    conn = fresh_conn()
    tr = TeamResolver(conn)

    # Ohio vs Ohio State -- must NOT collapse into the same team
    ohio = tr.resolve("common", "Ohio", "cfb")
    ohio_state = tr.resolve("common", "Ohio State", "cfb")
    assert ohio == "cfb_ohio"
    assert ohio_state == "cfb_ohio_st"
    assert ohio != ohio_state

    # Oklahoma vs Oklahoma State -- must NOT collapse
    oklahoma = tr.resolve("common", "Oklahoma", "cfb")
    oklahoma_state = tr.resolve("common", "Oklahoma State", "cfb")
    assert oklahoma == "cfb_oklahoma"
    assert oklahoma_state == "cfb_oklahoma_st"
    assert oklahoma != oklahoma_state

    # Miami (FL) vs Miami (OH) -- must NOT collapse
    miami_fl = tr.resolve("common", "Miami (FL)", "cfb")
    miami_oh = tr.resolve("common", "Miami (OH)", "cfb")
    assert miami_fl == "cfb_miami_fl"
    assert miami_oh == "cfb_miami_oh"
    assert miami_fl != miami_oh

    conn.close()
    print("PASS: genuinely similar CFB names (Ohio/Ohio State, Oklahoma/Oklahoma State, "
          "Miami FL/OH) are correctly kept distinct")


def test_unknown_and_deliberately_ambiguous_aliases_are_unresolved_not_guessed():
    conn = fresh_conn()
    tr = TeamResolver(conn)

    # Completely unknown text
    result = tr.resolve("common", "Not A Real Team XYZ", "cfb")
    assert result is None

    # Deliberately NOT seeded because ambiguous: bare "Miami"
    result_miami = tr.resolve("common", "Miami", "cfb")
    assert result_miami is None, "bare 'Miami' must not be guessed between FL and OH"

    # Deliberately NOT seeded because ambiguous: "OSU"
    result_osu = tr.resolve("common", "OSU", "cfb")
    assert result_osu is None, "'OSU' must not be guessed among Ohio State/Oklahoma State/Oregon State"

    # Confirm these were logged for later review, with occurrence counting
    tr.resolve("common", "Not A Real Team XYZ", "cfb")  # trigger a second sighting
    row = conn.execute(
        "SELECT occurrence_count, reason FROM unresolved_aliases "
        "WHERE source = 'common' AND raw_text = 'Not A Real Team XYZ'"
    ).fetchone()
    assert row is not None, "unresolved alias should be logged"
    assert row[0] == 2, "second sighting should increment occurrence_count"
    assert row[1] == "no_match"

    miami_row = conn.execute(
        "SELECT reason FROM unresolved_aliases WHERE raw_text = 'Miami'"
    ).fetchone()
    assert miami_row is not None

    conn.close()
    print("PASS: unknown and deliberately ambiguous aliases resolve to None and are logged, never guessed")


# ============================================================
# GameResolver tests
# ============================================================

def test_same_game_two_sources_one_canonical_two_map_rows():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    cg1 = gr.resolve_or_create(
        source="the_odds_api",
        source_game_key="odds_api_evt_001",
        sport="nfl",
        home_team_raw="Kansas City Chiefs",
        away_team_raw="Los Angeles Chargers",
        commence_time_raw="2026-09-10T20:20:00Z",
    )
    cg2 = gr.resolve_or_create(
        source="vegasinsider",
        source_game_key="vi_evt_kc_lac_wk1",
        sport="nfl",
        home_team_raw="Kansas City Chiefs",
        away_team_raw="LA Chargers",
        # slightly different reported time -- within tolerance
        commence_time_raw="2026-09-10T20:25:00Z",
    )

    assert cg1 is not None and cg2 is not None
    assert cg1 == cg2, "same real-world game from two sources must map to one canonical game"

    map_rows = conn.execute(
        "SELECT source FROM game_id_map WHERE canonical_game_id = ? ORDER BY source",
        (cg1,),
    ).fetchall()
    assert len(map_rows) == 2
    assert {r[0] for r in map_rows} == {"the_odds_api", "vegasinsider"}

    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert canonical_count == 1, "must not have created two canonical games"

    conn.close()
    print("PASS: same game from two sources -> one canonical game, two game_id_map rows")


def test_duplicate_ingestion_same_source_game_is_idempotent():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    cg1 = gr.resolve_or_create(
        "the_odds_api", "evt_dup_001", "nfl",
        "Buffalo Bills", "Miami Dolphins", "2026-09-14T17:00:00Z",
    )
    cg2 = gr.resolve_or_create(
        "the_odds_api", "evt_dup_001", "nfl",
        "Buffalo Bills", "Miami Dolphins", "2026-09-14T17:00:00Z",
    )
    cg3 = gr.resolve_or_create(
        "the_odds_api", "evt_dup_001", "nfl",
        "Buffalo Bills", "Miami Dolphins", "2026-09-14T17:00:00Z",
    )

    assert cg1 == cg2 == cg3

    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    map_count = conn.execute("SELECT COUNT(*) FROM game_id_map").fetchone()[0]
    assert canonical_count == 1
    assert map_count == 1, "re-ingesting the same source_game_key must not create duplicate map rows"

    conn.close()
    print("PASS: duplicate ingestion of the same source game is idempotent")


def test_different_kickoff_same_teams_creates_separate_canonical_game():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    # Week 1 meeting
    cg1 = gr.resolve_or_create(
        "the_odds_api", "evt_wk1", "nfl",
        "Kansas City Chiefs", "Los Angeles Chargers", "2026-09-10T20:20:00Z",
    )
    # Divisional rematch weeks later -- same two teams, materially different time
    cg2 = gr.resolve_or_create(
        "the_odds_api", "evt_wk15", "nfl",
        "Kansas City Chiefs", "Los Angeles Chargers", "2026-12-20T18:00:00Z",
    )

    assert cg1 is not None and cg2 is not None
    assert cg1 != cg2, "materially different kickoff between the same two teams must be a separate game"

    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert canonical_count == 2

    conn.close()
    print("PASS: materially different kickoff between same teams -> separate canonical game")


def test_home_away_order_mismatch_is_recorded_not_assumed():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    cg1 = gr.resolve_or_create(
        "the_odds_api", "evt_order_001", "nfl",
        home_team_raw="Kansas City Chiefs", away_team_raw="Los Angeles Chargers",
        commence_time_raw="2026-09-10T20:20:00Z",
    )
    # A second source reports the SAME game but with home/away reversed
    cg2 = gr.resolve_or_create(
        "actionnetwork", "an_evt_order_001", "nfl",
        home_team_raw="Los Angeles Chargers", away_team_raw="Kansas City Chiefs",
        commence_time_raw="2026-09-10T20:20:00Z",
    )

    assert cg1 == cg2, "should still be recognized as the same game regardless of order"

    order_flags = dict(
        conn.execute(
            "SELECT source, home_away_matches_canonical FROM game_id_map WHERE canonical_game_id = ?",
            (cg1,),
        ).fetchall()
    )
    assert order_flags["the_odds_api"] == 1, "first-seen source establishes canonical order"
    assert order_flags["actionnetwork"] == 0, "reversed-order source must be flagged, not silently normalized"

    conn.close()
    print("PASS: home/away order mismatch across sources is detected and recorded, never assumed")


def test_reschedule_preserves_canonical_identity_and_logs_change():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    cg1 = gr.resolve_or_create(
        "the_odds_api", "evt_reschedule_001", "nfl",
        "Philadelphia Eagles", "Dallas Cowboys", "2026-11-01T18:00:00Z",
    )

    # Same source_game_key, but the game gets postponed to a materially
    # later time (e.g. weather delay) -- must be treated as a reschedule
    # of the SAME canonical game, not a new one.
    cg2 = gr.resolve_or_create(
        "the_odds_api", "evt_reschedule_001", "nfl",
        "Philadelphia Eagles", "Dallas Cowboys", "2026-11-02T17:00:00Z",
    )

    assert cg1 == cg2, "reschedule of the same source_game_key must preserve canonical_game_id"

    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert canonical_count == 1

    updated_time = conn.execute(
        "SELECT commence_time FROM games_canonical WHERE canonical_game_id = ?", (cg1,)
    ).fetchone()[0]
    assert updated_time == "2026-11-02T17:00:00Z", "canonical commence_time should reflect the reschedule"

    log_rows = conn.execute(
        "SELECT previous_commence_time, new_commence_time FROM game_reschedule_log "
        "WHERE canonical_game_id = ?", (cg1,)
    ).fetchall()
    assert len(log_rows) == 1
    assert log_rows[0][0] == "2026-11-01T18:00:00Z"
    assert log_rows[0][1] == "2026-11-02T17:00:00Z"

    conn.close()
    print("PASS: reschedule of the same source game preserves canonical identity and is logged, not destroyed")


def test_reschedule_collision_reconciles_to_existing_linked_canonical():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    # Source A first sees the game at an early placeholder kickoff.
    survivor_id = gr.resolve_or_create(
        "the_odds_api", "evt_collision_odds", "nfl",
        "Kansas City Chiefs", "Los Angeles Chargers",
        "2026-09-10T16:00:00Z",
    )

    # Source B independently reports the same teams at a kickoff more than
    # 90 minutes away, so at this moment a second canonical is legitimate.
    duplicate_id = gr.resolve_or_create(
        "actionnetwork", "evt_collision_an", "nfl",
        "Kansas City Chiefs", "Los Angeles Chargers",
        "2026-09-10T23:30:00Z",
    )

    assert survivor_id is not None and duplicate_id is not None
    assert survivor_id != duplicate_id

    # Source A later reports that its stable event key was rescheduled onto
    # Source B's kickoff. This proves the two canonicals have converged.
    resolved_id = gr.resolve_or_create(
        "the_odds_api", "evt_collision_odds", "nfl",
        "Kansas City Chiefs", "Los Angeles Chargers",
        "2026-09-10T23:30:00Z",
    )

    assert resolved_id == survivor_id

    redirect = conn.execute(
        "SELECT survivor_game_id FROM game_canonical_redirects "
        "WHERE duplicate_game_id = ?",
        (duplicate_id,),
    ).fetchone()
    assert redirect == (survivor_id,), (
        "the collided canonical must be redirected to the stable-source survivor"
    )

    survivor_time = conn.execute(
        "SELECT commence_time FROM games_canonical WHERE canonical_game_id = ?",
        (survivor_id,),
    ).fetchone()[0]
    assert survivor_time == "2026-09-10T23:30:00Z"

    log_rows = conn.execute(
        "SELECT previous_commence_time, new_commence_time "
        "FROM game_reschedule_log WHERE canonical_game_id = ?",
        (survivor_id,),
    ).fetchall()
    assert log_rows == [("2026-09-10T16:00:00Z", "2026-09-10T23:30:00Z")]

    # Historical source mapping remains untouched for provenance.
    duplicate_map = conn.execute(
        "SELECT canonical_game_id FROM game_id_map "
        "WHERE source = 'actionnetwork' AND source_game_key = 'evt_collision_an'"
    ).fetchone()
    assert duplicate_map == (duplicate_id,)

    conn.close()
    print("PASS: reschedule collision creates an append-only canonical redirect without rewriting provenance")


def test_unresolvable_team_does_not_create_a_game():
    conn = fresh_conn()
    tr = TeamResolver(conn)
    gr = GameResolver(conn, tr)

    result = gr.resolve_or_create(
        "common", "evt_bad_team", "cfb",
        home_team_raw="Miami",  # ambiguous, deliberately unresolved
        away_team_raw="Ohio State",
        commence_time_raw="2026-09-05T19:00:00Z",
    )

    assert result is None, "a game with an unresolvable team must not be created"
    canonical_count = conn.execute("SELECT COUNT(*) FROM games_canonical").fetchone()[0]
    assert canonical_count == 0

    conn.close()
    print("PASS: unresolvable team blocks game creation rather than guessing")


if __name__ == "__main__":
    test_nfl_chargers_variants_resolve_to_same_team()
    test_nfl_common_abbreviation_variants()
    test_cfb_punctuation_and_abbreviation_variants()
    test_cfb_similar_names_not_merged()
    test_unknown_and_deliberately_ambiguous_aliases_are_unresolved_not_guessed()
    test_same_game_two_sources_one_canonical_two_map_rows()
    test_duplicate_ingestion_same_source_game_is_idempotent()
    test_different_kickoff_same_teams_creates_separate_canonical_game()
    test_home_away_order_mismatch_is_recorded_not_assumed()
    test_reschedule_preserves_canonical_identity_and_logs_change()
    test_reschedule_collision_reconciles_to_existing_linked_canonical()
    test_unresolvable_team_does_not_create_a_game()
    print("\nAll identity resolution tests passed.")
