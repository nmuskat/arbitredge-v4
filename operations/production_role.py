"""
operations/production_role.py

The two-axis production-safety guard (approved architecture):

  Axis A -- PRODUCTION_ROLE (machine-level marker file): is THIS MACHINE
  currently authorized to be the production writer? PRODUCTION | STANDBY.

  Axis B -- ARBITREDGE_ENV (per-invocation environment variable): what is
  THIS SPECIFIC PROCESS RUN doing? PRODUCTION | TEST.

Guard logic, evaluated before any network call or DB connection:
  - ARBITREDGE_ENV missing, or not exactly "PRODUCTION"/"TEST" -> refuse.
  - ARBITREDGE_ENV=PRODUCTION -> requires PRODUCTION_ROLE==PRODUCTION,
    else refuse. DB path is ALWAYS the hardcoded db.migrate.DEFAULT_DB_PATH
    -- never overridable in this branch, by design.
  - ARBITREDGE_ENV=TEST -> PRODUCTION_ROLE is read and logged but is NOT
    a permission gate here (a STANDBY machine legitimately runs TEST
    mode -- that's the entire point of the mechanism). Requires
    ARBITREDGE_DB_PATH to be explicitly set to a path whose CANONICAL
    resolved location differs from DEFAULT_DB_PATH's canonical resolved
    location (Path.resolve(), catching symlink/relative-path aliasing).
    Missing, or aliased to production -> refuse.

PRODUCTION_ROLE marker file semantics:
  - Accepted content (after .strip()): exactly "PRODUCTION" or "STANDBY".
  - Missing file, unreadable file, or any other content whatsoever ->
    treated as STANDBY. Never assumes PRODUCTION under any failure.
  - Role changes are ALWAYS atomic: written to a temp file in the same
    directory, then swapped into place via os.replace() -- a reader can
    only ever observe the complete old value or the complete new value,
    never a partial/corrupt intermediate write.
"""

import logging
import os
import tempfile
from pathlib import Path

from db.migrate import DEFAULT_DB_PATH

logger = logging.getLogger("arbitredge.production_role")

PRODUCTION = "PRODUCTION"
STANDBY = "STANDBY"
TEST = "TEST"

DEFAULT_ROLE_FILE_PATH = Path(__file__).resolve().parent.parent / "PRODUCTION_ROLE"


class ProductionGuardRefusal(Exception):
    """
    Raised when the two-axis guard refuses to authorize a run. This is an
    expected, routine outcome (e.g. a STANDBY machine's production timer
    firing on schedule) -- callers should catch it, log the message, and
    exit cleanly. It is never an unexpected crash.
    """
    pass


def read_production_role(role_file_path=None):
    """Returns PRODUCTION or STANDBY. Fails closed to STANDBY on any
    missing/unreadable/malformed condition."""
    path = Path(role_file_path) if role_file_path is not None else DEFAULT_ROLE_FILE_PATH
    try:
        content = path.read_text().strip()
    except (FileNotFoundError, OSError) as e:
        logger.warning(f"PRODUCTION_ROLE file missing or unreadable at {path} ({e}) -- failing closed to STANDBY")
        return STANDBY

    if content == PRODUCTION:
        return PRODUCTION
    if content == STANDBY:
        return STANDBY

    logger.error(f"PRODUCTION_ROLE file at {path} contains malformed content {content!r} "
                 f"(expected exactly 'PRODUCTION' or 'STANDBY') -- failing closed to STANDBY")
    return STANDBY


def write_production_role(value, role_file_path=None):
    """Atomic role change via temp-file-then-rename. Raises ValueError
    for any value other than exactly PRODUCTION or STANDBY -- never
    silently coerces or guesses."""
    if value not in (PRODUCTION, STANDBY):
        raise ValueError(f"invalid PRODUCTION_ROLE value {value!r}; must be exactly {PRODUCTION!r} or {STANDBY!r}")

    path = Path(role_file_path) if role_file_path is not None else DEFAULT_ROLE_FILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".production_role_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(value)
        os.replace(tmp_path, str(path))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best-effort on platforms/filesystems that don't support chmod semantics identically

    logger.info(f"PRODUCTION_ROLE atomically set to {value!r} at {path}")


def resolve_authorized_db_path(role_file_path=None):
    """
    The full two-axis guard. Returns the authorized database Path on
    success. Raises ProductionGuardRefusal (carrying a specific,
    loggable reason) on any refusal. Must be called by every production
    entrypoint BEFORE any network call or DB connection.
    """
    env = os.environ.get("ARBITREDGE_ENV")
    role = read_production_role(role_file_path)

    if env not in (PRODUCTION, TEST):
        msg = f"REFUSED: ARBITREDGE_ENV is missing or invalid (got {env!r}); must be exactly 'PRODUCTION' or 'TEST'"
        logger.error(msg)
        raise ProductionGuardRefusal(msg)

    if env == PRODUCTION:
        if role != PRODUCTION:
            msg = f"REFUSED: ARBITREDGE_ENV=PRODUCTION requires PRODUCTION_ROLE=PRODUCTION, but role is {role!r}"
            logger.error(msg)
            raise ProductionGuardRefusal(msg)
        logger.info(f"AUTHORIZED: ARBITREDGE_ENV=PRODUCTION, PRODUCTION_ROLE=PRODUCTION -> {DEFAULT_DB_PATH}")
        return Path(DEFAULT_DB_PATH)

    # env == TEST
    test_db_path_raw = os.environ.get("ARBITREDGE_DB_PATH")
    if not test_db_path_raw:
        msg = ("REFUSED: ARBITREDGE_ENV=TEST requires ARBITREDGE_DB_PATH to be explicitly set; "
               "refusing to fall back to the production path")
        logger.error(msg)
        raise ProductionGuardRefusal(msg)

    test_canonical = Path(test_db_path_raw).resolve()
    prod_canonical = Path(DEFAULT_DB_PATH).resolve()
    if test_canonical == prod_canonical:
        msg = (f"REFUSED: ARBITREDGE_DB_PATH ({test_db_path_raw}) resolves to the SAME canonical file as "
               f"the production database ({DEFAULT_DB_PATH}) -- refusing to allow TEST mode to alias "
               f"production, even via symlink or relative path")
        logger.error(msg)
        raise ProductionGuardRefusal(msg)

    logger.info(f"AUTHORIZED: ARBITREDGE_ENV=TEST (machine PRODUCTION_ROLE={role!r}, not a permission gate "
                f"for TEST mode) -> {test_canonical}")
    return test_canonical
