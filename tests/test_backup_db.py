"""
Tests for backup/backup_db.py

Covers:
  - successful backup: file created, correctly named, integrity-checked,
    readable, and the live DB is left untouched
  - missing-source handling: clear failure, no exception, no file created
  - backup integrity: a deliberately corrupted file is correctly detected
  - retention policy: keeps most recent N, deletes older, never touches
    .corrupt files
  - repeated runs are safe: multiple backups in quick succession never
    collide or overwrite each other

Run with: python tests/test_backup_db.py
"""

import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup.backup_db import (  # noqa: E402
    apply_retention,
    backup_database,
    check_integrity,
    list_valid_backups,
)
from db.migrate import run_migration  # noqa: E402

TEST_DIR = Path(__file__).parent / "_backup_test_workspace"


def _clean_workspace():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)


def _seed_live_db(db_path: Path):
    run_migration(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO teams_canonical (canonical_team_id, sport, display_name) "
        "VALUES ('nfl_kc', 'nfl', 'Kansas City Chiefs')"
    )
    conn.commit()
    conn.close()


def test_successful_backup_and_live_db_untouched():
    _clean_workspace()
    db_path = TEST_DIR / "arbitredge.db"
    backup_dir = TEST_DIR / "backups"
    _seed_live_db(db_path)

    live_mtime_before = db_path.stat().st_mtime

    result_path = backup_database(db_path=db_path, backup_dir=backup_dir, retention=30)

    assert result_path is not None, "backup should have succeeded"
    assert result_path.exists()
    assert result_path.parent == backup_dir
    assert result_path.name.startswith("arbitredge_backup_")
    assert result_path.name.endswith(".db")

    # Live DB must be untouched: same mtime, same content, still queryable
    assert db_path.stat().st_mtime == live_mtime_before, "live DB mtime changed -- backup wrote to it"
    live_conn = sqlite3.connect(str(db_path))
    live_row = live_conn.execute(
        "SELECT display_name FROM teams_canonical WHERE canonical_team_id = 'nfl_kc'"
    ).fetchone()
    live_conn.close()
    assert live_row == ("Kansas City Chiefs",), "live DB content changed"

    # Backup must be independently readable and contain the same data
    backup_conn = sqlite3.connect(str(result_path))
    backup_row = backup_conn.execute(
        "SELECT display_name FROM teams_canonical WHERE canonical_team_id = 'nfl_kc'"
    ).fetchone()
    backup_conn.close()
    assert backup_row == ("Kansas City Chiefs",), "backup does not contain expected data"

    assert check_integrity(result_path) is True
    print("PASS: successful backup, correctly named, integrity-checked, live DB untouched")


def test_missing_source_handled_clearly():
    _clean_workspace()
    missing_db_path = TEST_DIR / "does_not_exist.db"
    backup_dir = TEST_DIR / "backups"

    result_path = backup_database(db_path=missing_db_path, backup_dir=backup_dir, retention=30)

    assert result_path is None, "backup of a missing source should return None, not raise"
    # No backup file should have been created
    if backup_dir.exists():
        created_files = list(backup_dir.glob("arbitredge_backup_*.db"))
        assert created_files == [], "no backup file should exist for a missing source"
    # The failure must be logged to the backup log file
    log_path = backup_dir / "backup.log"
    assert log_path.exists(), "backup.log should exist even after a failed run"
    log_contents = log_path.read_text()
    assert "does not exist" in log_contents
    print("PASS: missing source handled with a clear logged error, no exception, no phantom file")


def test_corrupted_backup_detected_by_integrity_check():
    _clean_workspace()
    fake_backup = TEST_DIR / "arbitredge_backup_20260101T000000Z.db"
    fake_backup.write_bytes(b"this is not a valid sqlite database file")

    assert check_integrity(fake_backup) is False
    print("PASS: integrity check correctly rejects a corrupted/invalid database file")


def test_retention_keeps_most_recent_n_and_spares_corrupt_files():
    _clean_workspace()
    backup_dir = TEST_DIR / "backups"
    backup_dir.mkdir(parents=True)

    # Create 5 valid-looking backup filenames (content doesn't matter for
    # retention logic, which only inspects filenames)
    timestamps = [
        "20260101T000000Z",
        "20260102T000000Z",
        "20260103T000000Z",
        "20260104T000000Z",
        "20260105T000000Z",
    ]
    for ts in timestamps:
        (backup_dir / f"arbitredge_backup_{ts}.db").write_bytes(b"placeholder")

    # A .corrupt file must never be touched by retention
    corrupt_file = backup_dir / "arbitredge_backup_20251231T000000Z.db.corrupt"
    corrupt_file.write_bytes(b"corrupt placeholder")

    apply_retention(backup_dir, retention=3)

    remaining = list_valid_backups(backup_dir)
    assert len(remaining) == 3, f"expected 3 remaining valid backups, got {len(remaining)}"
    remaining_names = [p.name for p in remaining]
    assert remaining_names == [
        "arbitredge_backup_20260103T000000Z.db",
        "arbitredge_backup_20260104T000000Z.db",
        "arbitredge_backup_20260105T000000Z.db",
    ], "retention did not keep the most recent 3 backups"
    assert corrupt_file.exists(), "retention must never delete .corrupt files"
    print("PASS: retention keeps the most recent N backups and never touches .corrupt files")


def test_repeated_runs_are_safe_and_never_collide():
    _clean_workspace()
    db_path = TEST_DIR / "arbitredge.db"
    backup_dir = TEST_DIR / "backups"
    _seed_live_db(db_path)

    result1 = backup_database(db_path=db_path, backup_dir=backup_dir, retention=30)
    result2 = backup_database(db_path=db_path, backup_dir=backup_dir, retention=30)
    result3 = backup_database(db_path=db_path, backup_dir=backup_dir, retention=30)

    assert result1 is not None and result2 is not None and result3 is not None
    paths = {result1, result2, result3}
    assert len(paths) == 3, "each run must produce a distinct file, even if run in the same second"
    for p in paths:
        assert check_integrity(p) is True

    valid_backups = list_valid_backups(backup_dir)
    assert len(valid_backups) == 3
    print("PASS: repeated backup runs never collide or overwrite each other")


if __name__ == "__main__":
    test_successful_backup_and_live_db_untouched()
    test_missing_source_handled_clearly()
    test_corrupted_backup_detected_by_integrity_check()
    test_retention_keeps_most_recent_n_and_spares_corrupt_files()
    test_repeated_runs_are_safe_and_never_collide()
    print("\nAll backup tests passed.")
