"""
market_v4/quality.py -- data-quality and market-integrity monitors.

Each detector returns a list of findings (dicts with a 'kind'). None of
them modifies data; findings are surfaced on System Health and block
decisions where relevant (e.g. a provider gap closes affected markets via
asof.market_state's STALE status).
"""

from collections import defaultdict

from edge_v4 import pricing


def fetch_gaps(conn, sport, expected_interval_s, start, end, tolerance=2.0):
    """Windows in [start, end] with no successful fetch for longer than
    tolerance x expected interval (provider outage, collector down, credits)."""
    times = [r[0] for r in conn.execute(
        "SELECT fetched_at FROM mkt_fetch WHERE sport=? AND fetched_at BETWEEN ? AND ? AND n_quotes>0 ORDER BY 1",
        (sport, start, end))]
    pts = [start] + times + [end]
    gaps = []
    for a, b in zip(pts, pts[1:]):
        if b - a > tolerance * expected_interval_s:
            gaps.append({"kind": "fetch_gap", "sport": sport, "from": a, "to": b, "hours": round((b - a) / 3600, 2)})
    return gaps


def games_affected_by_gaps(conn, gaps):
    """Games that kicked off inside a gap: their close was NOT observed."""
    out = []
    for g in gaps:
        for eid, home, away, commence in conn.execute(
                "SELECT DISTINCT event_id, home, away, commence FROM mkt_quote WHERE sport=? AND commence>? AND commence<=?",
                (g["sport"], g["from"], g["to"])):
            out.append({"kind": "close_unobserved", "event_id": eid, "home": home, "away": away, "commence": commence})
    return out


def stale_upcoming(conn, now, max_age_s):
    out = []
    from market_v4.asof import kickoffs
    ko = kickoffs(conn, as_of=now)
    for eid, home, away, last in conn.execute(
            "SELECT event_id, home, away, MAX(fetched_at) FROM mkt_quote WHERE fetched_at<=? GROUP BY event_id", (now,)):
        if ko.get(eid, (0,))[0] > now and now - last > max_age_s:
            out.append({"kind": "stale_market", "event_id": eid, "home": home, "away": away,
                        "age_min": round((now - last) / 60, 1)})
    return out


def thin_markets(conn, now, min_books=3, max_age_s=45 * 60):
    out = []
    for eid, market, n in conn.execute(
            "SELECT event_id, market, COUNT(DISTINCT book) FROM mkt_quote WHERE fetched_at BETWEEN ? AND ? "
            "AND commence>? GROUP BY event_id, market", (now - max_age_s, now, now)):
        if n < min_books:
            out.append({"kind": "few_books", "event_id": eid, "market": market, "books": n})
    return out


def duplicate_games(conn, window_s=36 * 3600):
    """Different provider event ids for the same matchup at nearly the same time
    (or with home/away swapped: a neutral-site or data-entry problem)."""
    rows = conn.execute("SELECT DISTINCT event_id, home, away, commence FROM mkt_quote").fetchall()
    out = []
    for i, (e1, h1, a1, c1) in enumerate(rows):
        for e2, h2, a2, c2 in rows[i + 1:]:
            if e1 != e2 and {h1, a1} == {h2, a2} and abs(c1 - c2) <= window_s:
                out.append({"kind": "duplicate_game" if (h1, a1) == (h2, a2) else "duplicate_game_swapped_home",
                            "event_ids": [e1, e2], "teams": [h1, a1]})
    return out


def unmatched_teams(conn, canonical_names):
    canon = set(canonical_names)
    names = {n for (n,) in conn.execute("SELECT home FROM mkt_quote UNION SELECT away FROM mkt_quote")}
    return [{"kind": "team_not_in_canonical_map", "name": n} for n in sorted(names - canon)]


def suspicious_moves(conn, event_id, market, jump=0.06, quiet=0.02):
    """A single book's no-vig probability jumps by >= `jump` between consecutive
    observations while the median of the other books moves < `quiet`:
    a stale, erroneous or limit-driven price, not market information."""
    a, b = {"h2h": ("home", "away"), "spreads": ("home", "away"), "totals": ("over", "under")}[market]
    series = defaultdict(dict)   # fetched_at -> book -> (fair_a, point)
    for book, side, point, price, fa in conn.execute(
            "SELECT book, side, point, price, fetched_at FROM mkt_quote WHERE event_id=? AND market=? ORDER BY fetched_at",
            (event_id, market)):
        series[fa].setdefault(book, {})[side] = (price, point)
    snaps = []
    for fa in sorted(series):
        fair = {}
        for book, s in series[fa].items():
            if a in s and b in s:
                try:
                    fair[book] = (pricing.devig([s[a][0], s[b][0]])[0], s[a][1])
                except ValueError:
                    pass
        snaps.append((fa, fair))
    out = []
    for (t0, f0), (t1, f1) in zip(snaps, snaps[1:]):
        for book in f0.keys() & f1.keys():
            if f0[book][1] != f1[book][1]:
                continue                       # line moved: compare like with like only
            d = f1[book][0] - f0[book][0]
            others = [f1[k][0] - f0[k][0] for k in f0.keys() & f1.keys() if k != book and f0[k][1] == f1[k][1]]
            if abs(d) >= jump and others:
                med = sorted(others)[len(others) // 2]
                if abs(med) < quiet:
                    out.append({"kind": "book_outlier_move", "event_id": event_id, "market": market, "book": book,
                                "from": t0, "to": t1, "delta_prob": round(d, 4), "others_median": round(med, 4)})
    return out
