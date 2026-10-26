"""
features/market_freshness.py

Explicit, read-only market-data freshness for downstream consumers.

Problem: during an odds-collection outage, every point-in-time consumer
(market_state_as_of -> offered_line, public_pressure's consensus spread,
handle-intensity activity counts, the dashboard) keeps returning "the latest
observation at or before as_of". That observation can be hours or days old
and nothing downstream says so: the V3 synthetic ledger keeps pricing at the
last pre-outage line, and handle intensity reads "no new snapshots" as "quiet
market".

Definition used here (same cadence the collector itself promises):
  required_interval = operations.cadence_config.tier_for_hours(hours to the
  game's kickoff) -- 20 min inside 1h, 60 min inside 6h, 8h inside 24h, ...
  A game's market data is STALE when its newest observation is older than
      STALE_MULTIPLIER x required_interval + GRACE_MINUTES
  (i.e. more than ~two scheduled polls have been missed). NO_DATA when the
  game has never been observed.
"""

from datetime import datetime, timezone

from operations.cadence_config import tier_for_hours

STALE_MULTIPLIER = 2
GRACE_MINUTES = 15

FRESH, STALE, NO_DATA = "FRESH", "STALE", "NO_DATA"


def _parse(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def classify_age(age_minutes, hours_to_kickoff):
    tier, interval = tier_for_hours(hours_to_kickoff)
    limit = STALE_MULTIPLIER * interval + GRACE_MINUTES
    if age_minutes is None:
        return NO_DATA, tier, interval, limit
    return (FRESH if age_minutes <= limit else STALE), tier, interval, limit


def game_freshness(conn, canonical_game_id, now=None):
    now = now or datetime.now(timezone.utc)
    row = conn.execute("SELECT commence_time FROM games_canonical WHERE canonical_game_id = ?",
                       (canonical_game_id,)).fetchone()
    kickoff = _parse(row[0]) if row else None
    obs = conn.execute(
        "SELECT MAX(observed_at) FROM market_snapshots WHERE canonical_game_id = ? AND observed_at <= ?",
        (canonical_game_id, now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
    ).fetchone()[0]
    last = _parse(obs) if obs else None
    age = (now - last).total_seconds() / 60 if last else None
    hours = (kickoff - now).total_seconds() / 3600 if kickoff else None
    status, tier, interval, limit = classify_age(age, hours if hours is None or hours > 0 else 0.0)
    return {
        "status": status, "last_market_observation": _iso(last),
        "age_minutes": None if age is None else round(age, 1),
        "tier": tier, "expected_interval_minutes": interval, "stale_after_minutes": limit,
    }


def sport_freshness(conn, now=None):
    """Newest observation per market_snapshots.sport and latest successful odds poll."""
    now = now or datetime.now(timezone.utc)
    per_sport = {}
    for sport, last in conn.execute("SELECT sport, MAX(observed_at) FROM market_snapshots GROUP BY sport"):
        t = _parse(last)
        per_sport[sport] = {"last_market_observation": _iso(t),
                            "age_minutes": round((now - t).total_seconds() / 60, 1)}
    row = conn.execute(
        "SELECT MAX(executed_at) FROM collection_log WHERE collector_name = 'odds_collector' "
        "AND success = 1 AND (error_type IS NULL OR error_type != 'SkippedTooSoon')"
    ).fetchone()
    last_ok = _parse(row[0]) if row and row[0] else None
    fail = conn.execute(
        "SELECT COUNT(*) FROM collection_log WHERE collector_name = 'odds_collector' AND success = 0 "
        "AND executed_at > ?", ((last_ok or datetime(1970, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),)
    ).fetchone()[0]
    return {"by_sport": per_sport, "last_successful_odds_poll": _iso(last_ok),
            "failed_odds_runs_since_last_success": fail}
