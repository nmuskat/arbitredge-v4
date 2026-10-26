"""
validation/harness/holdout.py

Frozen holdout mechanics. access_holdout_for_confirmation is the actual
enforcement point: every access attempt is logged (successful or refused),
but only the FIRST-EVER access for a given holdout_key returns the
holdout's game list -- every subsequent attempt raises
HoldoutAlreadyAccessedError rather than silently returning data. This
makes "second-access refusal" a real, testable guarantee, not an
advisory flag a caller could ignore.

mechanical_diagnostic_holdout_2025 (created by the 2025 diagnostic run
script) and any future real behavioral holdout are just different
holdout_key values in the same tables -- touching one never touches the
other, by construction (every function here is scoped by holdout_key).
"""

from datetime import datetime, timezone


class HoldoutAlreadyAccessedError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def create_holdout(conn, holdout_key: str, canonical_game_ids: list) -> str:
    """Idempotent: if this holdout_key already has locked membership, does
    nothing and returns 'already_exists' rather than re-locking (which
    would risk silently changing what a previously-run experiment's
    holdout confirmation was actually tested against)."""
    existing = conn.execute(
        "SELECT COUNT(*) FROM validation_holdout_membership WHERE holdout_key = ?", (holdout_key,)
    ).fetchone()[0]
    if existing > 0:
        return "already_exists"

    now = _now()
    for gid in canonical_game_ids:
        conn.execute(
            "INSERT INTO validation_holdout_membership (holdout_key, canonical_game_id, locked_at) VALUES (?, ?, ?)",
            (holdout_key, gid, now),
        )
    conn.commit()
    return "created"


def get_holdout_game_ids(conn, holdout_key: str) -> list:
    rows = conn.execute(
        "SELECT canonical_game_id FROM validation_holdout_membership WHERE holdout_key = ?", (holdout_key,)
    ).fetchall()
    return [r[0] for r in rows]


def filter_out_holdout_games(conn, holdout_key: str, game_ids: list) -> list:
    """Removes any holdout members from a candidate train/validation game
    list -- applied when building the regular rolling walk-forward cohort,
    so the holdout is structurally excluded from every ordinary fold."""
    holdout_ids = set(get_holdout_game_ids(conn, holdout_key))
    return [gid for gid in game_ids if gid not in holdout_ids]


def access_holdout_for_confirmation(conn, holdout_key: str, experiment_id=None) -> list:
    """The only sanctioned way to read a holdout's game list. Logs the
    attempt FIRST (so a refused attempt is still on the record), then
    either returns the game list (first access) or raises
    HoldoutAlreadyAccessedError (any subsequent access) -- no data is
    returned on a refused attempt."""
    prior_count = conn.execute(
        "SELECT COUNT(*) FROM validation_holdout_access_log WHERE holdout_key = ?", (holdout_key,)
    ).fetchone()[0]

    conn.execute(
        "INSERT INTO validation_holdout_access_log (holdout_key, accessed_at, experiment_id, purpose) "
        "VALUES (?, ?, ?, ?)",
        (holdout_key, _now(), experiment_id, "holdout_confirmation"),
    )
    conn.commit()

    if prior_count > 0:
        raise HoldoutAlreadyAccessedError(
            f"Holdout {holdout_key!r} has already been accessed {prior_count} time(s). "
            f"This attempt has been logged but REFUSED -- no holdout data was returned."
        )

    return get_holdout_game_ids(conn, holdout_key)
