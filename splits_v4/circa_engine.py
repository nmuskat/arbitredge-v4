"""
splits_v4/circa_engine.py -- the owner's buddy's "99% Circa handle" engine, rebuilt mechanically (owner request
2026-11-18: every sport on the sheet, built like his). Input: one Circa splits sheet image (read by circa_ocr),
uploaded by the owner; games and DraftKings lines from ESPN's scoreboard at upload time (splits_v4.espn).
Rule version circa99.v2, fixed 2026-11-18 (v1 was NFL/CFB on our odds feed; never used on a real upload).

1. MATCH each sheet row to an ESPN game on the sheet's date (header date if within 3 days of the upload, else the
   upload date, US Eastern): the sheet's spread, total and moneyline must sit close to DraftKings' current numbers
   (tolerances per sport) and the closest game must be clearly closer than any other; team names add weight.
   The two team names (often badly read) must also resemble the game's teams (fuzzy similarity of both names
   together >= NAME_MIN), checked both ways round: a sheet that lists the teams in the other order is turned around
   so the money is credited to the right team. Each game can be claimed by one row only (the best fit). Signs and
   decimals the image cannot show reliably come from DraftKings (favourite gets the minus sign; "25" -> 2.5).
   Anything that does not fit is NOT MATCHED, never guessed.
2. SIGNAL: a side with >= 99% of Circa's HANDLE in a market (spread / puck line / run line, total, moneyline).
   NORMAL tier: that side's bets % < 99 (playable). THIN tier: bets also >= 99% (one ticket / tiny market): logged.
3. FILTERS (recorded on every signal):
   line move   DraftKings' opening line -> its current line at upload: AGAINST the heavy side (heavy side cheaper;
               the book agrees with the fade), WITH it (more expensive), NONE (< 0.5 pt, or < 2% win prob on a
               moneyline). Original number available = Circa's number still equals DraftKings' opener.
   key number  fade line on a key number (NFL 3, 7; college football 3, 7, 10, 14).
   bettor read bets 50-67% one big ticket (possibly sharp) / 68-79% mixed / 80%+ public. Bucket <70, 70-89, 90-98, 99+.
   timing      LIVE when the game had started before the upload (logged, not played).
4. LEAN (fixed written rules standing in for Grok's research judgement, which cannot be reproduced):
   LIKE (bet the fade)       normal tier and the line did not move WITH the heavy side;
   FLIP (bet the heavy side) normal tier, line moved WITH it, and bets 50-67% (the big money looks sharp);
   PASS                      otherwise, or LIVE. THIN tier = LOG only.
5. GRADING: fade, follow and lean, 1 unit risked, at Circa's number on the sheet; price = DraftKings' current price
   for that side when its number is the same, else -110 (moneylines at the sheet price).
"""
import datetime as _dt
import re
from zoneinfo import ZoneInfo

from splits_v4 import match as M

RULE = "circa99.v2 (2026-11-18): >=99% Circa handle, every sport; NORMAL bets<99 / THIN bets>=99; LIKE/FLIP/PASS fixed rules"
ET = ZoneInfo("America/New_York")
KEYS = {"nfl": {3, 7}, "cfb": {3, 7, 10, 14}}
TOL = {   # spread, total tolerance (points) per league
    "nfl": (3.0, 3.5), "cfb": (3.5, 4.0), "nba": (3.0, 6.0), "wnba": (3.0, 5.0), "cbb": (3.5, 6.0),
    "nhl": (0.01, 1.0), "mlb": (0.01, 1.5)}
OPP = {"away": "home", "home": "away", "over": "under", "under": "over"}
NAME_MIN = 1.0           # both names together (each 0..1); fixed from the owner's first real uploads (2026-11-18)
SWAP_MARGIN = 0.3        # reversed team order must fit this much better before the sheet's sides are swapped
LEAGUE_NAMES = {"nfl": "NFL", "cfb": "College football", "nba": "NBA", "wnba": "WNBA", "cbb": "College basketball",
                "nhl": "NHL", "mlb": "MLB"}


