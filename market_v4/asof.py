"""
market_v4/asof.py -- point-in-time market state and observed closes.

market_state(conn, event_id, market, T) uses ONLY rows fetched at or
before T whose book-reported update time is also <= T. Per book, the
latest observation is used; it is FRESH if T - fetched_at <= max_age_s.

Status:
  OK              at least one fresh book on the main line
  STALE           quotes exist but none is fresh (e.g. a provider outage):
                  the market is CLOSED for decisions and simulated arrivals
  NO_DATA         nothing observed for this event/market by T
  CLOSED_KICKOFF  T is at or after kickoff (pre-game markets only)

Default freshness: 2 x 15-minute collection interval + 15 min = 45 min
(V4 handoff rule). It is a parameter, recorded with every result.
"""

from collections import Counter

from edge_v4 import pricing

DEFAULT_MAX_AGE_S = 45 * 60
# UNVALIDATED ASSUMPTION: relative trust in market-making / low-margin books
# when forming the no-vig consensus. To be fitted against closing-line
# accuracy once data is available (Phase 4).
SHARP_WEIGHTS = {"pinnacle": 3.0, "circasports": 3.0, "betonlineag": 2.0, "lowvig": 2.0}
SIDES = {"h2h": ("home", "away"), "spreads": ("home", "away"), "totals": ("over", "under")}
MIN_SHARP = 2      # market-making books needed before the consensus uses them alone


def _latest_per_book(conn, event_id, market, T):
    rows = conn.execute(
        """SELECT q.book, q.side, q.point, q.price, q.fetched_at, q.market_last_update, q.commence
             FROM mkt_quote q
             JOIN (SELECT book, MAX(fetched_at) AS fa FROM mkt_quote
                    WHERE event_id=? AND market=? AND fetched_at<=? AND market_last_update<=?
                    GROUP BY book) l ON l.book=q.book AND l.fa=q.fetched_at
            WHERE q.event_id=? AND q.market=?""",
        (event_id, market, T, T, event_id, market)).fetchall()
    books = {}
    for book, side, point, price, fa, mlu, commence in rows:
        b = books.setdefault(book, {"fetched_at": fa, "market_last_update": mlu, "sides": {}, "commence": commence})
        b["sides"][side] = (price, point)
    return books


def market_state(conn, event_id, market, T, max_age_s=DEFAULT_MAX_AGE_S, weights=None, devig_method="multiplicative"):
    weights = SHARP_WEIGHTS if weights is None else weights
    out = {"event_id": event_id, "market": market, "as_of": T, "max_age_s": max_age_s,
           "devig_method": devig_method, "weights": dict(weights)}
    books = _latest_per_book(conn, event_id, market, T)
    if not books:
        return {**out, "status": "NO_DATA", "books": {}}
    commence = next(iter(books.values()))["commence"]
    out["commence"] = commence
    if T >= commence:
        return {**out, "status": "CLOSED_KICKOFF", "books": {}}
    a, b = SIDES[market]
    fresh = {k: v for k, v in books.items()
             if T - v["fetched_at"] <= max_age_s and a in v["sides"] and b in v["sides"]}
    out["books"] = {k: {"age_s": T - v["fetched_at"], "fresh": k in fresh, **{s: v["sides"].get(s) for s in (a, b)}}
                    for k, v in books.items()}
    if not fresh:
        newest = max(v["fetched_at"] for v in books.values())
        return {**out, "status": "STALE", "newest_quote_age_s": T - newest}
    # Consensus set: market-making books when >= MIN_SHARP of them are fresh, else every fresh
    # book. Books whose own two prices are under-round (a stale or mispriced pair) are kept
    # for scanning but excluded from the consensus -- one bad book must not poison the benchmark.
    def _ok(v):
        try:
            pricing.devig([v["sides"][a][0], v["sides"][b][0]])
            return True
        except ValueError:
            return False
    valid = {k: v for k, v in fresh.items() if _ok(v)}
    sharp_fresh = {k: v for k, v in valid.items() if k in weights}
    pool = sharp_fresh if len(sharp_fresh) >= MIN_SHARP else valid
    out["excluded_books"] = sorted(set(fresh) - set(valid))
    if not pool:
        return {**out, "status": "INCONSISTENT", "error": "no fresh book has a consistent two-way price"}
    # main line = most common point among consensus books (h2h has no point)
    line = None
    if market != "h2h":
        line = Counter(v["sides"][a][1] for v in pool.values()).most_common(1)[0][0]
    on_line = {k: v for k, v in pool.items() if market == "h2h" or v["sides"][a][1] == line}
    quotes = {k: (v["sides"][a][0], v["sides"][b][0]) for k, v in on_line.items()}
    w = {k: weights.get(k, 1.0) for k in quotes}
    fair_a = pricing.consensus_fair_prob(quotes, w, devig_method)
    every = {k: v for k, v in fresh.items() if market == "h2h" or v["sides"][a][1] == line}
    best_a = pricing.best_price({k: v["sides"][a][0] for k, v in every.items()})
    best_b = pricing.best_price({k: v["sides"][b][0] for k, v in every.items()})
    return {**out, "status": "OK", "line": line, "fair": {a: fair_a, b: 1.0 - fair_a},
            "best": {a: best_a, b: best_b}, "n_books_on_line": len(every),
            "consensus_books": sorted(quotes), "consensus_basis": "market_makers" if pool is sharp_fresh else "all_books",
            "sharp_books_on_line": sorted(k for k in quotes if k in weights)}


def observed_close(conn, event_id, market, max_age_s=DEFAULT_MAX_AGE_S, **kw):
    """The market as last OBSERVED before kickoff, or None with a reason.
    Never substitutes a later or earlier price for an unobserved close."""
    row = conn.execute("SELECT MIN(commence) FROM mkt_quote WHERE event_id=?", (event_id,)).fetchone()
    if not row or row[0] is None:
        return None, "no quotes for event"
    st = market_state(conn, event_id, market, row[0] - 1e-6, max_age_s, **kw)
    if st["status"] != "OK":
        return None, f"no fresh quote within {max_age_s // 60} min before kickoff ({st['status']})"
    return st, "observed"
