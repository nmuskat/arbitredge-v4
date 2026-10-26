"""
market_v4/store.py -- append-only quote store (SQLite now; same schema maps
to PostgreSQL on the dev server).

Every observation is kept: the same price seen again at a later fetch is a
new row, because "still posted at time T" is what freshness and execution
checks need. Rows are immutable (triggers). Times are UTC epoch seconds.
"""

import calendar
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS mkt_fetch (
  id INTEGER PRIMARY KEY,
  fetched_at REAL NOT NULL,
  sport TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  request_fp TEXT NOT NULL,
  source TEXT NOT NULL,
  n_quotes INTEGER NOT NULL,
  n_anomalies INTEGER NOT NULL,
  UNIQUE (fetched_at, sport, endpoint, request_fp)
);
CREATE TABLE IF NOT EXISTS mkt_quote (
  id INTEGER PRIMARY KEY,
  event_id TEXT NOT NULL, sport TEXT NOT NULL, commence REAL NOT NULL,
  home TEXT NOT NULL, away TEXT NOT NULL,
  book TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
  point REAL, price INTEGER NOT NULL,
  market_last_update REAL NOT NULL, fetched_at REAL NOT NULL,
  source TEXT NOT NULL, request_fp TEXT NOT NULL,
  UNIQUE (event_id, book, market, side, fetched_at)
);
CREATE INDEX IF NOT EXISTS mkt_quote_asof ON mkt_quote (event_id, market, book, fetched_at);
CREATE TABLE IF NOT EXISTS mkt_anomaly (
  id INTEGER PRIMARY KEY, fetched_at REAL NOT NULL, kind TEXT NOT NULL,
  event_id TEXT, book TEXT, market TEXT, detail TEXT
);
"""
for _t in ("mkt_fetch", "mkt_quote", "mkt_anomaly"):
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_t}_no_update BEFORE UPDATE ON {_t}
BEGIN SELECT RAISE(ABORT, '{_t} is append-only'); END;
CREATE TRIGGER IF NOT EXISTS {_t}_no_delete BEFORE DELETE ON {_t}
BEGIN SELECT RAISE(ABORT, '{_t} is append-only'); END;"""


def iso_to_epoch(s):
    """'2026-10-26T17:44:03Z' (or with fractional seconds / +00:00) -> epoch seconds."""
    s = s.strip().replace("+00:00", "Z")
    if not s.endswith("Z"):
        raise ValueError(f"timestamp must be UTC: {s!r}")
    base, _, frac = s[:-1].partition(".")
    t = calendar.timegm(time.strptime(base, "%Y-%m-%dT%H:%M:%S"))
    return t + (float("0." + frac) if frac else 0.0)


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def ingest(conn, quotes, anomalies, fetched_at_iso, sport, endpoint, request_fp, source="odds_api"):
    """Store one fetch atomically. Returns the number of quote rows inserted."""
    fa = iso_to_epoch(fetched_at_iso)
    with conn:
        conn.execute(
            "INSERT INTO mkt_fetch (fetched_at, sport, endpoint, request_fp, source, n_quotes, n_anomalies)"
            " VALUES (?,?,?,?,?,?,?)", (fa, sport, endpoint, request_fp, source, len(quotes), len(anomalies)))
        n = 0
        for q in quotes:
            cur = conn.execute(
                "INSERT OR IGNORE INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point,"
                " price, market_last_update, fetched_at, source, request_fp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (q.event_id, q.sport or sport, iso_to_epoch(q.commence_time), q.home_team, q.away_team, q.book,
                 q.market, q.side, q.point, q.price, iso_to_epoch(q.market_last_update), fa, q.source, q.request_fp))
            n += cur.rowcount
        for a in anomalies:
            conn.execute("INSERT INTO mkt_anomaly (fetched_at, kind, event_id, book, market, detail) VALUES (?,?,?,?,?,?)",
                         (fa, a["kind"], a.get("event"), a.get("book"), a.get("market"), repr(a)))
    return n
