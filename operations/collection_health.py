"""
operations/collection_health.py

Read-only. Never makes a network call -- safe to run as often as wanted,
including via `run_collection.py --status`.

RED for market data is determined directly against actual market_snapshots
coverage, not against theoretical schedule counts (requirement 6): a
game whose kickoff has already passed is RED only if it has zero
market_snapshots rows within 6 hours of its own kickoff -- i.e. the dense
near-kickoff tier genuinely never captured it. A quiet gap for a game
that hasn't kicked off yet, or one still within its currently-required
tier interval, is never RED (requirement 4).
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from operations.cadence_config import tier_for_hours  # noqa: E402


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _last_successful(conn, collector_name):
    row = conn.execute(
        "SELECT executed_at FROM collection_log WHERE collector_name = ? AND success = 1 "
        "ORDER BY executed_at DESC LIMIT 1",
        (collector_name,),
    ).fetchone()
    return _parse_iso(row[0]) if row else None


def _recent_consecutive_failures(conn, collector_name, lookback=5):
    rows = conn.execute(
        "SELECT success FROM collection_log WHERE collector_name = ? "
        "ORDER BY executed_at DESC LIMIT ?",
        (collector_name, lookback),
    ).fetchall()
    count = 0
    for (success,) in rows:
        if success:
            break
        count += 1
    return count


def _last_successful_odds_for_sport(conn, sport):
    """Sport-scoped, via cadence_decisions -- see the matching fix and
    explanation in operations/run_collection.py."""
    row = conn.execute(
        "SELECT checked_at FROM cadence_decisions WHERE sport = ? "
        "AND poll_triggered = 1 AND collector_run_result = 'success' "
        "ORDER BY checked_at DESC LIMIT 1",
        (sport,),
    ).fetchone()
    return _parse_iso(row[0]) if row else None


def _nearest_future_kickoff(conn, sport):
    if sport == "nfl_preseason":
        row = conn.execute(
            "SELECT canonical_game_id, commence_time FROM market_snapshots "
            "WHERE sport = 'nfl_preseason' AND commence_time > ? "
            "ORDER BY commence_time ASC LIMIT 1",
            (_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)
    row = conn.execute(
        "SELECT canonical_game_id, commence_time FROM games_canonical "
        "WHERE sport = ? AND commence_time > ? ORDER BY commence_time ASC LIMIT 1",
        (sport, _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _has_any_market_snapshots(conn, sport):
    row = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE sport = ?", (sport,)).fetchone()
    return row[0] > 0


def _odds_status(conn):
    results = {}
    for sport in ("nfl", "cfb", "nfl_preseason"):
        game_id, kickoff = _nearest_future_kickoff(conn, sport)
        hours_to_nearest = (_parse_iso(kickoff) - _now()).total_seconds() / 3600 if kickoff else None
        tier_label, required_interval = tier_for_hours(hours_to_nearest)

        last_run = _last_successful_odds_for_sport(conn, sport)
        minutes_since_last = (_now() - last_run).total_seconds() / 60 if last_run else None

        if minutes_since_last is None:
            status = "YELLOW"
            note = "no successful odds poll recorded yet"
        elif minutes_since_last <= required_interval:
            status = "GREEN"
            note = f"within current tier ({tier_label}, needs <= {required_interval}min)"
        else:
            status = "YELLOW"
            note = (f"overdue for current tier ({tier_label}, needs <= {required_interval}min, "
                    f"{minutes_since_last:.0f}min elapsed) -- still recoverable if kickoff hasn't passed")

        # Distinguishes "the cadence/collector ran successfully" from "usable
        # market observations were actually captured" -- a poll can complete
        # without raising an exception while every individual record insert
        # silently fails (e.g. a stale CHECK constraint rejecting every row
        # via safe_insert's per-record error isolation), producing a
        # misleadingly GREEN status with zero real data behind it. If we've
        # had at least one successful poll AND there's a real tracked game
        # for this sport, but literally zero market_snapshots rows exist for
        # it, that combination is never legitimate -- downgrade to RED.
        if status == "GREEN" and game_id is not None and not _has_any_market_snapshots(conn, sport):
            status = "RED"
            note = (f"cadence ran successfully (tier={tier_label}) but ZERO market_snapshots rows "
                    f"exist for sport={sport!r} despite a tracked game -- the collector is running "
                    f"without error but not actually capturing usable data (check for a stale schema "
                    f"constraint or silent per-record insert failures)")

        consecutive_failures = _recent_consecutive_failures(conn, "odds_collector")
        if consecutive_failures >= 3:
            status = "RED"
            note = f"{consecutive_failures} consecutive failed odds_collector runs"

        results[sport] = {
            "status": status, "note": note, "nearest_game_id": game_id,
            "hours_to_nearest_kickoff": hours_to_nearest, "tier_label": tier_label,
            "minutes_since_last_poll": minutes_since_last,
        }
    return results


def _missed_market_observations(conn, lookback_days=7):
    cutoff = (_now() - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    now_str = _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    past_games = conn.execute(
        "SELECT canonical_game_id, sport, commence_time FROM games_canonical "
        "WHERE commence_time < ? AND commence_time > ? AND sport IN ('nfl','cfb')",
        (now_str, cutoff),
    ).fetchall()

    missed = []
    for gid, sport, commence_time in past_games:
        kickoff_dt = _parse_iso(commence_time)
        window_start = (kickoff_dt - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        row = conn.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE canonical_game_id = ? "
            "AND observed_at >= ? AND observed_at <= ?",
            (gid, window_start, commence_time),
        ).fetchone()
        if row[0] == 0:
            missed.append({"canonical_game_id": gid, "sport": sport, "commence_time": commence_time})
    return missed


def _simple_freshness(conn, collector_name, interval_minutes, label):
    last_run = _last_successful(conn, collector_name)
    if last_run is None:
        return {"status": "YELLOW", "note": f"no successful {label} run recorded yet"}
    minutes_since = (_now() - last_run).total_seconds() / 60
    consecutive_failures = _recent_consecutive_failures(conn, collector_name)
    if consecutive_failures >= 3:
        return {"status": "RED", "note": f"{consecutive_failures} consecutive failed {label} runs"}
    if minutes_since <= interval_minutes:
        return {"status": "GREEN", "note": f"last successful run {minutes_since:.0f}min ago"}
    return {"status": "YELLOW", "note": f"last successful run {minutes_since:.0f}min ago "
                                          f"(expected within {interval_minutes}min) -- data recoverable"}


def _unresolved_aliases_status(conn, active_window_hours=48):
    """
    Splits unresolved_aliases into ACTIVE vs HISTORICAL AUDIT, reusing the
    same recency-based discrimination principle already established in
    collectors/run_odds_discovery.py's classify_unresolved_aliases -- a row
    is only ever written on a resolution FAILURE and a later successful
    resolution never touches or clears it, so a raw COUNT(*) mixes
    permanently-preserved historical audit rows with genuinely current
    ones. Since a health check has no single "this run" boundary the way a
    discovery script does, ACTIVE here is instead a rolling recency window
    on last_seen_at: unresolved_aliases.last_seen_at is updated on every
    occurrence, including repeat failures, so a row still being hit by
    ongoing polling will have a recent last_seen_at; a row that hasn't been
    touched in 48+ hours despite the current polling cadence (which
    touches every tracked sport at least once/day even in the sparsest
    tier) is no longer plausibly tied to an active resolution failure.
    No rows are ever deleted or modified here -- this is read-only
    classification for reporting purposes only.
    """
    cutoff = (_now() - timedelta(hours=active_window_hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    active = conn.execute(
        "SELECT COUNT(DISTINCT raw_text) FROM unresolved_aliases WHERE last_seen_at >= ?", (cutoff,)
    ).fetchone()[0]
    historical = conn.execute(
        "SELECT COUNT(DISTINCT raw_text) FROM unresolved_aliases WHERE last_seen_at < ?", (cutoff,)
    ).fetchone()[0]
    active_rows = conn.execute(
        "SELECT sport, source, raw_text, reason, occurrence_count, last_seen_at "
        "FROM unresolved_aliases WHERE last_seen_at >= ? ORDER BY last_seen_at DESC",
        (cutoff,),
    ).fetchall()
    return {
        "active_count": active,
        "historical_count": historical,
        "active_window_hours": active_window_hours,
        "active_rows": active_rows,
    }


def generate_report(conn):
    from operations.cadence_config import CONTEXT_POLL_INTERVAL_MINUTES_NORMAL, RESULTS_POLL_INTERVAL_MINUTES

    odds_status = _odds_status(conn)
    missed_market = _missed_market_observations(conn)
    context_status = _simple_freshness(conn, "context_collector", CONTEXT_POLL_INTERVAL_MINUTES_NORMAL, "context")
    results_status = _simple_freshness(conn, "results_collector", RESULTS_POLL_INTERVAL_MINUTES, "results")

    unresolved = _unresolved_aliases_status(conn)

    tracked_games = conn.execute(
        "SELECT sport, COUNT(*) FROM games_canonical WHERE commence_time > ? GROUP BY sport",
        (_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),),
    ).fetchall()

    odds_credits_this_month = conn.execute(
        "SELECT COUNT(*) FROM raw_responses WHERE collector_name = 'odds_collector' "
        "AND strftime('%Y-%m', retrieved_at) = strftime('%Y-%m', 'now')"
    ).fetchone()[0]

    return {
        "generated_at": _now().isoformat(),
        "odds": odds_status,
        "missed_market_observations": missed_market,
        "context": context_status,
        "results": results_status,
        "unresolved": unresolved,
        "tracked_games": dict(tracked_games),
        "odds_credits_this_month_actual": odds_credits_this_month,
        "odds_credits_monthly_budget": 500,
        "backup_note": "see db/backups/backup.log for last-backup timestamp",
    }


def format_report(report):
    lines = []
    lines.append("=" * 70)
    lines.append(f"ArbitrEdge Collection Health -- {report['generated_at']}")
    lines.append("=" * 70)

    lines.append("\nODDS:")
    for sport, info in report["odds"].items():
        lines.append(f"  [{info['status']}] {sport.upper()}: {info['note']}")

    if report["missed_market_observations"]:
        lines.append(f"\n[RED] {len(report['missed_market_observations'])} PERMANENTLY MISSED market "
                      f"observation(s) -- games whose kickoff already passed with zero snapshots "
                      f"within 6h of kickoff:")
        for m in report["missed_market_observations"]:
            lines.append(f"    game={m['canonical_game_id']} sport={m['sport']} kickoff={m['commence_time']}")
    else:
        lines.append("\n[GREEN] No permanently missed market observations in the last 7 days.")

    lines.append(f"\nCONTEXT: [{report['context']['status']}] {report['context']['note']}")
    lines.append(f"RESULTS: [{report['results']['status']}] {report['results']['note']}")

    lines.append(f"\nUnresolved aliases:")
    lines.append(f"  ACTIVE (last seen within {report['unresolved']['active_window_hours']}h -- "
                 f"needs attention): {report['unresolved']['active_count']}")
    if report["unresolved"]["active_rows"]:
        for sport, source, raw_text, reason, occurrence_count, last_seen_at in report["unresolved"]["active_rows"]:
            lines.append(f"    [{sport}/{source}] {raw_text!r} reason={reason} "
                         f"seen={occurrence_count}x last={last_seen_at}")
    lines.append(f"  HISTORICAL AUDIT (older, preserved, not currently actionable): "
                 f"{report['unresolved']['historical_count']}")
    lines.append(f"Games currently tracked (future): {report['tracked_games']}")
    lines.append(f"\nOdds API credits used this month (actual, not theoretical): "
                 f"{report['odds_credits_this_month_actual']} / {report['odds_credits_monthly_budget']}")
    lines.append(f"Backup: {report['backup_note']}")
    lines.append("=" * 70)
    return "\n".join(lines)
