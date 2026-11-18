"""
splits_v4/circa_engine.py -- the owner's buddy's "99% Circa handle" engine, rebuilt mechanically (owner request
2026-11-18). Input: one Circa splits sheet image (read by circa_ocr), uploaded by the owner. NFL and college football
only (the only sports we have final scores and market lines for). Rule version circa99.v1, fixed 2026-11-18.

1. MATCH each sheet row to a game in our odds feed: same sport, kickoff on the sheet's date (US Eastern), and the
   sheet's spread/total within 3 points of our market at upload time (the closest unique game). Signs and decimals
   the image cannot show reliably are taken from the market (e.g. "25" -> 2.5, favourite gets the minus sign); a
   reading that does not fit the market is rejected, never guessed.
2. SIGNAL: a side with >= 99% of Circa's HANDLE in a market (spread, total, moneyline).
   Tier NORMAL: that side's bets % < 99 (playable). Tier THIN: bets also >= 99% (one ticket / tiny market):
   logged, never played.
3. FILTERS (recorded on every signal):
   line move   our market line at upload vs the first line we saw: AGAINST the heavy side (heavy side got cheaper;
               the book agrees with the fade), WITH it (heavy side got more expensive), or NONE (< 0.5 pt / 10 cents).
   key number  whether the fade line sits on a key number (3, 7, and 10/14 for college).
   bettor read bets % bucket: 50-67 one big ticket (possibly sharp), 68-79 mixed, 80+ public piling in.
   timing      LIVE if the game had already started when the sheet was uploaded (logged, not played).
4. LEAN (fixed written rules standing in for Grok's judgement, which cannot be reproduced):
   LIKE  (bet the fade)  normal tier and the line did not move WITH the heavy side;
   FLIP  (bet the heavy side) normal tier, line moved WITH the heavy side, and bets 50-67% (big money looks sharp);
   PASS  otherwise (line moved with a public-looking heavy side, or the game was live). THIN = LOG only.
5. GRADING: fade, follow and lean results at the sheet's Circa line (spreads/totals at -110, moneylines at the
   sheet price), 1 unit risked.
"""
import datetime as _dt
import re
import time
from zoneinfo import ZoneInfo

from market_v4 import asof
from splits_v4 import match as M

RULE = "circa99.v1 (2026-11-18): >=99% Circa handle; NORMAL bets<99 / THIN bets>=99; LIKE/FLIP/PASS fixed rules"
ET = ZoneInfo("America/New_York")
SPORT_KEY = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}
KEYS = {"nfl": {3, 7}, "cfb": {3, 7, 10, 14}}
MATCH_TOL = 3.0
OPP = {"away": "home", "home": "away", "over": "under", "under": "over"}


def sheet_date(label, year):
    m = re.search(r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\w*\s+(\d{1,2})", label.upper())
    if not m:
        return None
    mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].index(m.group(1)) + 1
    try:
        return _dt.date(year, mon, int(m.group(2)))
    except ValueError:
        return None


def _variants(v):
    """The magnitudes a misread line could really be: as read, and with a lost decimal point (25 -> 2.5)."""
    if v is None:
        return []
    a = abs(v)
    out = {a}
    if a >= 10 and a == int(a):
        out.add(a / 10)
    return sorted(out)


def _market(conn, eid, at):
    sp = asof.market_state(conn, eid, "spreads", at, max_age_s=12 * 3600)
    to = asof.market_state(conn, eid, "totals", at, max_age_s=12 * 3600)
    ml = asof.market_state(conn, eid, "h2h", at, max_age_s=12 * 3600)
    return {"spread_home": sp.get("line") if sp.get("status") == "OK" else None,
            "total": to.get("line") if to.get("status") == "OK" else None,
            "ml_home_fav": (ml.get("fair") or {}).get("home", 0.5) > 0.5 if ml.get("status") == "OK" else None,
            "ml_both_neg": None}


