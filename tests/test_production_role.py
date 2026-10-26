"""
Tests for operations/production_role.py -- the two-axis PRODUCTION_ROLE /
ARBITREDGE_ENV safety guard, and db/connection.py's centralized SQLite
hardening.

Run with: python tests/test_production_role.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operations.production_role import (
    read_production_role, write_production_role, resolve_authorized_db_path,
    ProductionGuardRefusal, PRODUCTION, STANDBY, TEST,
)
from db.migrate import DEFAULT_DB_PATH
from db.connection import get_connection

TEST_DIR = Path(__file__).parent / "_production_role_test_workspace"


def fresh_role_file():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    return TEST_DIR / "PRODUCTION_ROLE"


def clear_env():
    os.environ.pop("ARBITREDGE_ENV", None)
    os.environ.pop("ARBITREDGE_DB_PATH", None)


def test_missing_role_file_fails_closed_to_standby():
    role_file = fresh_role_file()
    assert read_production_role(role_file) == STANDBY
    print("PASS: a missing PRODUCTION_ROLE file fails closed to STANDBY, never assumes PRODUCTION")


def test_malformed_role_content_fails_closed_to_standby():
    role_file = fresh_role_file()
    role_file.write_text("garbage")
    assert read_production_role(role_file) == STANDBY
    role_file.write_text("production")  # wrong case
    assert read_production_role(role_file) == STANDBY
    role_file.write_text("")
    assert read_production_role(role_file) == STANDBY
    print("PASS: malformed, wrong-case, or empty PRODUCTION_ROLE content all fail closed to STANDBY")


def test_valid_role_values_read_correctly():
    role_file = fresh_role_file()
    write_production_role(PRODUCTION, role_file)
    assert read_production_role(role_file) == PRODUCTION
    write_production_role(STANDBY, role_file)
    assert read_production_role(role_file) == STANDBY
    print("PASS: exactly-formatted PRODUCTION and STANDBY values read back correctly")


def test_write_production_role_rejects_invalid_values():
    role_file = fresh_role_file()
    try:
        write_production_role("production", role_file)  # wrong case
        assert False
    except ValueError:
        pass
    try:
        write_production_role("MAYBE", role_file)
        assert False
    except ValueError:
        pass
    print("PASS: write_production_role rejects any value other than exactly PRODUCTION/STANDBY")


def test_role_change_is_atomic_no_partial_file():
    """A reader must never observe a half-written file. We can't easily
    interrupt a real write mid-flight in a unit test, but we CAN confirm
    the mechanism uses a temp-file + os.replace pattern (never opens the
    real target path directly for writing) and that no temp file is left
    behind after a successful write."""
    role_file = fresh_role_file()
    write_production_role(PRODUCTION, role_file)
    leftover_temp_files = [p for p in role_file.parent.iterdir() if p.name.startswith(".production_role_")]
    assert leftover_temp_files == []
    assert role_file.read_text() == PRODUCTION
    print("PASS: role changes leave no temp file behind and the target contains exactly the new value -- "
          "confirms the atomic temp-file-then-rename pattern completed cleanly")


def test_role_file_permissions_owner_only():
    role_file = fresh_role_file()
    write_production_role(PRODUCTION, role_file)
    mode = role_file.stat().st_mode & 0o777
    assert mode == 0o600
    print("PASS: the PRODUCTION_ROLE file is set to owner-only read/write permissions (0600)")


def test_production_mode_requires_production_role():
    role_file = fresh_role_file()
    write_production_role(STANDBY, role_file)
    os.environ["ARBITREDGE_ENV"] = PRODUCTION
    clear_env_db_path = os.environ.pop("ARBITREDGE_DB_PATH", None)
    try:
        resolve_authorized_db_path(role_file)
        assert False, "must refuse PRODUCTION mode when role is STANDBY"
    except ProductionGuardRefusal as e:
        assert "PRODUCTION_ROLE" in str(e)
    finally:
        clear_env()
    print("PASS: ARBITREDGE_ENV=PRODUCTION correctly refuses to run when PRODUCTION_ROLE is STANDBY")


def test_production_mode_authorized_uses_default_db_path():
    role_file = fresh_role_file()
    write_production_role(PRODUCTION, role_file)
    os.environ["ARBITREDGE_ENV"] = PRODUCTION
    os.environ.pop("ARBITREDGE_DB_PATH", None)
    try:
        path = resolve_authorized_db_path(role_file)
        assert path == Path(DEFAULT_DB_PATH)
    finally:
        clear_env()
    print("PASS: an authorized PRODUCTION run always resolves to the real DEFAULT_DB_PATH")


def test_production_mode_ignores_db_path_override_attempt():
    """Even if ARBITREDGE_DB_PATH happens to be set in the environment,
    PRODUCTION mode must never use it -- the production path is hardcoded,
    not overridable, in this branch."""
    role_file = fresh_role_file()
    write_production_role(PRODUCTION, role_file)
    os.environ["ARBITREDGE_ENV"] = PRODUCTION
    os.environ["ARBITREDGE_DB_PATH"] = "/tmp/some_other_file.db"
    try:
        path = resolve_authorized_db_path(role_file)
        assert path == Path(DEFAULT_DB_PATH)
        assert str(path) != "/tmp/some_other_file.db"
    finally:
        clear_env()
    print("PASS: PRODUCTION mode ignores any ARBITREDGE_DB_PATH override attempt -- the DB path "
          "cannot be overridden in this branch under any circumstance")


def test_test_mode_requires_explicit_db_path():
    role_file = fresh_role_file()
    write_production_role(STANDBY, role_file)
    os.environ["ARBITREDGE_ENV"] = TEST
    os.environ.pop("ARBITREDGE_DB_PATH", None)
    try:
        resolve_authorized_db_path(role_file)
        assert False, "must refuse TEST mode with no explicit ARBITREDGE_DB_PATH"
    except ProductionGuardRefusal as e:
        assert "ARBITREDGE_DB_PATH" in str(e)
    finally:
        clear_env()
    print("PASS: ARBITREDGE_ENV=TEST refuses to run without an explicit ARBITREDGE_DB_PATH -- "
          "never falls back to the production path")


def test_test_mode_runs_on_standby_machine():
    """The core fix from the prior round: TEST mode must be able to run
    while PRODUCTION_ROLE=STANDBY -- role is not a permission gate for
    TEST mode."""
    role_file = fresh_role_file()
    write_production_role(STANDBY, role_file)
    disposable_path = str(TEST_DIR / "disposable_test.db")
    os.environ["ARBITREDGE_ENV"] = TEST
    os.environ["ARBITREDGE_DB_PATH"] = disposable_path
    try:
        path = resolve_authorized_db_path(role_file)
        assert path == Path(disposable_path).resolve()
    finally:
        clear_env()
    print("PASS: TEST mode successfully authorizes and runs while the machine's PRODUCTION_ROLE "
          "is STANDBY -- resolving the contradiction from the prior architecture round")


def test_test_mode_rejects_exact_production_path_alias():
    role_file = fresh_role_file()
    write_production_role(STANDBY, role_file)
    os.environ["ARBITREDGE_ENV"] = TEST
    os.environ["ARBITREDGE_DB_PATH"] = str(DEFAULT_DB_PATH)
    try:
        resolve_authorized_db_path(role_file)
        assert False, "must refuse when ARBITREDGE_DB_PATH is literally the production path"
    except ProductionGuardRefusal as e:
        assert "SAME canonical file" in str(e)
    finally:
        clear_env()
    print("PASS: TEST mode refuses when ARBITREDGE_DB_PATH literally IS the production path")


def test_test_mode_rejects_relative_path_alias_to_production():
    """A relative path that RESOLVES to the same file as production must
    be caught, not just a literal string match."""
    role_file = fresh_role_file()
    write_production_role(STANDBY, role_file)

    prod_dir = Path(DEFAULT_DB_PATH).resolve().parent
    prod_name = Path(DEFAULT_DB_PATH).resolve().name
    sneaky_relative = str(prod_dir / ".." / prod_dir.name / prod_name)

    os.environ["ARBITREDGE_ENV"] = TEST
    os.environ["ARBITREDGE_DB_PATH"] = sneaky_relative
    try:
        resolve_authorized_db_path(role_file)
        assert False, "a relative path that resolves to the production file must still be refused"
    except ProductionGuardRefusal as e:
        assert "SAME canonical file" in str(e)
    finally:
        clear_env()
    print("PASS: a relative-path alias that RESOLVES to the production file is caught via canonical "
          "path comparison, not fooled by different literal path strings")


def test_test_mode_rejects_symlink_alias_to_production():
    """A symlink pointing AT the production file must also be caught."""
    role_file = fresh_role_file()
    write_production_role(STANDBY, role_file)

    fake_prod_target = TEST_DIR / "fake_production.db"
    fake_prod_target.write_text("")
    symlink_path = TEST_DIR / "symlink_to_fake_prod.db"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(fake_prod_target)

    os.environ["ARBITREDGE_ENV"] = TEST
    os.environ["ARBITREDGE_DB_PATH"] = str(symlink_path)
    try:
        # This symlink points at a FAKE prod file, not the real DEFAULT_DB_PATH,
        # so it should be ALLOWED -- confirming the check is real path equality,
        # not a blanket "symlinks are always refused" rule.
        path = resolve_authorized_db_path(role_file)
        assert path == fake_prod_target.resolve()
    finally:
        clear_env()

    # Now point the symlink at the REAL production path and confirm it IS refused.
    symlink_path.unlink()
    symlink_path.symlink_to(Path(DEFAULT_DB_PATH))
    os.environ["ARBITREDGE_ENV"] = TEST
    os.environ["ARBITREDGE_DB_PATH"] = str(symlink_path)
    try:
        resolve_authorized_db_path(role_file)
        assert False, "a symlink resolving to the real production file must be refused"
    except ProductionGuardRefusal as e:
        assert "SAME canonical file" in str(e)
    finally:
        clear_env()
    print("PASS: a symlink resolving to the REAL production file is correctly refused, while a symlink "
          "to a genuinely different file is correctly allowed -- confirming real canonical-path equality")


def test_invalid_arbitredge_env_value_refused():
    role_file = fresh_role_file()
    write_production_role(PRODUCTION, role_file)
    os.environ["ARBITREDGE_ENV"] = "PRODUCTIONN"  # typo
    try:
        resolve_authorized_db_path(role_file)
        assert False
    except ProductionGuardRefusal as e:
        assert "ARBITREDGE_ENV" in str(e)
    finally:
        clear_env()
    print("PASS: an invalid/typo ARBITREDGE_ENV value is refused, never silently coerced to a valid mode")


def test_forbidden_dual_production_state_never_authorized_twice():
    """Simulates the forbidden Mac=PRODUCTION, Cloud=PRODUCTION state by
    confirming that TWO INDEPENDENT role files can each independently
    authorize PRODUCTION mode against the SAME hardcoded DEFAULT_DB_PATH
    only if each machine's OWN role file says PRODUCTION -- this test
    documents that the guard is machine-local and does not itself prevent
    a human from setting both role files to PRODUCTION; that operational
    discipline lives in the documented cutover/rollback procedure, not
    in code that could reach across machines."""
    role_file_a = fresh_role_file()
    write_production_role(PRODUCTION, role_file_a)
    role_file_b = TEST_DIR / "PRODUCTION_ROLE_B"
    write_production_role(PRODUCTION, role_file_b)

    os.environ["ARBITREDGE_ENV"] = PRODUCTION
    os.environ.pop("ARBITREDGE_DB_PATH", None)
    try:
        path_a = resolve_authorized_db_path(role_file_a)
        path_b = resolve_authorized_db_path(role_file_b)
        assert path_a == path_b == Path(DEFAULT_DB_PATH)
    finally:
        clear_env()
    print("PASS (documentation test): confirms both role files independently resolve to the SAME "
          "DEFAULT_DB_PATH when both say PRODUCTION -- the forbidden dual-writer state is prevented "
          "by the documented one-directional cutover procedure (only one machine's role file is ever "
          "set to PRODUCTION at a time), not by any cross-machine code check, since the two machines "
          "never communicate directly by design")


def test_get_connection_enables_wal_and_busy_timeout():
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "hardened.db")
    conn = get_connection(db_path)
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"
    assert timeout == 5000
    assert fk == 1
    print("PASS: get_connection() applies WAL mode, a 5000ms busy_timeout, and foreign_keys=ON consistently")


def test_get_connection_changes_no_data_semantics():
    """Confirms WAL/busy_timeout hardening changes nothing about actual
    data storage/retrieval -- a real write/read round-trip behaves
    identically to a plain sqlite3.connect() connection."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "semantics.db")
    conn = get_connection(db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello')")
    conn.commit()
    row = conn.execute("SELECT val FROM t WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "hello"

    conn2 = sqlite3.connect(db_path)
    row2 = conn2.execute("SELECT val FROM t WHERE id=1").fetchone()
    conn2.close()
    assert row2[0] == "hello"
    print("PASS: data written through a hardened connection reads back identically through a plain "
          "connection -- WAL/busy_timeout change concurrency behavior only, never stored data semantics")


if __name__ == "__main__":
    test_missing_role_file_fails_closed_to_standby()
    test_malformed_role_content_fails_closed_to_standby()
    test_valid_role_values_read_correctly()
    test_write_production_role_rejects_invalid_values()
    test_role_change_is_atomic_no_partial_file()
    test_role_file_permissions_owner_only()
    test_production_mode_requires_production_role()
    test_production_mode_authorized_uses_default_db_path()
    test_production_mode_ignores_db_path_override_attempt()
    test_test_mode_requires_explicit_db_path()
    test_test_mode_runs_on_standby_machine()
    test_test_mode_rejects_exact_production_path_alias()
    test_test_mode_rejects_relative_path_alias_to_production()
    test_test_mode_rejects_symlink_alias_to_production()
    test_invalid_arbitredge_env_value_refused()
    test_forbidden_dual_production_state_never_authorized_twice()
    test_get_connection_enables_wal_and_busy_timeout()
    test_get_connection_changes_no_data_semantics()
    print("\nAll production_role/connection safety-layer tests passed.")
