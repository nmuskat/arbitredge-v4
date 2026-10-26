"""
run_slate/discover_slate.py

Resolves upcoming canonical games for a sport/date range -- deliberately
does NOT require or check game_results at all, since RUN SLATE's whole
purpose is serving games that haven't been played yet. This is the exact
distinction the milestone is built to exploit: compute_features_v2 (pure)
never required a confirmed result; only persist_features_v2's retrospective
safeguard does, and that safeguard is never called here.
"""

from datetime import datetime, timezone


def _now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def discover_upcoming_slate(conn, sport, start_date, end_date, as_of=None):
    """
    sport: 'nfl' or 'cfb' -- the games_canonical identity sport, not a
    market_snapshots tag.
    start_date/end_date: 'YYYY-MM-DD', inclusive.
    as_of: optional ISO timestamp for reproducible/testable discovery
    (defaults to the real current moment). Only games with commence_time
    STRICTLY AFTER as_of are returned -- an already-started or completed
    game is never "upcoming."

    Returns a list of (canonical_game_id, home_team_id, away_team_id,
    commence_time) tuples. Does not touch game_results.
    """
    if sport not in ("nfl", "cfb"):
        raise ValueError(f"sport must be 'nfl' or 'cfb', got {sport!r}")

    as_of = as_of or _now_str()
    window_start = f"{start_date}T00:00:00Z"
    window_end = f"{end_date}T23:59:59Z"

    return conn.execute(
        "SELECT canonical_game_id, home_team_id, away_team_id, commence_time FROM games_canonical "
        "WHERE sport = ? AND commence_time > ? AND commence_time >= ? AND commence_time <= ? "
        "ORDER BY commence_time",
        (sport, as_of, window_start, window_end),
    ).fetchall()
