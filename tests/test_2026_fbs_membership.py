"""
Tests for reference/build_2026_fbs_membership.py

Run with: python tests/test_2026_fbs_membership.py
"""

import csv
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reference.build_2026_fbs_membership as builder  # noqa: E402
from collectors.run_results_backfill_dryrun import load_fbs_allowlist  # noqa: E402
import collectors.run_results_backfill_dryrun as fbs_module  # noqa: E402
from features.build_engineered_features_v2_batch import check_2026_fbs_membership_available  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402

TEST_DIR = Path(__file__).parent / "_fbs_2026_test_workspace"


def fresh_conn():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    db_path = TEST_DIR / "arbitredge.db"
    seed(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def test_all_138_teams_resolve():
    conn = fresh_conn()
    result = builder.build_and_verify(conn)
    conn.close()
    assert result["total_compiled"] == 138
    assert len(result["unmatched"]) == 0, f"unmatched: {result['unmatched']}"
    print("PASS: all 138 real 2026 FBS teams resolve to a real canonical_team_id, no guessing")


def test_no_duplicate_canonical_ids():
    conn = fresh_conn()
    result = builder.build_and_verify(conn)
    conn.close()
    assert len(result["duplicates"]) == 0
    assert result["unique_ids"] == 138
    print("PASS: no duplicate canonical_team_id assignments among the 138 resolved teams")


def test_fcs_to_fbs_newcomers_correctly_flagged():
    conn = fresh_conn()
    result = builder.build_and_verify(conn)
    conn.close()
    newcomers = {m[1] for m in result["matched"] if m[3]}
    assert newcomers == {"North Dakota State", "Sacramento State"}
    print("PASS: exactly the two real, cross-referenced FCS-to-FBS newcomers are flagged as such")


def test_disambiguation_resolves_correctly():
    conn = fresh_conn()
    result = builder.build_and_verify(conn)
    conn.close()
    lookup = {m[1]: m[2] for m in result["matched"]}
    assert lookup["Miami (FL)"] == "cfb_miami_fl"
    assert lookup["Miami (OH)"] == "cfb_miami_oh"
    assert lookup["North Carolina"] == "cfb_north_carolina"
    assert lookup["Southern Miss"] == "cfb_southern_mississippi"
    assert lookup["ULM"] == "cfb_ul_monroe"
    print("PASS: known name-collision cases (Miami FL/OH, North Carolina vs A&T, "
          "Southern Miss, ULM) all resolve to the correct real canonical_team_id")


def test_write_never_touches_existing_rows():
    conn = fresh_conn()

    tmp_csv = Path(tempfile.mktemp(suffix=".csv"))
    tmp_csv.write_text(
        "season,canonical_team_id,classification,source,source_note\n"
        "2025,cfb_alabama,FBS,test,pre-existing row that must survive untouched\n"
    )
    original_path = builder.FBS_MEMBERSHIP_PATH
    builder.FBS_MEMBERSHIP_PATH = tmp_csv
    try:
        result = builder.build_and_verify(conn)
        wrote = builder.write_2026_rows(result["matched"])
        assert wrote is True

        with open(tmp_csv, newline="") as f:
            rows = list(csv.DictReader(f))
        rows_2025 = [r for r in rows if r["season"] == "2025"]
        rows_2026 = [r for r in rows if r["season"] == "2026"]

        assert len(rows_2025) == 1
        assert rows_2025[0]["canonical_team_id"] == "cfb_alabama"
        assert rows_2025[0]["source_note"] == "pre-existing row that must survive untouched"
        assert len(rows_2026) == 138
    finally:
        builder.FBS_MEMBERSHIP_PATH = original_path
        tmp_csv.unlink(missing_ok=True)
    conn.close()
    print("PASS: writing 2026 rows never modifies a pre-existing 2025 row")


def test_refuses_to_write_if_2026_already_exists():
    conn = fresh_conn()
    tmp_csv = Path(tempfile.mktemp(suffix=".csv"))
    tmp_csv.write_text(
        "season,canonical_team_id,classification,source,source_note\n"
        "2026,cfb_alabama,FBS,test,already here\n"
    )
    original_path = builder.FBS_MEMBERSHIP_PATH
    builder.FBS_MEMBERSHIP_PATH = tmp_csv
    try:
        result = builder.build_and_verify(conn)
        wrote = builder.write_2026_rows(result["matched"])
        assert wrote is False

        with open(tmp_csv, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
    finally:
        builder.FBS_MEMBERSHIP_PATH = original_path
        tmp_csv.unlink(missing_ok=True)
    conn.close()
    print("PASS: the script refuses to write (and duplicate) if 2026 rows already exist -- "
          "safe against accidental reruns")


def test_real_2025_fallback_still_returns_2025_data_unchanged():
    a2025 = load_fbs_allowlist(2025)
    assert len(a2025) == 136
    print(f"PASS: load_fbs_allowlist(2025) still returns the original {len(a2025)} teams, unaffected")


def test_2026_gate_now_opens_with_real_data():
    a2026 = load_fbs_allowlist(2026)
    assert len(a2026) == 138
    assert check_2026_fbs_membership_available() is True
    print(f"PASS: the production CFB hard gate (check_2026_fbs_membership_available) now correctly "
          f"opens with {len(a2026)} real, verified 2026 teams")


def test_2026_gate_still_closes_with_no_data():
    tmp_csv = Path(tempfile.mktemp(suffix=".csv"))
    tmp_csv.write_text(
        "season,canonical_team_id,classification,source,source_note\n"
        "2025,cfb_alabama,FBS,test,test\n"
    )
    original_path = fbs_module.FBS_MEMBERSHIP_PATH
    fbs_module.FBS_MEMBERSHIP_PATH = tmp_csv
    try:
        assert check_2026_fbs_membership_available() is False
    finally:
        fbs_module.FBS_MEMBERSHIP_PATH = original_path
        tmp_csv.unlink(missing_ok=True)
    print("PASS: with only 2025 data present, the hard gate still correctly refuses -- "
          "no silent fallback, confirmed unaffected by this milestone")


if __name__ == "__main__":
    test_all_138_teams_resolve()
    test_no_duplicate_canonical_ids()
    test_fcs_to_fbs_newcomers_correctly_flagged()
    test_disambiguation_resolves_correctly()
    test_write_never_touches_existing_rows()
    test_refuses_to_write_if_2026_already_exists()
    test_real_2025_fallback_still_returns_2025_data_unchanged()
    test_2026_gate_now_opens_with_real_data()
    test_2026_gate_still_closes_with_no_data()
    print("\nAll 2026 FBS membership tests passed.")
