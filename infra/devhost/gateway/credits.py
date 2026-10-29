"""
edge_v4/credits.py -- Odds API credit budget, cost estimation, cache.

V3 and V4 share ONE subscription (20,000 credits / month, resets on the
1st). V4 must never starve V3. Every V4 request passes through
CreditGate.authorize() BEFORE it is sent, and record() AFTER, with the
provider's x-requests-* headers.

COST TABLE -- per The Odds API v4 docs as audited (the docs host was not
reachable from the build sandbox, so the table is marked UNVERIFIED until
the first live response's `x-requests-last` header confirms each entry;
record() flags any mismatch). The provider charges for markets actually
RETURNED, so estimates are upper bounds.
  /sports, /events                         0
  /odds, /events/{id}/odds                 markets x regions
  /scores                                  1 (2 with daysFrom)
  /events/{id}/markets, /participants      1
  /historical/odds, /historical/events/{id}/odds   10 x markets x regions
  /historical/events                       1
`bookmakers=` instead of `regions=`: each group of up to 10 books = 1 region.
"""

import calendar
import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass

COST_TABLE_STATUS = "UNVERIFIED_UNTIL_HEADER_CONFIRMED"


def region_units(regions=None, bookmakers=None):
    if bookmakers:
        return math.ceil(len(set(bookmakers)) / 10)
    if not regions:
        raise ValueError("regions or bookmakers required")
    return len(set(regions))


def estimate_cost(endpoint, markets=(), regions=None, bookmakers=None, days_from=None):
    e = endpoint
    if e in ("sports", "events"):
        return 0
    if e == "scores":
        return 2 if days_from else 1
    if e in ("event_markets", "participants", "historical_events"):
        return 1
    n_mk = len(set(markets))
    if n_mk == 0:
        raise ValueError("markets required")
    base = n_mk * region_units(regions, bookmakers)
    if e in ("odds", "event_odds"):
        return base
    if e in ("historical_odds", "historical_event_odds"):
        return 10 * base
    raise ValueError(f"unknown endpoint {endpoint!r}")


SCHEMA = """
CREATE TABLE IF NOT EXISTS credit_ledger (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  app TEXT NOT NULL CHECK (app IN ('v4')),
  endpoint TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  est_cost INTEGER NOT NULL,
  actual_cost INTEGER,
  hdr_used INTEGER,
  hdr_remaining INTEGER,
  status INTEGER,
  note TEXT
);
CREATE TABLE IF NOT EXISTS response_cache (
  fingerprint TEXT PRIMARY KEY,
  fetched_at REAL NOT NULL,
  body TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS credit_ledger_no_update BEFORE UPDATE OF ts, app, endpoint, fingerprint, est_cost ON credit_ledger
BEGIN SELECT RAISE(ABORT, 'credit_ledger rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS credit_ledger_no_delete BEFORE DELETE ON credit_ledger
BEGIN SELECT RAISE(ABORT, 'credit_ledger rows are immutable'); END;
"""


def fingerprint(endpoint, params):
    clean = {k: v for k, v in params.items() if k.lower() not in ("apikey", "api_key")}
    return hashlib.sha256(json.dumps([endpoint, clean], sort_keys=True, default=str).encode()).hexdigest()


def month_start(ts):
    """Start of the UTC calendar month containing ts (provider resets on the 1st)."""
    t = time.gmtime(ts)
    return calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0, 0, 0, 0))


@dataclass(frozen=True)
class Budget:
    monthly_total: int = 20_000
    v4_monthly_cap: int = 6_000        # owner-approved 2026-10-26; leaves 14,000 to V3
    v4_daily_cap: int = 400
    v3_floor_remaining: int = 2_000    # never let provider-remaining fall below this because of V4
    max_single_request: int = 200      # anything larger requires explicit owner approval


class CreditRefused(Exception):
    pass