def match_game(g, conn, at, year):
    """(event, None) or (None, reason). The sport and date in the header are often misread, so every row is tried
    against NFL and college football games on the header date (if it is within 3 days of the upload) or else on the
    upload date. Other sports never fit: their spreads/totals do not match any football game that day."""
    up_day = _dt.datetime.fromtimestamp(at, ET).date()
    day = sheet_date(g.get("sport_label", ""), year)
    if day is None or abs((day - up_day).days) > 3:
        day = up_day
    sp = (g.get("spread") or {}).get("away", {}).get("line")
    tot = (g.get("total") or {}).get("over", {}).get("line")
    cands = []
    for sport, key in SPORT_KEY.items():
      for eid, (k, home, away) in asof.kickoffs(conn, sport=key).items():
        if _dt.datetime.fromtimestamp(k, ET).date() != day:
            continue
        mk = _market(conn, eid, min(at, k - 1e-6))
        if mk["spread_home"] is None or mk["total"] is None:
            continue
        ds = min((abs(v - abs(mk["spread_home"])) for v in _variants(sp)), default=99)
        dt = min((abs(v - mk["total"]) for v in _variants(tot)), default=99)
        if ds <= MATCH_TOL and dt <= MATCH_TOL:
            names = M.name_score(g.get("away", ""), away, sport) + M.name_score(g.get("home", ""), home, sport)
            cands.append((ds + dt - 0.5 * names, eid, k, home, away, mk, sport))
    cands.sort()
    if not cands:
        return None, "not an NFL or college football game we track (no game that day with this spread and total)"
    if len(cands) > 1 and cands[1][0] - cands[0][0] < 1.0:
        return None, "more than one game fits; not guessed"
    _s, eid, k, home, away, mk, sport = cands[0]
    return {"event_id": eid, "kickoff": k, "home": home, "away": away, "market": mk, "sport": sport}, None


def resolve(g, ev):
    """Signs and decimals from the market. Returns {market: {side: {line, handle, bets}}} with unusable markets dropped."""
    mk = ev["market"]
    out = {}
    # spread: favourite (market home line < 0 -> home) gets the minus sign; magnitude = variant closest to the market
    sp = g.get("spread") or {}
    mag = min(_variants((sp.get("away") or {}).get("line")), key=lambda v: abs(v - abs(mk["spread_home"])), default=None)
    if mag is not None and abs(mag - abs(mk["spread_home"])) <= MATCH_TOL and mk["spread_home"] != 0:
        home_line = -mag if mk["spread_home"] < 0 else mag
        out["spread"] = {"home": dict(sp.get("home") or {}, line=home_line), "away": dict(sp.get("away") or {}, line=-home_line)}
    to = g.get("total") or {}
    tmag = min(_variants((to.get("over") or {}).get("line")), key=lambda v: abs(v - mk["total"]), default=None)
    if tmag is not None and abs(tmag - mk["total"]) <= MATCH_TOL:
        out["total"] = {"over": dict(to.get("over") or {}, line=tmag), "under": dict(to.get("under") or {}, line=tmag)}
    mo = g.get("money") or {}
    a, h = (mo.get("away") or {}).get("line"), (mo.get("home") or {}).get("line")
    if a is not None and h is not None and mk["ml_home_fav"] is not None and abs(a) >= 100 and abs(h) >= 100:
        fav, dog = ("home", "away") if mk["ml_home_fav"] else ("away", "home")
        vals = {"away": abs(a), "home": abs(h)}
        if vals[fav] >= vals[dog] or vals[fav] <= 120:          # a favourite's price is the larger number (or near pick'em)
            out["money"] = {fav: dict(mo.get(fav) or {}, line=-vals[fav]),
                            dog: dict(mo.get(dog) or {}, line=vals[dog] if vals[dog] > vals[fav] or vals[dog] > 120 else -vals[dog])}
    return out


def first_line(conn, eid, market, side):
    """Our first observed consensus line for that side (spreads: that side's number; totals: the total)."""
    row = conn.execute("SELECT MIN(fetched_at) FROM mkt_quote WHERE event_id=? AND market=?",
                       (eid, "spreads" if market == "spread" else "totals")).fetchone()[0]
    if row is None:
        return None
    st = asof.market_state(conn, eid, "spreads" if market == "spread" else "totals", row + 1, max_age_s=24 * 3600)
    if st.get("status") != "OK":
        return None
    if market == "spread":
        return st["line"] if side == "home" else -st["line"]
    return st["line"]


