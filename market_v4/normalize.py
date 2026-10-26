"""
market_v4/normalize.py -- Odds API v4 JSON -> flat quote rows.

Accepts the /odds and /events/{id}/odds payloads (a list of events, or one
event) and the historical wrapper {"timestamp", "data": ...}. Every row
carries provenance: source, fetched_at (when WE received it), the book's
own market last_update, and the request fingerprint. Rows that fail
validation are returned separately as anomalies, never silently dropped.
"""

from dataclasses import dataclass

SUPPORTED_MARKETS = ("h2h", "spreads", "totals")


@dataclass(frozen=True)
class Quote:
    event_id: str
    sport: str
    commence_time: str
    home_team: str
    away_team: str
    book: str
    market: str
    side: str            # home | away | over | under | draw
    point: float | None
    price: int
    market_last_update: str
    fetched_at: str
    source: str
    request_fp: str


def _side(market, name, home, away):
    if market in ("h2h", "spreads"):
        if name == home:
            return "home"
        if name == away:
            return "away"
        if name.lower() == "draw":
            return "draw"
        return None
    if market == "totals":
        return {"over": "over", "under": "under"}.get(name.lower())
    return None


def normalize(payload, fetched_at, request_fp="", source="odds_api"):
    """Returns (quotes, anomalies). fetched_at: ISO-8601 UTC string."""
    snapshot_ts = None
    if isinstance(payload, dict) and "data" in payload and "timestamp" in payload:
        snapshot_ts = payload["timestamp"]           # historical snapshot time
        payload = payload["data"]
        source = source + ":historical"
    events = payload if isinstance(payload, list) else [payload]
    quotes, anomalies = [], []
    for ev in events:
        eid, home, away = ev.get("id"), ev.get("home_team"), ev.get("away_team")
        if not (eid and home and away and ev.get("commence_time")):
            anomalies.append({"kind": "event_missing_fields", "event": eid})
            continue
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                m = mk.get("key")
                if m not in SUPPORTED_MARKETS:
                    continue
                rows = []
                for oc in mk.get("outcomes", []):
                    side = _side(m, oc.get("name", ""), home, away)
                    price = oc.get("price")
                    point = oc.get("point")
                    if side is None:
                        anomalies.append({"kind": "unmatched_outcome_name", "event": eid, "book": bk.get("key"),
                                          "market": m, "name": oc.get("name")})
                        continue
                    if not isinstance(price, (int, float)) or abs(price) < 100:
                        anomalies.append({"kind": "invalid_price", "event": eid, "book": bk.get("key"),
                                          "market": m, "price": price})
                        continue
                    if m in ("spreads", "totals") and point is None:
                        anomalies.append({"kind": "missing_point", "event": eid, "book": bk.get("key"), "market": m})
                        continue
                    rows.append(Quote(eid, ev.get("sport_key", ""), ev["commence_time"], home, away, bk.get("key"),
                                      m, side, None if point is None else float(point), int(price),
                                      mk.get("last_update") or bk.get("last_update") or snapshot_ts or fetched_at,
                                      fetched_at, source, request_fp))
                problem = _check_pair(m, rows)
                if problem:
                    anomalies.append({"kind": problem, "event": eid, "book": bk.get("key"), "market": m})
                    continue
                quotes.extend(rows)
    return quotes, anomalies


def _check_pair(market, rows):
    sides = {r.side: r for r in rows}
    if market in ("spreads", "h2h") and not {"home", "away"} <= sides.keys():
        return "incomplete_market"
    if market == "totals" and not {"over", "under"} <= sides.keys():
        return "incomplete_market"
    if market == "spreads" and abs(sides["home"].point + sides["away"].point) > 1e-9:
        return "spread_points_not_opposite"
    if market == "totals" and sides["over"].point != sides["under"].point:
        return "total_points_differ"
    return None
