import json
"""
operations/run_collection.py

The single orchestrator. Decides internally what's actually due and runs
it. Does NOT modify any existing collector -- calls each one exactly as
an external script would, with min_legitimate_poll_interval_minutes=0
(the orchestrator's own due-check is now the authoritative timing
decision, same pattern already used by the backfill/dry-run scripts).

Every odds due-check is logged to cadence_decisions (append-only),
whether or not a poll actually fires -- this is what lets
collection_health.py later distinguish a real missed market observation
from a gap that's well within the tier's own interval.

Usage:
    python3 operations/run_collection.py            # normal run: do whatever's due
    python3 operations/run_collection.py --status    # read-only health report, ZERO network calls
    python3 operations/run_collection.py --force      # bypass due-checks, poll everything now
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from book_need.experimental_mixed import evaluate_experimental_mixed_signal, SIGNAL_VERSION  # noqa: E402

from db.migrate import DEFAULT_DB_PATH, run_migration  # noqa: E402
from db.connection import get_connection  # noqa: E402
from operations.production_role import resolve_authorized_db_path, ProductionGuardRefusal  # noqa: E402
from operations import odds_circuit_breaker  # noqa: E402
from identity.seed_team_data import seed  # noqa: E402
from identity.team_resolver import TeamResolver  # noqa: E402
from identity.game_resolver import GameResolver  # noqa: E402
from collectors.odds_collector import OddsCollector  # noqa: E402
from collectors.context_collector import ContextCollector  # noqa: E402
from collectors.results_collector import ResultsCollector  # noqa: E402
from backup.backup_db import backup_database, DEFAULT_BACKUP_DIR, DEFAULT_RETENTION, list_valid_backups  # noqa: E402
from operations.cadence_config import (  # noqa: E402
    tier_for_hours, CONTEXT_POLL_INTERVAL_MINUTES_NORMAL, CONTEXT_POLL_INTERVAL_MINUTES_GAMEDAY,
    RESULTS_POLL_INTERVAL_MINUTES, BACKUP_INTERVAL_MINUTES,
)
import sqlite3  # noqa: E402

SPORTS = ["nfl", "cfb"]  # context/results: preseason games appear here naturally via ESPN's
                          # current-day scoreboard, no separate identifier needed (see module docstring)
ODDS_SPORTS = ["nfl", "cfb", "nfl_preseason"]  # odds specifically needs the separate the-odds-api
                                                 # sport key for preseason -- see odds_collector.py


def _now():
    return datetime.now(timezone.utc)


def _now_str():
    return _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def get_nearest_future_kickoff(conn, sport):
    """
    For 'nfl'/'cfb': queries games_canonical directly, as before.
    For 'nfl_preseason': games_canonical deliberately does NOT tag preseason
    games separately (team/game identity is shared with 'nfl' -- see
    odds_collector.py's IDENTITY_SPORT_MAP), so the nearest tracked
    preseason kickoff is instead derived from market_snapshots, which DOES
    preserve the 'nfl_preseason' tag on every raw observation. Before any
    preseason game has ever been polled, this correctly returns (None, None)
    -- the same "nothing tracked yet" signal used everywhere else, which
    tier_for_hours() already treats as the discovery tier, so the very
    first check still polls and discovers the slate.
    """
    if sport == "nfl_preseason":
        row = conn.execute(
            "SELECT canonical_game_id, commence_time FROM market_snapshots "
            "WHERE sport = 'nfl_preseason' AND commence_time > ? "
            "ORDER BY commence_time ASC LIMIT 1",
            (_now_str(),),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    row = conn.execute(
        "SELECT canonical_game_id, commence_time FROM games_canonical "
        "WHERE sport = ? AND commence_time > ? ORDER BY commence_time ASC LIMIT 1",
        (sport, _now_str()),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def get_last_successful_run(conn, collector_name):
    row = conn.execute(
        "SELECT executed_at FROM collection_log WHERE collector_name = ? AND success = 1 "
        "ORDER BY executed_at DESC LIMIT 1",
        (collector_name,),
    ).fetchone()
    return _parse_iso(row[0]) if row else None


def get_last_run_success_flag(conn, collector_name):
    row = conn.execute(
        "SELECT success FROM collection_log WHERE collector_name = ? "
        "ORDER BY executed_at DESC LIMIT 1",
        (collector_name,),
    ).fetchone()
    return bool(row[0]) if row else None


def log_cadence_decision(conn, sport, nearest_game_id, hours_to_nearest, tier_label,
                          required_interval, minutes_since_last, triggered, result):
    conn.execute(
        "INSERT INTO cadence_decisions (checked_at, sport, nearest_kickoff_game_id, "
        "hours_to_nearest_kickoff, tier_label, required_interval_minutes, "
        "minutes_since_last_poll, poll_triggered, collector_run_result) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (_now_str(), sport, nearest_game_id, hours_to_nearest, tier_label, required_interval,
         minutes_since_last, 1 if triggered else 0, result),
    )
    conn.commit()


def get_last_successful_odds_poll_for_sport(conn, sport):
    """
    Sport-scoped, unlike collection_log (which has no sport column and is
    shared across every collector call). Uses cadence_decisions, which IS
    sport-scoped, specifically to avoid the bug this replaces: querying
    collection_log's most recent successful odds_collector run without a
    sport filter meant that polling NFL successfully would make a CFB (or
    nfl_preseason) due-check immediately afterward, in the same
    invocation, wrongly see a near-zero "minutes since last poll" and
    conclude it had just been polled too -- silently suppressing whichever
    sport happened to be checked later in the loop.
    """
    row = conn.execute(
        "SELECT checked_at FROM cadence_decisions WHERE sport = ? "
        "AND poll_triggered = 1 AND collector_run_result = 'success' "
        "ORDER BY checked_at DESC LIMIT 1",
        (sport,),
    ).fetchone()
    return _parse_iso(row[0]) if row else None


def check_and_run_odds(conn, team_resolver, game_resolver, sport, force=False):
    game_id, kickoff = get_nearest_future_kickoff(conn, sport)
    hours_to_nearest = (_parse_iso(kickoff) - _now()).total_seconds() / 3600 if kickoff else None
    tier_label, required_interval = tier_for_hours(hours_to_nearest)

    last_run = get_last_successful_odds_poll_for_sport(conn, sport)
    minutes_since_last = (_now() - last_run).total_seconds() / 60 if last_run else None

    due = force or minutes_since_last is None or minutes_since_last >= required_interval

    if not due:
        log_cadence_decision(conn, sport, game_id, hours_to_nearest, tier_label,
                              required_interval, minutes_since_last, False, None)
        print(f"  [odds/{sport}] not due -- tier={tier_label}, needs {required_interval}min, "
              f"only {minutes_since_last:.0f}min elapsed")
        return

    # Circuit breaker: after sustained authentication/quota failures, stop
    # sending requests until a probe is due, the documented monthly credit
    # reset passes, or an operator touches the reset marker. --force does
    # NOT bypass this (force overrides cadence, not account-level failure).
    breaker = odds_circuit_breaker.decide(conn)
    if not breaker.allow:
        log_cadence_decision(conn, sport, game_id, hours_to_nearest, tier_label,
                              required_interval, minutes_since_last, False, None)
        print(f"  [odds/{sport}] SUPPRESSED -- {odds_circuit_breaker.describe(breaker)}")
        return
    if breaker.state == "HALF_OPEN_PROBE":
        print(f"  [odds/{sport}] PROBE -- {odds_circuit_breaker.describe(breaker)}")

    reason = "forced" if force else f"due (tier={tier_label})"
    try:
        collector = OddsCollector(conn, team_resolver, game_resolver, min_legitimate_poll_interval_minutes=0)
        summary = collector.collect(sport)
        success = get_last_run_success_flag(conn, "odds_collector")
        result = "success" if success else "failed"
        print(f"  [odds/{sport}] POLLED ({reason}): {summary}")
    except Exception as e:
        result = "failed"
        print(f"  [odds/{sport}] FAILED ({reason}): {type(e).__name__}: {e}")

    log_cadence_decision(conn, sport, game_id, hours_to_nearest, tier_label,
                          required_interval, minutes_since_last, True, result)


def check_and_run_context(conn, team_resolver, game_resolver, sport, force=False):
    is_gameday = (sport == "cfb" and _now().weekday() == 5) or (sport == "nfl" and _now().weekday() == 6)
    interval = CONTEXT_POLL_INTERVAL_MINUTES_GAMEDAY if is_gameday else CONTEXT_POLL_INTERVAL_MINUTES_NORMAL

    last_run = get_last_successful_run(conn, f"context_collector_{sport}")
    minutes_since_last = (_now() - last_run).total_seconds() / 60 if last_run else None
    due = force or minutes_since_last is None or minutes_since_last >= interval

    if not due:
        print(f"  [context/{sport}] not due -- needs {interval}min, only {minutes_since_last:.0f}min elapsed")
        return

    collector = ContextCollector(conn, team_resolver, game_resolver, min_legitimate_poll_interval_minutes=0)
    try:
        summary = collector.collect(sport)
        print(f"  [context/{sport}] POLLED: {summary}")
    except Exception as e:
        print(f"  [context/{sport}] FAILED: {e}")


def check_and_run_results(conn, team_resolver, game_resolver, sport, force=False):
    last_run = get_last_successful_run(conn, f"results_collector_{sport}")
    minutes_since_last = (_now() - last_run).total_seconds() / 60 if last_run else None
    due = force or minutes_since_last is None or minutes_since_last >= RESULTS_POLL_INTERVAL_MINUTES

    if not due:
        print(f"  [results/{sport}] not due -- needs {RESULTS_POLL_INTERVAL_MINUTES}min, "
              f"only {minutes_since_last:.0f}min elapsed")
        return

    collector = ResultsCollector(conn, team_resolver, game_resolver, min_legitimate_poll_interval_minutes=0)
    try:
        summary = collector.collect(sport)
        print(f"  [results/{sport}] POLLED: {summary}")
    except Exception as e:
        print(f"  [results/{sport}] FAILED: {e}")


def check_and_run_backup(force=False):
    backups = list_valid_backups(DEFAULT_BACKUP_DIR) if DEFAULT_BACKUP_DIR.exists() else []
    if backups and not force:
        last_backup_time = datetime.fromtimestamp(backups[-1].stat().st_mtime, tz=timezone.utc)
        minutes_since_last = (_now() - last_backup_time).total_seconds() / 60
        if minutes_since_last < BACKUP_INTERVAL_MINUTES:
            print(f"  [backup] not due -- needs {BACKUP_INTERVAL_MINUTES}min, "
                  f"only {minutes_since_last:.0f}min elapsed")
            return
    result = backup_database(retention=DEFAULT_RETENTION)
    if result:
        print(f"  [backup] SUCCESS: {result}")
    else:
        print("  [backup] FAILED -- see backup log for details")


def run_status(conn):
    from operations.collection_health import generate_report, format_report
    report = generate_report(conn)
    print(format_report(report))


def log_key_fingerprint():
    """
    Safe, non-secret comparison tool: prints presence, length, and a
    short SHA-256 fingerprint of THE_ODDS_API_KEY as seen in THIS
    process's actual environment -- never the key itself. Printed on
    EVERY run (manual or launchd-triggered), so comparing "what did my
    Terminal shell see" against "what did the unattended launchd process
    see" is just a matter of running this manually once and then checking
    the same line in operations/logs/run_collection.out.log after the
    next launchd-triggered (or launchctl start-triggered) run -- if the
    fingerprints differ, the two contexts are using different key values;
    if the length is 0, the key isn't visible to that process at all.
    """
    import hashlib
    key = os.environ.get("THE_ODDS_API_KEY")
    if not key:
        print("THE_ODDS_API_KEY: NOT SET in this process's environment (length=0)")
        return
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:12]
    print(f"THE_ODDS_API_KEY: present, length={len(key)}, fingerprint={fingerprint} "
          f"(compare this exact line across manual-Terminal and launchd-triggered runs)")


def update_experimental_mixed_signals(conn):
    """
    Prospectively track frozen MIXED/book-side-dominant v1 signals.

    Only games inside the final 15 minutes before kickoff are evaluated.
    While a game remains pre-kickoff, its row reflects the latest
    qualifying evaluation. After kickoff, it is never touched again.
    """

    now = _now()
    now_str = _now_str()
    window_end = (now + timedelta(minutes=15)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )

    games = conn.execute(
        """
        SELECT canonical_game_id, sport, commence_time
        FROM games_canonical
        WHERE sport IN ('nfl', 'cfb')
          AND commence_time > ?
          AND commence_time <= ?
        ORDER BY commence_time, canonical_game_id
        """,
        (now_str, window_end),
    ).fetchall()

    if not games:
        print("  [experimental] no NFL/CFB games inside T-15")
        return

    for canonical_game_id, sport, commence_time in games:
        try:
            signal = evaluate_experimental_mixed_signal(
                conn,
                canonical_game_id,
                sport,
                commence_time,
                now_str,
            )

            if signal is None:
                conn.execute(
                    """
                    DELETE FROM experimental_mixed_signals
                    WHERE canonical_game_id = ?
                      AND signal_version = ?
                    """,
                    (canonical_game_id, SIGNAL_VERSION),
                )
                conn.commit()

                print(
                    f"  [experimental/{sport}] game "
                    f"{canonical_game_id}: no qualifying signal"
                )
                continue

            written_at = _now_str()

            conn.execute(
                """
                INSERT INTO experimental_mixed_signals (
                    canonical_game_id,
                    sport,
                    signal_version,
                    as_of,
                    written_at,
                    public_side,
                    arbitrage_side,
                    side_preference_percentile,
                    aggregate_regime,
                    resistance_count,
                    divergence_count,
                    partial_divergence_count,
                    confirmation_count,
                    no_movement_count,
                    book_side_count,
                    signal_line,
                    signal_line_method,
                    data_quality,
                    full_record_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(canonical_game_id, signal_version)
                DO UPDATE SET
                    as_of = excluded.as_of,
                    written_at = excluded.written_at,
                    public_side = excluded.public_side,
                    arbitrage_side = excluded.arbitrage_side,
                    side_preference_percentile =
                        excluded.side_preference_percentile,
                    aggregate_regime = excluded.aggregate_regime,
                    resistance_count = excluded.resistance_count,
                    divergence_count = excluded.divergence_count,
                    partial_divergence_count =
                        excluded.partial_divergence_count,
                    confirmation_count = excluded.confirmation_count,
                    no_movement_count = excluded.no_movement_count,
                    book_side_count = excluded.book_side_count,
                    signal_line = excluded.signal_line,
                    signal_line_method = excluded.signal_line_method,
                    data_quality = excluded.data_quality,
                    full_record_json = excluded.full_record_json
                """,
                (
                    signal["canonical_game_id"],
                    signal["sport"],
                    signal["signal_version"],
                    signal["as_of"],
                    written_at,
                    signal["public_side"],
                    signal["arbitrage_side"],
                    signal["side_preference_percentile"],
                    signal["aggregate_regime"],
                    signal["resistance_count"],
                    signal["divergence_count"],
                    signal["partial_divergence_count"],
                    signal["confirmation_count"],
                    signal["no_movement_count"],
                    signal["book_side_count"],
                    signal["signal_line"],
                    signal["signal_line_method"],
                    signal["data_quality"],
                    json.dumps(signal["full_record"]),
                ),
            )

            conn.commit()

            print(
                f"  [experimental/{sport}] game "
                f"{canonical_game_id}: "
                f"{signal['arbitrage_side']} "
                f"{signal['signal_line']} "
                f"(SP {signal['side_preference_percentile']:.1f}, "
                f"book {signal['book_side_count']} vs "
                f"public {signal['confirmation_count']})"
            )

        except Exception as exc:
            print(
                f"  [experimental/{sport}] game "
                f"{canonical_game_id}: FAILED: {exc}"
            )
def main():
    parser = argparse.ArgumentParser(description="ArbitrEdge Needs collection orchestrator")
    parser.add_argument("--status", action="store_true", help="Read-only health report. No network calls.")
    parser.add_argument("--force", action="store_true", help="Bypass due-checks; poll everything now.")
    args = parser.parse_args()

    if args.status:
        # Read-only status reporting is never a production-writer safety
        # concern -- it makes no network calls and writes nothing -- so
        # it is always allowed regardless of PRODUCTION_ROLE/ARBITREDGE_ENV,
        # using the real production DB path for accurate diagnostics.
        db_path = DEFAULT_DB_PATH
        run_migration(db_path)
        seed(db_path)
        conn = get_connection(db_path)
        log_key_fingerprint()
        run_status(conn)
        conn.close()
        return

    try:
        db_path = resolve_authorized_db_path()
    except ProductionGuardRefusal as e:
        print(f"{'=' * 70}\nCOLLECTION RUN REFUSED BY PRODUCTION-ROLE SAFETY GUARD\n{'=' * 70}\n{e}\n{'=' * 70}")
        return

    run_migration(db_path)
    seed(db_path)

    conn = get_connection(db_path)

    log_key_fingerprint()

    print(f"{'=' * 70}\nArbitrEdge collection run -- {_now_str()}\n{'=' * 70}")

    team_resolver = TeamResolver(conn)
    game_resolver = GameResolver(conn, team_resolver)

    # backup always targets the real production database (backup_db.py's
    # own DEFAULT_DB_PATH) -- a TEST-mode run must never trigger it, since
    # TEST mode is meant to have zero interaction with production, even a
    # read-only one.
    if os.environ.get("ARBITREDGE_ENV") == "PRODUCTION":
        check_and_run_backup(force=args.force)
    for sport in ODDS_SPORTS:
        check_and_run_odds(conn, team_resolver, game_resolver, sport, force=args.force)
    for sport in SPORTS:
        check_and_run_context(conn, team_resolver, game_resolver, sport, force=args.force)
    for sport in SPORTS:
        check_and_run_results(conn, team_resolver, game_resolver, sport, force=args.force)
    update_experimental_mixed_signals(conn)

    conn.close()
    print(f"{'=' * 70}\nDone. Run with --status anytime for a health summary (no network calls).\n{'=' * 70}")


if __name__ == "__main__":
    main()