def sheet_date(label, year):
    m = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+(\d{1,2})", (label or "").upper())
    if not m:
        return None
    mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].index(m.group(1)) + 1
    try:
        return _dt.date(year, mon, int(m.group(2)))
    except ValueError:
        return None


def game_day(label, uploaded_at):
    up = _dt.datetime.fromtimestamp(uploaded_at, ET).date()
    d = sheet_date(label, up.year)
    return d if d is not None and abs((d - up).days) <= 3 else up


def _variants(v):
    if v is None:
        return []
    a = abs(v)
    out = {a}
    if a >= 10 and a == int(a):
        out.add(a / 10)                     # a lost decimal point: 25 -> 2.5
    return sorted(out)


def _sname(league):
    return "nfl" if league == "nfl" else "cfb"          # name_score: NFL abbreviations, else school/city rules


def _score(g, e):
    """Lower = better fit; None = does not fit."""
    o = e.get("odds")
    if not o:
        return None
    ts, tt = TOL[e["league"]]
    sp = ((g.get("spread") or {}).get("away") or {}).get("line")
    tot = ((g.get("total") or {}).get("over") or {}).get("line")
    s = 0.0
    if o.get("spread_home_close") is not None and sp is not None:
        ds = min(abs(v - abs(o["spread_home_close"])) for v in _variants(sp))
        if ds > ts:
            return None
        s += ds
    if o.get("total_close") is not None and tot is not None:
        dt = min(abs(v - o["total_close"]) for v in _variants(tot))
        if dt > tt:
            return None
        s += dt
    ma, mh = ((g.get("money") or {}).get("away") or {}).get("line"), ((g.get("money") or {}).get("home") or {}).get("line")
    if ma is not None and mh is not None and o.get("ml_away_close") is not None and o.get("ml_home_close") is not None:
        dm = min(abs(abs(ma) - abs(o["ml_away_close"])), abs(abs(mh) - abs(o["ml_home_close"])))
        s += min(dm, 400) / 60.0
    straight = M.fuzzy(g.get("away"), e["away"]) + M.fuzzy(g.get("home"), e["home"])
    swapped = M.fuzzy(g.get("away"), e["home"]) + M.fuzzy(g.get("home"), e["away"])
    names = max(straight, swapped)
    if names < NAME_MIN:
        return None
    return s - 1.5 * names, swapped > straight + SWAP_MARGIN


def swap_sides(g):
    """The sheet listed the teams in the other order: swap away/home on spread and moneyline (totals unaffected)."""
    out = dict(g, away=g.get("home"), home=g.get("away"), swapped=True)
    for mk in ("spread", "money"):
        m = g.get(mk) or {}
        out[mk] = {"away": m.get("home"), "home": m.get("away")}
    return out


def match_game(g, events, uploaded_at):
    """(event, row as oriented to the event) or (None, reason)."""
    day = game_day(g.get("sport_label"), uploaded_at)
    cands = []
    for e in events:
        if _dt.datetime.fromtimestamp(e["kickoff"], ET).date() != day:
            continue
        r = _score(g, e)
        if r is not None:
            cands.append((r[0], r[1], e))
    cands.sort(key=lambda c: c[0])
    if not cands:
        return None, "no game that day fits this row's numbers and team names"
    if len(cands) > 1 and cands[1][0] - cands[0][0] < 1.0:
        return None, "more than one game fits; not guessed"
    return cands[0][2], (swap_sides(g) if cands[0][1] else g)


def match_all(sheet, events, uploaded_at, others=None):
    """One game per row and one row per game: when several rows claim the same game, only the clearly best keeps it.
    others: rows of other screenshots uploaded together (they compete for games but are not returned)."""
    res, claims = [], {}
    for i, g in enumerate(list(sheet) + list(others or [])):
        e, x = match_game(g, events, uploaded_at)
        if e is None:
            res.append((g, None, x))
            continue
        sc = _score(x, e)[0]
        res.append((x, e, sc))
        claims.setdefault(e["id"], []).append((sc, i))
    for gid, cl in claims.items():
        if len(cl) < 2:
            continue
        cl.sort()
        keep = cl[0][1] if cl[1][0] - cl[0][0] >= 0.5 else None
        for _sc, i in cl:
            if i != keep:
                res[i] = (res[i][0], None, "another row fits this game better; not guessed")
    return [(g, e, (None if e else why)) for g, e, why in res[:len(sheet)]]