def movement(market, heavy, first, now):
    """AGAINST (heavy side got cheaper), WITH (heavy side got more expensive), NONE."""
    if first is None or now is None:
        return "UNKNOWN"
    if market == "spread":
        d = now - first                      # heavy side's own number: higher = better for heavy bettors = cheaper
    else:
        d = (first - now) if heavy == "over" else (now - first)   # over cheaper when the total drops
    return "AGAINST" if d >= 0.5 else "WITH" if d <= -0.5 else "NONE"


def bucket(bets):
    return "<70" if bets < 70 else "70-89" if bets < 90 else "90-98" if bets < 99 else "99+"


def bettor_read(bets):
    return "one big ticket (possibly sharp)" if 50 <= bets <= 67 else "public piling in" if bets >= 80 else "mixed"


def signals(sheet, conn, uploaded_at, year=None):
    """One record per >=99% handle side found on the sheet (all sports listed; only NFL/CFB matched and graded)."""
    year = year or time.gmtime(uploaded_at).tm_year
    out = []
    for g in sheet:
        ev, why = match_game(g, conn, uploaded_at, year)
        res = resolve(g, ev) if ev else {}
        for market, sides in (("spread", ("away", "home")), ("total", ("over", "under")), ("money", ("away", "home"))):
            src = res.get(market) if ev else (g.get(market) or {})
            for heavy in sides:
                s = (src or {}).get(heavy) or {}
                if (s.get("handle") or 0) < 99:
                    continue
                fade = OPP[heavy]
                rec = {"rule": RULE, "sport_label": g.get("sport_label"), "sport": g.get("sport"), "market": market,
                       "sheet_game": f"{g.get('away')} @ {g.get('home')}", "heavy": heavy, "fade": fade,
                       "handle": s.get("handle"), "bets": s.get("bets"), "uploaded_at": uploaded_at}
                if not ev:
                    rec.update(status="NOT MATCHED", reason=why)
                    out.append(rec)
                    continue
                if market not in res:
                    rec.update(status="NOT MATCHED", reason=f"{market} numbers did not fit the market", event_id=ev["event_id"])
                    out.append(rec)
                    continue
                bets = s.get("bets")
                tier = "THIN" if bets is not None and bets >= 99 else "NORMAL"
                line_heavy, line_fade = res[market][heavy].get("line"), res[market][fade].get("line")
                mv = "UNKNOWN"
                if market in ("spread", "total"):
                    mv = movement(market, heavy, first_line(conn, ev["event_id"], market, heavy),
                                  line_heavy if market == "spread" else line_heavy)
                live = uploaded_at >= ev["kickoff"]
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
                key = abs(line_fade) in KEYS.get(ev["sport"], set()) if market == "spread" and line_fade is not None else False
                rec["sport"] = ev["sport"]
                rec.update(status="SIGNAL", event_id=ev["event_id"], game=f"{ev['away']} @ {ev['home']}",
                           kickoff=ev["kickoff"], tier=tier, lean=lean, live=live, movement=mv,
                           bucket=bucket(bets) if bets is not None else None,
                           bettor_read=bettor_read(bets) if bets is not None else None,
                           line_heavy=line_heavy, line_fade=line_fade, key_number=key,
                           price_heavy=line_heavy if market == "money" else -110,
                           price_fade=line_fade if market == "money" else -110,
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
                "NONE": "line held despite 99%+ of the money", "UNKNOWN": "line history unavailable"}[mv]
    if lean == "FLIP":
        return f"line moved with the money and {bets:g}% of bets looks like one big (possibly sharp) ticket"
    return f"line moved with the money and {bets:g}% of bets looks public"


def grade_side(market, side, line, price, final):
    """W/L/P and units for one side. final = (home_score, away_score)."""
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
    units = (p / 100 if p > 0 else 100 / -p) if res == "W" else -1.0 if res == "L" else 0.0
    return {"result": res, "units": round(units, 3)}


def grade(rec, final):
    if rec.get("status") != "SIGNAL":
        return None
    fade = grade_side(rec["market"], rec["fade"], rec["line_fade"], rec["price_fade"], final)
    follow = grade_side(rec["market"], rec["heavy"], rec["line_heavy"], rec["price_heavy"], final)
    lean = fade if rec["lean"] == "LIKE" else follow if rec["lean"] == "FLIP" else None
    return {"fade": fade, "follow": follow, "lean": lean}
