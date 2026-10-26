"""
sharp_v4/signals.py -- price-based sharp-action evidence for one game/market.

All series are point-in-time: analyse(conn, event_id, market, T) sees only
rows fetched at or before T (book timestamps also <= T).

Movement is measured in POINT-EQUIVALENTS of the home side (spreads) or the
over (totals): line + price expressed together,
    pe = -home_line + (fair_cover - 0.5) / PROB_PER_POINT
(PROB_PER_POINT ~ 0.03 near an NFL spread: an approximation, labelled).
Positive pe movement = market moving toward HOME (or OVER).

Book classes are an UNVALIDATED ASSUMPTION (market-making / low-margin
books vs recreational books) to be tested against CLV once history exists.

Signals that need betting splits (reverse line movement, ticket vs money)
or injury feeds are reported as UNAVAILABLE -- never estimated.
"""

from collections import defaultdict

from edge_v4 import pricing

SHARP_BOOKS = {"pinnacle", "circasports", "betonlineag", "lowvig", "bookmaker"}
REC_BOOKS = {"draftkings", "fanduel", "betmgm", "williamhill_us", "caesars", "betrivers", "pointsbetus",
             "espnbet", "fanatics", "hardrockbet", "ballybet", "betparx", "unibet_us", "superbook", "wynnbet"}
PROB_PER_POINT = 0.03
KEY_NUMBERS = {"spreads": (3.0, 7.0, 10.0, 6.0, 14.0), "totals": (41.0, 44.0, 37.0, 47.0, 51.0)}
REAL_SOURCES = ("odds_api", "v3_copy")


class SyntheticSourceRejected(ValueError):
    pass


DEMO_SOURCES = ("demo_sim",)


def _series(conn, event_id, market, T, allow_demo=False):
    """book -> [(fetched_at, pe, line, fair_a)] in time order (point-in-time)."""
    a, b = ("home", "away") if market in ("spreads", "h2h") else ("over", "under")
    rows = conn.execute(
        "SELECT book, side, point, price, fetched_at, source FROM mkt_quote "
        "WHERE event_id=? AND market=? AND fetched_at<=? AND market_last_update<=? ORDER BY fetched_at",
        (event_id, market, T, T)).fetchall()
    snap = defaultdict(dict)
    for book, side, point, price, fa, source in rows:
        if not (source.startswith(REAL_SOURCES) or (allow_demo and source.startswith(DEMO_SOURCES))):
            raise SyntheticSourceRejected(f"source {source!r} is not a real market feed")
        snap[(book, fa)][side] = (price, point)
    out = defaultdict(list)
    for (book, fa), s in sorted(snap.items(), key=lambda kv: kv[0][1]):
        if a not in s or b not in s:
            continue
        try:
            fair = pricing.devig([s[a][0], s[b][0]])[0]
        except ValueError:
            continue
        line = s[a][1]
        if market == "spreads":
            pe = -line + (fair - 0.5) / PROB_PER_POINT
        elif market == "totals":
            pe = line + (fair - 0.5) / PROB_PER_POINT
        else:   # h2h: express in probability points x 100
            pe = fair * 100
        out[book].append((fa, pe, line, fair))
    return out


def _class(book):
    return "sharp" if book in SHARP_BOOKS else ("rec" if book in REC_BOOKS else "other")


def _consensus(series, books, t):
    vals = []
    for bk in books:
        pts = [p for p in series.get(bk, []) if p[0] <= t]
        if pts:
            vals.append(pts[-1][1])
    return sum(vals) / len(vals) if vals else None


