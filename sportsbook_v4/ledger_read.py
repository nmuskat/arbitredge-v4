"""
sportsbook_v4/ledger_read.py -- READ-ONLY views of the V4 book.

This is the only module a dashboard/API would call. It never writes:
open_readonly() uses sqlite's mode=ro, so even a bug here cannot create,
alter or delete a wager. Reading a book "as of" t only includes wagers
accepted at or before t.
"""

import sqlite3

from sportsbook_v4.settlement import exposure
from sportsbook_v4.util import from_epoch, to_epoch

_WAGER_COLS = ("wager_id", "bettor_index", "archetype", "canonical_game_id", "side", "team_id",
               "accepted_line", "accepted_home_spread", "accepted_price", "stake", "accepted_at",
               "offer_as_of", "interval_seq")


def open_readonly(db_path):
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def fetch_wagers(conn, run_id, game_id=None, as_of=None):
    sql = f"SELECT {', '.join(_WAGER_COLS)} FROM v4_wager WHERE run_id=?"
    args = [run_id]
    if game_id is not None:
        sql += " AND canonical_game_id=?"
        args.append(game_id)
    rows = conn.execute(sql + " ORDER BY accepted_at, wager_id", args).fetchall()
    out = [dict(zip(_WAGER_COLS, r)) for r in rows]
    if as_of is not None:
        cutoff = to_epoch(as_of)
        out = [w for w in out if to_epoch(w["accepted_at"]) <= cutoff]
    return out


def game_book(conn, run_id, game_id, as_of=None, reference_home_spread=None):
    return exposure(fetch_wagers(conn, run_id, game_id, as_of), reference_home_spread)


def exposure_timeline(conn, run_id, game_id, step_seconds=6 * 3600):
    """Exposure after each step of the clock -- how liability accumulated."""
    wagers = fetch_wagers(conn, run_id, game_id)
    if not wagers:
        return []
    start = to_epoch(wagers[0]["accepted_at"])
    end = to_epoch(wagers[-1]["accepted_at"])
    out, t = [], start - (start % step_seconds) + step_seconds
    while True:
        subset = [w for w in wagers if to_epoch(w["accepted_at"]) <= t]
        e = exposure(subset)
        out.append({k: e.get(k) for k in ("tickets", "handle", "home_ticket_pct", "home_handle_pct",
                                            "pnl_if_home_covers_all", "pnl_if_away_covers_all",
                                            "pnl_swing", "financial_need_side")} | {"as_of": from_epoch(t)})
        if t >= end:
            return out
        t += step_seconds
