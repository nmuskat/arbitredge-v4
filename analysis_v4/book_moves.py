"""
analysis_v4/book_moves.py -- PINNACLE & CIRCA line-movement tracker with an INFERRED book need (owner request
2026-11-12). Real collected prices only; nothing simulated.

For every game, each tracked book's spread and total: the first price we observed, the current (or closing) price,
and every change in between.

INFERRED NEED (rule fixed 2026-11-12 before any result was computed; owner's description, from a Circa manager:
"balance until we know the right price, then book hard to the side we want to need"):
  * A book that makes one side MORE EXPENSIVE -- a worse number (Bears +3 -> +2.5) or more juice at the same number
    (Bears +3 -110 -> +3 -115) -- is "showing that side as strong" and inviting money on the other, cheaper side.
    The side made more expensive is the book's INFERRED NEED.
  * Measured with one number per quote, the point-equivalent of the home side (spreads) or over (totals), from the
    book's OWN two prices (no-vig): pe = -home_line + (fair_home - 0.5)/0.03; totals pe = total + (fair_over - 0.5)/0.03.
    A rise = home/over more expensive. 0.03 probability per point is the same labelled approximation Engine B uses.
  * move = pe(latest) - pe(first observed). |move| < 0.25 point-equivalents -> NO CLEAR MOVE (no need shown).
    (-110/-110 -> -115/-105 at the same number is ~0.39; a 2-cent juice change is ~0.16.)
  * Strength: LINE when the number itself moved >= 0.5 toward the need side, else JUICE.
  * "First observed" is our first collection, not the book's true opening line.
Graded W/L/P against the final score at the book's own last pre-kickoff number. A heuristic read of public prices:
the books' real liabilities are private. Not a bet; default NO BET.
"""
import time

from edge_v4 import pricing

BOOKS = {"pinnacle": "Pinnacle", "circasports": "Circa"}
MARKETS = {"spreads": ("home", "away"), "totals": ("over", "under")}
PROB_PER_POINT = 0.03
MIN_MOVE = 0.25
LINE_STEP = 0.5
RULE = "book_moves.v1 (2026-11-12): side made more expensive since first observation, |pe move| >= 0.25"


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _pe(market, line_a, pa, pb):
    try:
        fair = pricing.devig([pa, pb])[0]
    except ValueError:
        return None
    return (-line_a if market == "spreads" else line_a) + (fair - 0.5) / PROB_PER_POINT


def series(conn, event_id, book, market, until):
    """[(fetched_at, line_a, price_a, price_b, pe)] at each CHANGE of the book's two-way price, before `until`."""
    a, b = MARKETS[market]
    snap = {}
    for side, point, price, fa in conn.execute(
            "SELECT side, point, price, fetched_at FROM mkt_quote WHERE event_id=? AND book=? AND market=? "
            "AND fetched_at<? ORDER BY fetched_at", (event_id, book, market, until)):
        snap.setdefault(fa, {})[side] = (point, price)
    out = []
    for fa in sorted(snap):
        s = snap[fa]
        if a not in s or b not in s or s[a][0] is None:
            continue
        cur = (s[a][0], s[a][1], s[b][1])
        if out and out[-1][1:4] == cur:
            continue
        pe = _pe(market, *cur)
        if pe is not None:
            out.append((fa, *cur, pe))
    return out


def infer(market, ser):
    """{'need', 'strength', 'move', ...} from a price series (rule above)."""
    a, b = MARKETS[market]
    if len(ser) < 1:
        return {"need": None, "status": "NO PRICES YET"}
    first, last = ser[0], ser[-1]
    move = last[4] - first[4]
    out = {"move": round(move, 2), "changes": len(ser) - 1}
    if abs(move) < MIN_MOVE:
        return dict(out, need=None, status="NO CLEAR MOVE")
    need = a if move > 0 else b
    dline = (-(last[1] - first[1]) if market == "spreads" else (last[1] - first[1])) * (1 if need == a else -1)
    return dict(out, need=need, status="INFERRED NEED", strength="LINE" if dline >= LINE_STEP else "JUICE")


def grade(market, need, close_line_a, final):
    """W/L/P of the need side at the book's closing number; final = (home_score, away_score)."""
    if need is None or final is None or close_line_a is None:
        return None
    h, aw = final
    if market == "spreads":
        m = (h - aw) + close_line_a                       # home covers when > 0 (close_line_a = home spread)
        r = 0 if m == 0 else (1 if m > 0 else -1)
        if need == "away":
            r = -r
    else:
        m = (h + aw) - close_line_a
        r = 0 if m == 0 else (1 if m > 0 else -1)
        if need == "under":
            r = -r
    return {1: "W", -1: "L", 0: "P"}[r]


def build(conn, games, finals, now, list_past_s=21 * 86400):
    """games: [{'sport','event_id','home','away','kickoff' (epoch)}]; finals: {event_id: (home, away)}.
    Returns per-game rows (upcoming and completed) and season records per book/market/sport."""
    rows = []
    rec = {}
    for g in games:
        ko = g["kickoff"]
        until = min(now, ko)
        row = {"sport": g["sport"], "event_id": g["event_id"], "game": f"{g['away']} @ {g['home']}",
               "kickoff": _iso(ko), "status": "upcoming" if ko > now else "final" if g["event_id"] in finals else "started",
               "final": list(finals[g["event_id"]]) if g["event_id"] in finals else None, "books": {}}
        for book in BOOKS:
            for market in MARKETS:
                ser = series(conn, g["event_id"], book, market, until)
                inf = infer(market, ser)
                cell = {"inference": inf}
                if ser:
                    f, l = ser[0], ser[-1]
                    cell.update(first={"at": _iso(f[0]), "line": f[1], "a": f[2], "b": f[3]},
                                last={"at": _iso(l[0]), "line": l[1], "a": l[2], "b": l[3]},
                                moves=[{"at": _iso(x[0]), "line": x[1], "a": x[2], "b": x[3]} for x in ser[-15:]]
                                if ko > now else [])     # full move list for upcoming games; first/last for past ones
                    if ko <= now:
                        res = grade(market, inf.get("need"), l[1], row["final"] and tuple(row["final"]))
                        cell["result"] = res
                        if res:
                            k = (book, market, g["sport"], inf["strength"])
                            for kk in (k, (book, market, g["sport"], "ALL")):
                                t = rec.setdefault(kk, {"W": 0, "L": 0, "P": 0})
                                t[res] += 1
                row["books"].setdefault(book, {})[market] = cell
        if ko > now - list_past_s and any(c["inference"].get("status") != "NO PRICES YET"
                                          for b in row["books"].values() for c in b.values()):
            rows.append(row)
    # records count every collected game; the page lists games from the last 3 weeks
    records = [{"book": b, "market": m, "sport": s, "strength": st, **t} for (b, m, s, st), t in sorted(rec.items())]
    return {"status": "OK", "rule": RULE, "books": BOOKS, "games": rows, "records": records,
            "label": "REAL collected prices; the need side is an INFERENCE from price moves, not the book's liability",
            "note": "First price = our first collection, not the true opening line. Not a bet; default NO BET."}