def analyse(conn, event_id, market, T, min_books=3, min_move=0.5, steam_window_s=900, steam_books=3,
            allow_demo=False):
    """allow_demo: accept rows explicitly tagged 'demo_sim' (simulated quotes for UI demonstration).
    The result is then labelled DEMO. The synthetic sportsbook's own data is never accepted."""
    s = _series(conn, event_id, market, T, allow_demo)
    side_pos, side_neg = ("home", "away") if market in ("spreads", "h2h") else ("over", "under")
    res = {"event_id": event_id, "market": market, "as_of": T, "status": "INSUFFICIENT EVIDENCE",
           "suspected_side": None, "score": None,
           "label": ("DEMO DATA - " if allow_demo else "") + "EXPERIMENTAL heuristic (uncalibrated)",
           "supporting": [], "contradicting": [], "books": {}, "detected_at": None,
           "unavailable": ["reverse line movement (needs ticket/handle splits: no licensed source)",
                           "ticket vs money divergence (no licensed source)",
                           "injury/lineup news (no feed integrated)"],
           "historical_effectiveness": "not yet measured: needs multi-book quote history with closes"}
    books = sorted(s)
    res["books"] = {b: {"class": _class(b), "observations": len(s[b]),
                        "open_pe": round(s[b][0][1], 2), "now_pe": round(s[b][-1][1], 2)} for b in books}
    if len(books) < min_books:
        res["reason"] = f"only {len(books)} book(s) observed (< {min_books})"
        return res
    times = sorted({p[0] for b in books for p in s[b]})
    if len(times) < 2:
        res["reason"] = "single snapshot: no movement history"
        return res
    sharp = [b for b in books if _class(b) == "sharp"]
    rec = [b for b in books if _class(b) == "rec"]
    t0, tN = times[0], times[-1]
    open_c, now_c = _consensus(s, books, t0), _consensus(s, books, tN)
    move = now_c - open_c
    ev = []   # (direction +1/-1, weight, text, time)

    # 1. consensus movement since first observation
    if abs(move) >= min_move:
        ev.append((1 if move > 0 else -1, 1.0, f"consensus moved {move:+.2f} pts since first observation", tN))

    # 2. first mover: for each book, the first time it moved >= min_move from its open
    first = {}
    for b in books:
        for fa, pe, *_ in s[b]:
            d = pe - s[b][0][1]
            if abs(d) >= min_move:
                first[b] = (fa, 1 if d > 0 else -1)
                break
    if first:
        lead_t = min(v[0] for v in first.values())
        leaders = [b for b, v in first.items() if v[0] == lead_t]
        dirs = {first[b][1] for b in leaders}
        if len(dirs) == 1:
            d = dirs.pop()
            followers = [b for b, v in first.items() if v[0] > lead_t and v[1] == d]
            lead_sharp = [b for b in leaders if _class(b) == "sharp"]
            if lead_sharp and followers:
                ev.append((d, 2.0, f"market-making book(s) {', '.join(lead_sharp)} moved first; "
                                   f"{len(followers)} book(s) followed", lead_t))
            elif followers and all(_class(b) == "rec" for b in leaders):
                ev.append((d, 0.5, f"recreational book(s) {', '.join(leaders)} moved first "
                                   "(weaker: may reflect public flow or risk management)", lead_t))
        res["detected_at"] = lead_t

    # 3. steam: >= steam_books books move >= min_move the same way within a window
    for t in times:
        movers = defaultdict(set)
        for b in books:
            before = [p for p in s[b] if p[0] <= t - steam_window_s] or s[b][:1]
            after = [p for p in s[b] if t - steam_window_s < p[0] <= t]
            if before and after:
                d = after[-1][1] - before[-1][1]
                if abs(d) >= min_move:
                    movers[1 if d > 0 else -1].add(b)
        for d, bs in movers.items():
            if len(bs) >= steam_books:
                ev.append((d, 1.5, f"steam: {len(bs)} books moved the same way within "
                                   f"{steam_window_s // 60} min", t))
                break
        else:
            continue
        break

    # 4. key-number crossing of the consensus line
    if market in KEY_NUMBERS:
        def mode_line(t):
            ls = [[p for p in s[b] if p[0] <= t][-1][2] for b in books if any(p[0] <= t for p in s[b])]
            return max(set(ls), key=ls.count)
        l0, l1 = mode_line(t0), mode_line(tN)
        crossed = [k for k in KEY_NUMBERS[market] if min(abs(l0), abs(l1)) < k < max(abs(l0), abs(l1))]
        if crossed and abs(move) >= min_move:
            ev.append((1 if move > 0 else -1, 1.5, f"line crossed key number(s) {crossed} ({l0:+g} -> {l1:+g})", tN))

    # 5. buyback: the consensus peaked and gave back >= 50% of the move
    path = [_consensus(s, books, t) for t in times]
    peak = max(path, key=lambda v: abs(v - open_c))
    if abs(peak - open_c) >= min_move and abs(now_c - open_c) <= 0.5 * abs(peak - open_c):
        res["contradicting"].append(f"buyback: consensus moved {peak - open_c:+.2f} then gave back to {move:+.2f}")
        ev.append((-1 if peak - open_c > 0 else 1, 1.0, "buyback against the earlier move", tN))

    # 6. sharp vs recreational disagreement now
    if sharp and rec:
        sc, rc = _consensus(s, sharp, tN), _consensus(s, rec, tN)
        gap = sc - rc
        if abs(gap) >= min_move:
            ev.append((1 if gap > 0 else -1, 1.0, f"market-making books price {side_pos if gap > 0 else side_neg} "
                                                  f"{abs(gap):.2f} pts stronger than recreational books", tN))
    elif not sharp:
        res["contradicting"].append("no market-making book observed: first-mover and disagreement tests weak")

    # ---- aggregate
    net = sum(d * w for d, w, *_ in ev)
    gross = sum(w for _, w, *_ in ev)
    if gross < 1.5 or abs(net) < 1.0:
        res["reason"] = "evidence too weak or balanced"
        res["supporting"] = [e[2] for e in ev]
        return res
    d = 1 if net > 0 else -1
    res["suspected_side"] = side_pos if d > 0 else side_neg
    res["supporting"] = [e[2] for e in ev if e[0] == d]
    res["contradicting"] += [e[2] for e in ev if e[0] != d and not e[2].startswith("buyback")]
    agree = sum(w for dd, w, *_ in ev if dd == d)
    res["score"] = int(round(100 * agree / (gross + 2.0)))      # shrunk: never 100 from few signals
    res["status"] = "SIGNAL"
    res["detected_at"] = min(e[3] for e in ev if e[0] == d)
    return res


def effectiveness(records, bins=(0, 25, 50, 75, 101)):
    """Calibration harness. records: [{'score', 'side_beat_close': bool|None, 'side_covered': bool|None}].
    Returns hit rates by score bin; None outcomes (close not observed) are excluded and counted."""
    out = []
    for lo, hi in zip(bins, bins[1:]):
        r = [x for x in records if x.get("score") is not None and lo <= x["score"] < hi]
        clv = [x["side_beat_close"] for x in r if x.get("side_beat_close") is not None]
        cov = [x["side_covered"] for x in r if x.get("side_covered") is not None]
        out.append({"score_bin": f"{lo}-{hi - 1}", "n": len(r),
                    "clv_n": len(clv), "clv_hit_rate": (sum(clv) / len(clv)) if clv else None,
                    "cover_n": len(cov), "cover_rate": (sum(cov) / len(cov)) if cov else None,
                    "unobserved_close": len(r) - len(clv)})
    return out