def resolve(g, e):
    """{market: {side: {line, handle, bets}}} with signs/decimals from DraftKings; markets that do not fit dropped."""
    o, out = e["odds"], {}
    ts, tt = TOL[e["league"]]
    sp = g.get("spread") or {}
    hc = o.get("spread_home_close")
    mag = min(_variants((sp.get("away") or {}).get("line")), key=lambda v: abs(v - abs(hc)), default=None) if hc is not None else None
    if mag is not None and abs(mag - abs(hc)) <= ts and hc != 0:
        home = -mag if hc < 0 else mag
        out["spread"] = {"home": dict(sp.get("home") or {}, line=home), "away": dict(sp.get("away") or {}, line=-home)}
    to, tc = g.get("total") or {}, o.get("total_close")
    tmag = min(_variants((to.get("over") or {}).get("line")), key=lambda v: abs(v - tc), default=None) if tc is not None else None
    if tmag is not None and abs(tmag - tc) <= tt:
        out["total"] = {"over": dict(to.get("over") or {}, line=tmag), "under": dict(to.get("under") or {}, line=tmag)}
    mo = g.get("money") or {}
    a, h = (mo.get("away") or {}).get("line"), (mo.get("home") or {}).get("line")
    ah, aa = o.get("ml_home_close"), o.get("ml_away_close")
    if None not in (a, h, ah, aa) and abs(a) >= 100 and abs(h) >= 100:
        # each side takes DraftKings' sign for that side (both negative near pick'em is possible)
        out["money"] = {"home": dict(mo.get("home") or {}, line=abs(h) if ah > 0 else -abs(h)),
                        "away": dict(mo.get("away") or {}, line=abs(a) if aa > 0 else -abs(a))}
    return out


def _prob(ml):
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def movement(market, heavy, o):
    """DraftKings opener -> current, from the heavy side's point of view."""
    if market == "spread":
        a, b = o.get(f"spread_{heavy}_open"), o.get(f"spread_{heavy}_close")
        if a is None or b is None:
            return "UNKNOWN"
        d = b - a                                   # heavy side's number went up = cheaper for its bettors
    elif market == "total":
        a, b = o.get("total_open"), o.get("total_close")
        if a is None or b is None:
            return "UNKNOWN"
        d = (a - b) if heavy == "over" else (b - a)
    else:
        a, b = o.get(f"ml_{heavy}_open"), o.get(f"ml_{heavy}_close")
        if a is None or b is None or abs(a) < 100 or abs(b) < 100:
            return "UNKNOWN"
        d = (_prob(a) - _prob(b)) * 25              # 2% win probability ~ 0.5 "points"
    return "AGAINST" if d >= 0.5 else "WITH" if d <= -0.5 else "NONE"


def _dk_price(market, side, line, o):
    if market == "money":
        return None
    if market == "spread":
        return o.get(f"spread_{side}_close_price") if o.get(f"spread_{side}_close") == line else None
    return o.get(f"total_{side}_close_price") if o.get("total_close") == line else None


def bucket(b):
    return "<70" if b < 70 else "70-89" if b < 90 else "90-98" if b < 99 else "99+"


def bettor_read(b):
    return "one big ticket (possibly sharp)" if 50 <= b <= 67 else "public piling in" if b >= 80 else "mixed"