class CreditGate:
    def __init__(self, conn: sqlite3.Connection, budget: Budget = Budget(), clock=time.time):
        self.conn, self.budget, self.clock = conn, budget, clock
        conn.executescript(SCHEMA)

    def _spent_since(self, since):
        row = self.conn.execute(
            "SELECT COALESCE(SUM(COALESCE(actual_cost, est_cost)),0) FROM credit_ledger WHERE ts>=?", (since,)
        ).fetchone()
        return row[0]

    def last_provider_remaining(self):
        row = self.conn.execute(
            "SELECT hdr_remaining, ts FROM credit_ledger WHERE hdr_remaining IS NOT NULL ORDER BY ts DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if not row or row[1] < month_start(self.clock()):
            return None
        return row[0]

    def cached(self, endpoint, params, ttl_s):
        fp = fingerprint(endpoint, params)
        row = self.conn.execute("SELECT fetched_at, body FROM response_cache WHERE fingerprint=?", (fp,)).fetchone()
        if row and self.clock() - row[0] <= ttl_s:
            return json.loads(row[1])
        return None

    def authorize(self, endpoint, params, est_cost, owner_approved_large=False, require_remaining=False):
        now, b = self.clock(), self.budget
        if est_cost > b.max_single_request and not owner_approved_large:
            raise CreditRefused(f"single request est {est_cost} > {b.max_single_request}: owner approval required")
        month = self._spent_since(month_start(now))
        if month + est_cost > b.v4_monthly_cap:
            raise CreditRefused(f"V4 monthly cap: {month}+{est_cost} > {b.v4_monthly_cap}")
        day = self._spent_since(now - 86400)
        if day + est_cost > b.v4_daily_cap:
            raise CreditRefused(f"V4 24h cap: {day}+{est_cost} > {b.v4_daily_cap}")
        remaining = self.last_provider_remaining()
        if remaining is None and require_remaining:
            raise CreditRefused("provider balance unknown: cannot verify the V3 floor (failing closed)")
        if remaining is not None and remaining - est_cost < b.v3_floor_remaining:
            raise CreditRefused(f"provider remaining {remaining} - {est_cost} would breach V3 floor {b.v3_floor_remaining}")
        return fingerprint(endpoint, params)

    def record(self, endpoint, params, est_cost, status, headers, body=None):
        h = {k.lower(): v for k, v in (headers or {}).items()}
        last = h.get("x-requests-last")
        actual = int(float(last)) if last is not None else None
        note = None
        if actual is not None and actual != est_cost:
            note = f"cost mismatch: est {est_cost} vs provider {actual}" + (
                " (EXCEEDS estimate: cost table must be corrected)" if actual > est_cost else "")
        fp = fingerprint(endpoint, params)
        self.conn.execute(
            "INSERT INTO credit_ledger (ts, app, endpoint, fingerprint, est_cost, actual_cost, hdr_used, hdr_remaining, status, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (self.clock(), "v4", endpoint, fp, est_cost, actual,
             _int(h.get("x-requests-used")), _int(h.get("x-requests-remaining")), status, note),
        )
        if body is not None and status == 200:
            self.conn.execute(
                "INSERT OR REPLACE INTO response_cache (fingerprint, fetched_at, body) VALUES (?,?,?)",
                (fp, self.clock(), json.dumps(body)),
            )
        self.conn.commit()
        return note

    def usage_split(self):
        """Attribute month-to-date usage: V4 = own ledger; V3 = provider used - V4."""
        since = month_start(self.clock())
        v4 = self._spent_since(since)
        row = self.conn.execute(
            "SELECT hdr_used FROM credit_ledger WHERE hdr_used IS NOT NULL AND ts>=? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (since,)
        ).fetchone()
        provider_used = row[0] if row else None
        return {"v4": v4, "provider_used": provider_used,
                "v3_inferred": None if provider_used is None else max(0, provider_used - v4)}


def _int(v):
    return None if v is None else int(float(v))
