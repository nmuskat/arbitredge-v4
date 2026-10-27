"""
web_v4/live.py -- website views over GENUINE collected odds (the V4 collector's
release-asset shards rebuilt into a market_v4 store).

Everything here is OBSERVED data: real quotes from The Odds API, shown as of
the last collection run for each sport, with its timestamp and age. Nothing
is filled in when a market or close was not observed.
"""

import time

from analysis_v4 import line_shop
from edge_v4 import ev
from edge_v4.forward import scan
from market_v4 import asof
from sharp_v4 import signals

SPORT_LABEL = {"americanfootball_nfl": "NFL", "americanfootball_ncaaf": "College football"}
MARKETS = ("spreads", "totals", "h2h")


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def last_fetch(conn, sport):
    r = conn.execute("SELECT MAX(fetched_at) FROM mkt_fetch WHERE sport=? AND n_quotes>0", (sport,)).fetchone()
    return r[0] if r else None


def events(conn, sport, after):
    return [{"event_id": e, "kickoff": k, "home": h, "away": a} for e, k, h, a in conn.execute(
        "SELECT event_id, MIN(commence), home, away FROM mkt_quote WHERE sport=? GROUP BY event_id "
        "HAVING MIN(commence) > ? ORDER BY MIN(commence), home", (sport, after))]


def _side_quote(st, side):
    best = (st.get("best") or {}).get(side)
    return {"book": best[0], "price": best[1]} if best else None


def board(conn, sport, now):
    """One row per upcoming game: the sharp consensus and the best available price per side,
    as observed at the sport's last collection run."""
    T = last_fetch(conn, sport)
    if T is None:
        return {"sport": sport, "label": SPORT_LABEL[sport], "observed_at": None, "games": []}
    rows = []
    for e in events(conn, sport, max(now, T)):
        g = {"event_id": e["event_id"], "home": e["home"], "away": e["away"], "kickoff": _iso(e["kickoff"]),
             "markets": {}}
        for m in MARKETS:
            st = asof.market_state(conn, e["event_id"], m, T)
            a, b = asof.SIDES[m]
            g["markets"][m] = {"status": st["status"], "line": st.get("line"),
                               "fair": st.get("fair"), "basis": st.get("consensus_basis"),
                               "consensus_books": st.get("consensus_books", []),
                               "n_books": st.get("n_books_on_line", 0),
                               "best": {a: _side_quote(st, a), b: _side_quote(st, b)},
                               "books": {k: {"age_s": round(v["age_s"]), "fresh": v["fresh"], a: v.get(a), b: v.get(b)}
                                         for k, v in sorted((st.get("books") or {}).items())}}
        rows.append(g)
    return {"sport": sport, "label": SPORT_LABEL[sport], "observed_at": _iso(T), "age_min": round((now - T) / 60),
            "games": rows}


def scanner_rows(conn, sport, now, policy=ev.LIVE_POLICY):
    """EV scan of the LAST observed snapshot (prices still the latest seen), plus first-sighting
    history across every snapshot with closing EV where a close was observed."""
    T = last_fetch(conn, sport)
    if T is None:
        return [], None
    mk, ms, tk, ts, label = line_shop.models(sport)
    evs = events(conn, sport, T)
    rows = [r for r in scan(conn, None, evs, T, mk, ms, tk, ts, policy=policy, record=False)
            if r.get("selection") and r["decision"] != ev.NO_BET]
    for r in rows:
        r["sport"] = sport
        r["model"] = label
        r["observed_at"] = _iso(T)
    hist = line_shop.report(conn, sport, policy)
    hist.pop("opportunity_rows", None)
    return rows, hist


def sharp_rows(conn, sport, now):
    T = last_fetch(conn, sport)
    out = []
    if T is None:
        return out
    for e in events(conn, sport, max(now, T)):
        for m in ("spreads", "totals"):
            r = signals.analyse(conn, e["event_id"], m, T)
            r.update(home=e["home"], away=e["away"], kickoff=_iso(e["kickoff"]), sport=sport)
            out.append(r)
    return out


def collection_health(conn, ledger_rows, now):
    month = time.strftime("%Y-%m", time.gmtime(now))
    month_rows = [r for r in ledger_rows if time.strftime("%Y-%m", time.gmtime(r["ts"])) == month]
    spent = sum((r["actual_cost"] if r["actual_cost"] is not None else r["est_cost"]) for r in month_rows)
    last_hdr = next((r for r in reversed(ledger_rows) if r.get("hdr_remaining") is not None), None)
    mismatches = [r for r in ledger_rows if r.get("note")]
    fetches = conn.execute("SELECT sport, COUNT(*), MIN(fetched_at), MAX(fetched_at), SUM(n_quotes) FROM mkt_fetch "
                           "GROUP BY sport").fetchall()
    books = conn.execute("SELECT sport, COUNT(DISTINCT book), GROUP_CONCAT(DISTINCT book) FROM mkt_quote "
                         "GROUP BY sport").fetchall()
    return {"month": month, "v4_credits_this_month": spent, "requests_this_month": len(month_rows),
            "provider_remaining": last_hdr and last_hdr["hdr_remaining"],
            "provider_used": last_hdr and last_hdr["hdr_used"],
            "provider_checked_at": last_hdr and _iso(last_hdr["ts"]),
            "cost_mismatches": len(mismatches),
            "fetches": [{"sport": SPORT_LABEL.get(s, s), "fetches": n, "first": _iso(a), "last": _iso(b), "quotes": q}
                        for s, n, a, b, q in fetches],
            "books": [{"sport": SPORT_LABEL.get(s, s), "count": n, "books": sorted(bk.split(","))} for s, n, bk in books],
            "anomalies": conn.execute("SELECT COUNT(*) FROM mkt_anomaly").fetchone()[0]}