def signals(sheet, events, uploaded_at, others=None):
    out = []
    for g, e, why in match_all(sheet, events, uploaded_at, others):
        res = resolve(g, e) if e else {}
        for market, sides in (("spread", ("away", "home")), ("total", ("over", "under")), ("money", ("away", "home"))):
            src = res.get(market) if e else (g.get(market) or {})
            for heavy in sides:
                s = (src or {}).get(heavy) or {}
                if (s.get("handle") or 0) < 99:
                    continue
                fade = OPP[heavy]
                rec = {"rule": RULE, "sheet_game": f"{g.get('away')} @ {g.get('home')}", "sheet_label": g.get("sport_label"),
                       "sheet_order_swapped": bool(g.get("swapped")),
                       "market": market, "heavy": heavy, "fade": fade, "handle": s.get("handle"), "bets": s.get("bets"),
                       "uploaded_at": uploaded_at}
                if not e:
                    rec.update(status="NOT MATCHED", reason=why)
                    out.append(rec)
                    continue
                if market not in res:
                    rec.update(status="NOT MATCHED", reason=f"{market} numbers did not fit DraftKings'", event_id=e["id"])
                    out.append(rec)
                    continue
                o, bets = e["odds"], s.get("bets")
                tier = "THIN" if bets is not None and bets >= 99 else "NORMAL"
                lh, lf = res[market][heavy]["line"], res[market][fade]["line"]
                mv = movement(market, heavy, o)
                live = uploaded_at >= e["kickoff"]
                if tier == "THIN":
                    lean = "LOG"
                elif live:
                    lean = "PASS"
                elif mv != "WITH":
                    lean = "LIKE"
                elif bets is not None and 50 <= bets <= 67:
                    lean = "FLIP"
                else:
                    lean = "PASS"
                orig = None
                if market == "spread" and o.get(f"spread_{heavy}_open") is not None:
                    orig = lh == o.get(f"spread_{heavy}_open")
                elif market == "total" and o.get("total_open") is not None:
                    orig = lh == o["total_open"]
                rec.update(status="SIGNAL", league=e["league"], sport=LEAGUE_NAMES[e["league"]], event_id=e["id"],
                           game=f"{e['away']} @ {e['home']}", kickoff=e["kickoff"], tier=tier, lean=lean, live=live,
                           movement=mv, original_number=orig, bucket=bucket(bets) if bets is not None else None,
                           bettor_read=bettor_read(bets) if bets is not None else None, line_heavy=lh, line_fade=lf,
                           key_number=(market == "spread" and abs(lf) in KEYS.get(e["league"], set())),
                           price_heavy=lh if market == "money" else (_dk_price(market, heavy, lh, o) or -110),
                           price_fade=lf if market == "money" else (_dk_price(market, fade, lf, o) or -110),
                           dk={k: o.get(k) for k in ("spread_home_open", "spread_home_close", "total_open", "total_close",
                                                    "ml_home_open", "ml_home_close", "ml_away_open", "ml_away_close")},
                           game_date=_dt.datetime.fromtimestamp(e["kickoff"], ET).strftime("%Y%m%d"),
                           reason=_reason(tier, lean, mv, bets, live))
                out.append(rec)
    return out


def _reason(tier, lean, mv, bets, live):
    if tier == "THIN":
        return "bets also 99%+: likely a single ticket or tiny market (logged only)"
    if live:
        return "game had already started when the sheet was uploaded"
    if lean == "LIKE":
        return {"AGAINST": "line moved against the heavy money (book agrees with the fade)",
                "NONE": "line held despite 99%+ of the money", "UNKNOWN": "no line history; default fade"}[mv]
    if lean == "FLIP":
        return f"line moved with the money and {bets:g}% of bets looks like one big (possibly sharp) ticket"
    return f"line moved with the money and {bets:g}% of bets looks public"


def grade_side(market, side, line, price, final):
    """final = (home_score, away_score)."""
    if final is None or line is None:
        return None
    h, a = final
    if market == "spread":
        m = ((h - a) if side == "home" else (a - h)) + line
    elif market == "total":
        m = (h + a - line) * (1 if side == "over" else -1)
    else:
        m = (h - a) if side == "home" else (a - h)
    res = "W" if m > 0 else "L" if m < 0 else "P"
    p = float(price)
    return {"result": res, "units": round((p / 100 if p > 0 else 100 / -p) if res == "W" else -1.0 if res == "L" else 0.0, 3)}


def grade(rec, final):
    if rec.get("status") != "SIGNAL":
        return None
    fade = grade_side(rec["market"], rec["fade"], rec["line_fade"], rec["price_fade"], final)
    follow = grade_side(rec["market"], rec["heavy"], rec["line_heavy"], rec["price_heavy"], final)
    return {"fade": fade, "follow": follow,
            "lean": fade if rec["lean"] == "LIKE" else follow if rec["lean"] == "FLIP" else None}
