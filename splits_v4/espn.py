"""
splits_v4/espn.py -- ESPN's public scoreboard data (owner-approved 2026-11-18, incl. the terms risk, private use):
games, DraftKings' opening and current lines, and final scores, for every sport on the Circa sheet. Free, no key.
Odds are only listed BEFORE a game ends, so the Circa engine stores what it used at upload time.
"""
import json
import time
import urllib.request

BASE = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date}{extra}"
LEAGUES = {   # our code: (ESPN path, extra query)
    "nfl": ("football/nfl", ""),
    "cfb": ("football/college-football", "&groups=80&limit=400"),
    "nba": ("basketball/nba", ""),
    "cbb": ("basketball/mens-college-basketball", "&groups=50&limit=400"),
    "nhl": ("hockey/nhl", ""),
    "mlb": ("baseball/mlb", ""),
    "wnba": ("basketball/wnba", ""),
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _f(s):
    if s is None:
        return None
    s = str(s).strip().lower().lstrip("ou")
    if s in ("even", "ev", "pk"):
        return 100.0 if s.startswith("ev") else 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _iso_epoch(s):
    import calendar
    s = s.replace("Z", "")
    fmt = "%Y-%m-%dT%H:%M" if len(s) <= 16 else "%Y-%m-%dT%H:%M:%S"
    return float(calendar.timegm(time.strptime(s[:19], fmt)))


def parse(doc, league):
    """[{"id","league","kickoff","home","away","state","home_score","away_score","odds": {...} or None}]"""
    out = []
    for e in doc.get("events", []):
        c = (e.get("competitions") or [{}])[0]
        teams = {t.get("homeAway"): t for t in c.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue
        g = {"id": str(e.get("id")), "league": league, "kickoff": _iso_epoch(e["date"]),
             "home": teams["home"]["team"].get("displayName"), "away": teams["away"]["team"].get("displayName"),
             "home_abbr": teams["home"]["team"].get("abbreviation"), "away_abbr": teams["away"]["team"].get("abbreviation"),
             "state": ((e.get("status") or {}).get("type") or {}).get("state"),
             "final": bool(((e.get("status") or {}).get("type") or {}).get("completed")),
             "home_score": _f(teams["home"].get("score")), "away_score": _f(teams["away"].get("score")), "odds": None}
        o = (c.get("odds") or [None])[0]
        if o:
            ps, tt, ml = o.get("pointSpread") or {}, o.get("total") or {}, o.get("moneyline") or {}
            pick = lambda d, side, when, key: _f(((d.get(side) or {}).get(when) or {}).get(key))  # noqa: E731
            g["odds"] = {"provider": (o.get("provider") or {}).get("name"),
                         "home_fav": bool((o.get("homeTeamOdds") or {}).get("favorite")),
                         "away_fav": bool((o.get("awayTeamOdds") or {}).get("favorite")),
                         **{f"spread_{s}_{w}": pick(ps, s, w, "line") for s in ("home", "away") for w in ("open", "close")},
                         **{f"spread_{s}_{w}_price": pick(ps, s, w, "odds") for s in ("home", "away") for w in ("open", "close")},
                         **{f"total_{w}": pick(tt, "over", w, "line") for w in ("open", "close")},
                         **{f"total_{s}_{w}_price": pick(tt, s, w, "odds") for s in ("over", "under") for w in ("open", "close")},
                         **{f"ml_{s}_{w}": pick(ml, s, w, "odds") for s in ("home", "away") for w in ("open", "close")}}
            if g["odds"]["total_close"] is None and o.get("overUnder") is not None:
                g["odds"]["total_close"] = _f(o.get("overUnder"))
        out.append(g)
    return out


def games(date, leagues=tuple(LEAGUES), get=fetch_json):
    """All games on an ESPN date (YYYYMMDD, US time) for the given leagues; a league that fails is skipped."""
    out, errors = [], {}
    for lg in leagues:
        path, extra = LEAGUES[lg]
        try:
            out += parse(get(BASE.format(path=path, date=date, extra=extra)), lg)
        except Exception as e:                       # noqa: BLE001
            errors[lg] = f"{type(e).__name__}: {e}"[:120]
    return out, errors
