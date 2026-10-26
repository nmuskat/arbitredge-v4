"""
operations/odds_circuit_breaker.py

Suppresses repeated Odds API requests after sustained authentication /
quota failures, without hiding the outage.

STATE: derived entirely from the existing append-only collection_log rows of
odds_collector (no new table, no schema change). A failure is
"authentication-class" if its error_detail carries
`classification=QUOTA_EXHAUSTED|AUTH_INVALID_KEY|AUTH_DEACTIVATED_KEY|AUTH_UNKNOWN`
(written by collectors/provider_errors.summarize) or, for rows written by
the pre-patch code, matches "Non-retryable HTTP 401|403".

POLICY (all thresholds are constants below):
  * CLOSED   fewer than OPEN_AFTER consecutive auth-class failures since the
             last success (or the last operator reset) -> poll normally.
  * OPEN     >= OPEN_AFTER consecutive auth-class failures:
               - QUOTA_EXHAUSTED: The Odds API documents that usage credits
                 reset on the 1st of each month, so suppress until 00:00 UTC
                 on the next 1st; then allow probes.
               - any other auth class: allow ONE probe request (one sport)
                 per PROBE_INTERVAL_HOURS across all sports.
  * A single success closes the breaker (normal cadence resumes).
  * OPERATOR RESET: touching RESET_MARKER (an operator action, see the
    recovery checklist) closes the breaker for failures older than the
    marker's mtime -- e.g. after fixing the key or buying credits.

OBSERVABILITY: every suppressed check is printed/logged with the reason,
the failure count, the classification, and when the next attempt is
allowed; `python3 operations/odds_circuit_breaker.py --status` is read-only.
"""

import argparse
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

OPEN_AFTER = 3
PROBE_INTERVAL_HOURS = 6
LOOKBACK_ROWS = 200
RESET_MARKER = Path(__file__).resolve().parent / "state" / "odds_breaker_reset"

AUTH_CLASSES = ("QUOTA_EXHAUSTED", "AUTH_INVALID_KEY", "AUTH_DEACTIVATED_KEY", "AUTH_UNKNOWN")
_CLASS_RE = re.compile(r"classification=([A-Z_]+)")
_LEGACY_AUTH_RE = re.compile(r"Non-retryable HTTP (401|403)\b")


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def classify_log_row(success, error_type, error_detail):
    """'success' | 'skipped' | an auth class | 'other_failure'."""
    if error_type == "SkippedTooSoon":
        return "skipped"
    if success:
        return "success"
    detail = error_detail or ""
    m = _CLASS_RE.search(detail)
    if m and m.group(1) in AUTH_CLASSES:
        return m.group(1)
    if not m and _LEGACY_AUTH_RE.search(detail):
        return "AUTH_UNKNOWN"
    return "other_failure"


def next_month_start(now):
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


@dataclass(frozen=True)
class BreakerDecision:
    allow: bool
    state: str                   # CLOSED | OPEN | HALF_OPEN_PROBE
    reason: str
    consecutive_auth_failures: int
    last_classification: str | None
    last_failure_at: str | None
    next_attempt_at: str | None


def evaluate(rows, now, reset_at=None):
    """
    rows: odds_collector collection_log rows NEWEST FIRST as
          (executed_at, success, error_type, error_detail).
    Pure function -- the unit under test.
    """
    count, last_class, last_fail = 0, None, None
    for executed_at, success, error_type, error_detail in rows:
        t = _parse(executed_at)
        if reset_at is not None and t <= reset_at:
            break
        kind = classify_log_row(success, error_type, error_detail)
        if kind == "skipped":
            continue
        if kind in AUTH_CLASSES:
            count += 1
            if last_class is None:
                last_class, last_fail = kind, t
            continue
        break  # success or a non-auth failure ends the streak

    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None  # noqa: E731
    if count < OPEN_AFTER:
        return BreakerDecision(True, "CLOSED", "below threshold", count, last_class, iso(last_fail), None)

    if last_class == "QUOTA_EXHAUSTED":
        reset = next_month_start(last_fail)
        if now < reset:
            return BreakerDecision(False, "OPEN", "provider reported usage credits exhausted; "
                                   "documented monthly reset not yet reached", count, last_class,
                                   iso(last_fail), iso(reset))
        # After the documented reset, fall through to probe pacing.

    next_probe = last_fail + timedelta(hours=PROBE_INTERVAL_HOURS)
    if last_class == "QUOTA_EXHAUSTED":
        next_probe = max(next_probe, next_month_start(last_fail))
    if now >= next_probe:
        return BreakerDecision(True, "HALF_OPEN_PROBE", "probe allowed (one request)", count, last_class,
                               iso(last_fail), None)
    return BreakerDecision(False, "OPEN", f"{count} consecutive authentication-class failures; "
                           "next probe not yet due", count, last_class, iso(last_fail), iso(next_probe))


def load_rows(conn, limit=LOOKBACK_ROWS):
    return conn.execute(
        "SELECT executed_at, success, error_type, error_detail FROM collection_log "
        "WHERE collector_name = 'odds_collector' ORDER BY executed_at DESC LIMIT ?", (limit,)
    ).fetchall()


def reset_marker_time(marker=RESET_MARKER):
    try:
        return datetime.fromtimestamp(os.path.getmtime(marker), tz=timezone.utc)
    except OSError:
        return None


def decide(conn, now=None, marker=RESET_MARKER):
    now = now or datetime.now(timezone.utc)
    return evaluate(load_rows(conn), now, reset_marker_time(marker))


def describe(d):
    return (f"odds breaker {d.state}: {d.reason} (consecutive_auth_failures={d.consecutive_auth_failures}, "
            f"last={d.last_classification} at {d.last_failure_at}, next_attempt_at={d.next_attempt_at})")


def main():
    ap = argparse.ArgumentParser(description="Read-only odds circuit-breaker status")
    ap.add_argument("--db", required=True, help="path to the SQLite DB (opened read-only)")
    args = ap.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(describe(decide(conn)))
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
