"""
Deterministic, realistic-shaped fixtures for V4 tests and diagnostics.

These are SYNTHETIC market paths written for testing -- not historical
quotes. They model: Sunday-night openers, mid-week moves, a key-number
move through 3, a favourite flip, books updating at different times
(reference books lead, retail books lag), and a pick'em.
"""

import sqlite3
from datetime import timedelta

from sportsbook_v4.engine import GameSpec, SlateSpec
from sportsbook_v4.facts import FixtureFacts
from sportsbook_v4.market import FixtureMarket, quote
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.util import parse_ts, to_epoch

BOOKS = ["betmgm", "betonlineag", "caesars", "draftkings", "fanduel", "lowvig"]
REFERENCE_BOOKS = {"lowvig", "betonlineag"}
RETAIL_LAG = timedelta(hours=3)   # retail books copy reference moves 3 hours later

WINDOW_START = "2026-10-26T00:00:00Z"   # Sunday evening CT: next week's lines open
WINDOW_END = "2026-11-03T01:15:00Z"     # Monday night kickoff

NFL_TEAMS = ["nfl_buf", "nfl_dal", "nfl_det", "nfl_gb", "nfl_kc", "nfl_lv", "nfl_mia",
             "nfl_nyg", "nfl_phi", "nfl_sf", "nfl_ten", "nfl_was"]

# (game_id, home, away, kickoff, standalone, national_tv,
#  path: list of (time, home_spread, home_price, away_price) set by REFERENCE books)
NFL_WEEK = [
    ("g_tnf", "nfl_phi", "nfl_dal", "2026-10-30T00:15:00Z", True, True, [
        ("2026-10-26T00:00:00Z", -3.5, -110, -110),
        ("2026-10-27T15:00:00Z", -3.0, -115, -105),
        ("2026-10-29T20:00:00Z", -2.5, -110, -110),
    ]),
    ("g_sun1", "nfl_kc", "nfl_lv", "2026-11-01T18:00:00Z", False, False, [
        ("2026-10-26T00:00:00Z", -9.5, -110, -110),
        ("2026-10-28T18:00:00Z", -10.5, -110, -110),
    ]),
    ("g_sun2", "nfl_mia", "nfl_buf", "2026-11-01T18:00:00Z", False, False, [
        ("2026-10-26T00:00:00Z", 2.5, -110, -110),
        ("2026-10-29T14:00:00Z", 1.0, -110, -110),
        ("2026-10-31T16:00:00Z", -1.0, -110, -110),   # favourite flips
    ]),
    ("g_sun3", "nfl_ten", "nfl_nyg", "2026-11-01T18:00:00Z", False, False, [
        ("2026-10-26T00:00:00Z", 0.0, -110, -110),    # pick'em all week
    ]),
    ("g_sun4", "nfl_sf", "nfl_was", "2026-11-01T21:25:00Z", False, True, [
        ("2026-10-26T00:00:00Z", -6.0, -110, -110),
        ("2026-10-30T15:00:00Z", -7.0, -120, +100),
    ]),
    ("g_snf", "nfl_det", "nfl_gb", "2026-11-02T01:20:00Z", True, True, [
        ("2026-10-26T00:00:00Z", -3.0, -110, -110),
        ("2026-10-28T13:00:00Z", -3.0, -120, +100),
        ("2026-11-01T20:00:00Z", -3.5, -110, -110),
    ]),
    ("g_mnf", "nfl_buf", "nfl_kc", "2026-11-03T01:15:00Z", True, True, [
        ("2026-10-26T00:00:00Z", 1.5, -110, -110),
        ("2026-10-31T12:00:00Z", 2.5, -110, -110),
    ]),
]


def build_quotes(week=NFL_WEEK, retail_lag=RETAIL_LAG, source_id="fixture:nfl_week_v1"):
    quotes = {}
    for gid, _h, _a, _ko, _s, _tv, path in week:
        qs = []
        for i, (ts, hs, hp, ap) in enumerate(path):
            t = parse_ts(ts)
            for book in BOOKS:
                bt = t if (book in REFERENCE_BOOKS or i == 0) else t + retail_lag
                qs.append(quote(book, bt.isoformat(), hs, hp, ap, ref=f"fx:{gid}:{book}:{i}"))
        quotes[gid] = qs
    return FixtureMarket(quotes, source_id=source_id)


def static_week(week=NFL_WEEK):
    """Market counterfactual: every line frozen at its opening quote."""
    return [(gid, h, a, ko, st, tv, path[:1]) for gid, h, a, ko, st, tv, path in week]


def reversed_week(week=NFL_WEEK):
    """
    Market counterfactual: every move after the open is mirrored around the
    opening number (a move toward the favourite becomes an equal move toward
    the underdog), and juice-only moves swap sides. Same timing, same books.
    """
    out = []
    for gid, h, a, ko, st, tv, path in week:
        open_hs = path[0][1]
        new_path = [path[0]]
        for ts, hs, hp, ap in path[1:]:
            new_path.append((ts, open_hs - (hs - open_hs), ap, hp))
        out.append((gid, h, a, ko, st, tv, new_path))
    return out


def build_slate(week=NFL_WEEK, slate_id="nfl_2026_w9_fixture"):
    games = tuple(GameSpec(gid, h, a, ko, s, tv) for gid, h, a, ko, s, tv, _ in week)
    return SlateSpec("nfl", slate_id, WINDOW_START, WINDOW_END, games)


def build_facts():
    t0 = to_epoch("2026-10-26T00:00:00Z")
    return FixtureFacts({
        "g_tnf": [(t0, {"home_wins_last_3": 3, "away_wins_last_3": 1,
                        "home_point_margin_avg_last_3": 10.0, "away_point_margin_avg_last_3": -4.0,
                        "home_current_win_streak": 4, "away_current_win_streak": 0})],
        "g_sun1": [(t0, {"home_wins_last_3": 2, "away_wins_last_3": 0})],
    })


def symmetric_week():
    """Each matchup appears twice with home/away swapped and the spread mirrored."""
    base = [
        ("s1", "nfl_buf", "nfl_dal", -3.5), ("s2", "nfl_det", "nfl_gb", -7.0),
        ("s3", "nfl_kc", "nfl_lv", 0.0), ("s4", "nfl_mia", "nfl_nyg", -1.5),
    ]
    week = []
    for gid, h, a, hs in base:
        for suffix, home, away, spread in (("a", h, a, hs), ("b", a, h, -hs)):
            week.append((f"{gid}{suffix}", home, away, "2026-11-01T18:00:00Z", False, False,
                         [("2026-10-26T00:00:00Z", spread, -110, -110)]))
    return week


def new_v4_db(path=":memory:"):
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    return conn
