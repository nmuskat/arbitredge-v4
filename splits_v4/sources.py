"""
splits_v4/sources.py -- fetch and parse the six public splits sites. Pure parsers (text in -> rows out) so they are
testable without the network; fetch() is the only network call (stdlib urllib, no API keys, no secrets).

Every parser returns rows:
  {"source", "sport" ("nfl"|"cfb"), "away", "home", "kickoff" (epoch or None), "rot" ([away, home] or None),
   "spread": {"away": {...}, "home": {...}} | None, "total": {"over": {...}, "under": {...}} | None}
  where each side dict may hold "bets" (% of bets), "money" (% of handle), "line", "price".
A site that changes its layout yields [] for that site (reported as unavailable), never guessed numbers.
"""
import datetime as _dt
import html as _html
import json
import re
import urllib.request
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
ET, PT = ZoneInfo("America/New_York"), ZoneInfo("America/Los_Angeles")
SBD_BOOKS = "sr%3Abook%3A7612%2Csr%3Abook%3A31520%2Csr%3Abook%3A28901%2Csr%3Abook%3A32784"
URLS = {
    "draftkings": {"nfl": "https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/?tb_eg=88808&tb_edate=n7days&tb_emt=0&tb_page={page}",
                   "cfb": "https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/?tb_eg=87637&tb_edate=n7days&tb_emt=0&tb_page={page}"},
    "scoresandodds": {"nfl": "https://www.scoresandodds.com/nfl/consensus-picks",
                      "cfb": "https://www.scoresandodds.com/ncaaf/consensus-picks"},
    "sportsbettingdime": {"nfl": f"https://www.sportsbettingdime.com/wp-json/adpt/v1/nfl-odds?books={SBD_BOOKS}&format=us",
                          "cfb": f"https://www.sportsbettingdime.com/wp-json/adpt/v1/ncaafb-odds?books={SBD_BOOKS}&format=us"},
    "betmgm": {lg: ("https://betmgm-hub-live.isportgenius.com.au/get-public-betting-dashboard-ajax?sport_url=american-football"
                    f"&league_url={k}&locationUrl=&selected_league={k}&selected_sport=american-football&selected_event=&oddsformat=")
               for lg, k in (("nfl", "nfl"), ("cfb", "ncaaf"))},
    "wagertalk": {"all": "https://www.wagertalk.com/odds"},
    "covers": {"nfl": "https://contests.covers.com/consensus/topconsensus/nfl/overall",
               "cfb": "https://contests.covers.com/consensus/topconsensus/ncaaf/overall"},
}
DK_MAX_PAGES = 6


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _text(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", h))).replace("−", "-").strip()


def _num(s):
    s = s.replace("−", "-").replace("½", ".5").strip()
    if s.lower() in ("ev", "even", "pk", "pick"):
        return 100.0 if s.lower().startswith("ev") else 0.0
    try:
        return float(s)
    except ValueError:
        return None


def _local(md, hm, tz, year):
    """'11/12', '8:15 PM' (or '5:15p') in timezone tz -> epoch."""
    hm = hm.strip().lower().replace(".", "")
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])m?", hm)
    if not m:
        return None
    h, mi = int(m.group(1)) % 12 + (12 if m.group(3) == "p" else 0), int(m.group(2))
    mo, d = (int(x) for x in md.split("/")[:2])
    return _dt.datetime(year, mo, d, h, mi, tzinfo=tz).timestamp()


def _pct(s):
    m = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", s)
    return float(m.group(1)) if m else None


def _sides(a, b, key, va, vb):
    return {a: {key: va}, b: {key: vb}}


# ------------------------------------------------------------------------------------------------ DraftKings
def parse_draftkings(h, sport, year):
    rows = []
    for blk in re.split(r'class="tb-se rounded', h)[1:]:
        m = re.search(r"<h5[^>]*>(.*?)</h5>", blk, re.S)
        t = re.search(r"(\d{1,2}/\d{1,2})\s*·\s*(\d{1,2}:\d{2}\s*[AP]M)\s*ET", _text(blk))
        if not m or " @ " not in _text(m.group(1)):
            continue
        away, home = (x.strip() for x in _text(m.group(1)).split(" @ ", 1))
        body = _text(blk)
        r = {"source": "draftkings", "sport": sport, "away": away, "home": home, "rot": None,
             "kickoff": _local(t.group(1), t.group(2), ET, year) if t else None, "spread": None, "total": None}
        sp = re.search(r"Spread (.+?) ([+-]?\d+(?:\.\d)?) ([+-]\d+) (\d+)% (\d+)% (.+?) ([+-]?\d+(?:\.\d)?) ([+-]\d+) (\d+)% (\d+)%",
                       body)
        if sp:
            s = {}
            for name, ln, pr, hd, bt in (sp.group(1, 2, 3, 4, 5), sp.group(6, 7, 8, 9, 10)):
                side = "away" if _same_team(name, away) else "home" if _same_team(name, home) else None
                if side:
                    s[side] = {"line": float(ln), "price": int(pr), "money": float(hd), "bets": float(bt)}
            if len(s) == 2:
                r["spread"] = s
        to = re.search(r"Total Over (\d+(?:\.\d)?) ([+-]\d+) (\d+)% (\d+)% Under (\d+(?:\.\d)?) ([+-]\d+) (\d+)% (\d+)%", body)
        if to:
            r["total"] = {"over": {"line": float(to.group(1)), "price": int(to.group(2)), "money": float(to.group(3)),
                                   "bets": float(to.group(4))},
                          "under": {"line": float(to.group(5)), "price": int(to.group(6)), "money": float(to.group(7)),
                                    "bets": float(to.group(8))}}
        if r["spread"] or r["total"]:
            rows.append(r)
    return rows


def _same_team(a, b):
    return a.strip().lower() == b.strip().lower()


# ------------------------------------------------------------------------------------------------ ScoresAndOdds
def parse_scoresandodds(h, sport):
    rows = {}
    for blk in re.split(r'<div class="trend-card consensus ', h)[1:]:
        kind = re.match(r"consensus-table-(spread|total|moneyline)", blk)
        if not kind or kind.group(1) == "moneyline":
            continue
        names = re.findall(r'<span class="team-name">\s*<span>([^<]+)</span>', blk)
        ko = re.search(r'data-role="localtime" data-value="([^"]+)"', blk)
        if len(names) < 2 or not ko:
            continue
        txt = _text(blk)
        m = re.search(r"% of Bets .*?(\d{1,3})% (\d{1,3})% (\d{1,3})% (\d{1,3})% % of Money", txt)
        if not m:
            continue
        a, b, c, d = (float(x) for x in m.groups())
        away, home = _html.unescape(names[0]).strip(), _html.unescape(names[1]).strip()
        k = (away, home, ko.group(1))
        r = rows.setdefault(k, {"source": "scoresandodds", "sport": sport, "away": away, "home": home, "rot": None,
                                "kickoff": _dt.datetime.fromisoformat(ko.group(1).replace("Z", "+00:00")).timestamp(),
                                "spread": None, "total": None})
        if kind.group(1) == "spread":
            r["spread"] = {"away": {"bets": a, "money": c}, "home": {"bets": b, "money": d}}
            ln = re.search(r"\(([+-]?\d+(?:\.\d)?)\) % of Bets", txt)
            if ln:
                r["spread"]["away"]["line"] = float(ln.group(1))
        else:
            r["total"] = {"over": {"bets": a, "money": c}, "under": {"bets": b, "money": d}}
            ln = re.search(r"Over \(o(\d+(?:\.\d)?)\)", txt)
            if ln:
                r["total"]["over"]["line"] = r["total"]["under"]["line"] = float(ln.group(1))
    return list(rows.values())


# ------------------------------------------------------------------------------------------------ SportsBettingDime
def parse_sportsbettingdime(body, sport):
    rows = []
    for g in json.loads(body).get("data", []):
        c, bs = g.get("competitors") or {}, g.get("bettingSplits") or {}
        try:
            name = {k: f"{c[k].get('market') or ''} {c[k].get('name') or ''}".strip() for k in ("away", "home")}
            ko = _dt.datetime.fromisoformat(g["scheduled"]).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        r = {"source": "sportsbettingdime", "sport": sport, "away": name["away"], "home": name["home"], "kickoff": ko,
             "rot": [c["away"].get("rotation_number"), c["home"].get("rotation_number")], "spread": None, "total": None}
        sp, to = bs.get("spread") or {}, bs.get("total") or {}
        if (sp.get("away") or {}).get("betsPercentage") is not None:
            r["spread"] = {s: {"bets": sp[s].get("betsPercentage"), "money": sp[s].get("stakePercentage")} for s in ("away", "home")}
        if (to.get("over") or {}).get("betsPercentage") is not None:
            r["total"] = {s: {"bets": to[s].get("betsPercentage"), "money": to[s].get("stakePercentage")} for s in ("over", "under")}
        if r["spread"] or r["total"]:
            rows.append(r)
    return rows


# ------------------------------------------------------------------------------------------------ BetMGM (bets % only)
def parse_betmgm(h, sport):
    rows = []
    for grp in re.split(r'<caption class="match-date">', h)[1:]:
        trs = re.split(r'<tr class="tr" style=', grp)[1:3]
        ev = re.search(r'data-event-name="([^"]+?) @ ([^"]+)"', grp)
        tm = re.search(r'data-match-time="(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d)', grp)
        if len(trs) < 2 or not ev:
            continue
        away, home = _html.unescape(ev.group(1)), _html.unescape(ev.group(2))
        ko = (_dt.datetime(*(int(x) for x in tm.groups()), tzinfo=ET).timestamp() if tm else None)
        cells = []
        for tr in trs:
            pc = [float(x) for x in re.findall(r'<div class="bet-percent">\s*(\d{1,3})%', tr)]
            bt = [_text(x) for x in re.findall(r'<span class="bet-type[^"]*">(.*?)</span>', tr)]
            pr = re.findall(r'<strong class="bet-point">\s*([+-]?\d+)', tr)
            cells.append((pc, bt, pr))
        (pa, ba, ra), (ph, bh, rh) = cells
        r = {"source": "betmgm", "sport": sport, "away": away, "home": home, "kickoff": ko, "rot": None,
             "spread": None, "total": None}
        if len(pa) >= 2 and len(ph) >= 2:
            r["spread"] = {"away": {"bets": pa[0], "line": _num(ba[0]) if ba else None},
                           "home": {"bets": ph[0], "line": _num(bh[0]) if bh else None}}
            ot = next((x for x in ba + bh if x.upper().startswith("O ")), None)
            first_over = bool(ba) and len(ba) > 1 and ba[1].upper().startswith("O")
            ov, un = (pa[1], ph[1]) if first_over else (ph[1], pa[1])
            r["total"] = {"over": {"bets": ov, "line": _num(ot[2:]) if ot else None},
                          "under": {"bets": un, "line": _num(ot[2:]) if ot else None}}
            rows.append(r)
    return rows


# ------------------------------------------------------------------------------------------------ WagerTalk (DK numbers + Circa)
def _wt_cell(s):
    """'-2½-10' -> ('spread', -2.5, '-10'); '40½o-18' -> ('total', 40.5, 'o-18'); '41' -> ('total', 41.0, '')."""
    s = s.strip().replace("½", ".5")
    m = re.match(r"^([+-]\d+(?:\.5)?|pk)([+-]\d\d|ev)?$", s, re.I)
    if m:
        return "spread", (0.0 if m.group(1).lower() == "pk" else float(m.group(1))), m.group(2) or ""
    m = re.match(r"^(\d+(?:\.5)?)([ou][+-]\d\d|[ou]ev)?$", s, re.I)
    if m:
        return "total", float(m.group(1)), m.group(2) or ""
    return None


def parse_wagertalk(h, year):
    rows, sport = [], None
    for part in re.split(r"(<tr class=\"head1\">.*?</tr>|<tr class=\"(?:reg|alt)\">.*?</tr>)", h, flags=re.S):
        if part.startswith('<tr class="head1"'):
            t = _text(part).upper()
            sport = "nfl" if "NFL" in t else "cfb" if "COLLEGE FOOTBALL" in t else None
            continue
        if not part.startswith('<tr class="') or sport is None:
            continue
        divs = lambda cls: [_text(x) for x in re.findall(r"<div[^>]*>(.*?)</div>",  # noqa: E731
                                                         (re.search(rf'class="{cls}[^"]*">(.*?)</t[hd]>', part, re.S) or [None, ""])[1], re.S)]
        tm, gn, te = divs("time"), divs("gnum"), divs("team")
        books = [[_text(x) for x in re.findall(r"<div[^>]*>(.*?)</div>", c, re.S)]
                 for c in re.findall(r'<td class="book">(.*?)</td>', part, re.S)]
        if len(tm) < 2 or len(te) < 2 or len(books) < 6:
            continue
        r = {"source": "wagertalk", "sport": sport, "away": te[0], "home": te[1],
             "kickoff": _local(tm[0], tm[1], PT, year), "rot": [int(x) for x in gn[:2]] if all(x.isdigit() for x in gn[:2]) else None,
             "spread": None, "total": None, "circa": None, "open": None}
        tick, money = books[0], books[1]
        sp, to = {}, {}
        for i, side in ((0, "away"), (1, "home")):
            for vals, key in ((tick, "bets"), (money, "money")):
                v = vals[i] if i < len(vals) else ""
                p = _pct(v)
                if p is None:
                    continue
                if v[:1].lower() in "ou":
                    ou = "over" if v[:1].lower() == "o" else "under"
                    to.setdefault(ou, {})[key] = p
                    to.setdefault("under" if ou == "over" else "over", {})[key] = round(100 - p, 2)
                else:
                    sp.setdefault(side, {})[key] = p
                    sp.setdefault("home" if side == "away" else "away", {})[key] = round(100 - p, 2)
        r["spread"], r["total"] = (sp or None), (to or None)
        for col, key in ((2, "open"), (5, "circa")):
            line = {}
            for i, side in ((0, "away"), (1, "home")):
                c = _wt_cell(books[col][i]) if i < len(books[col]) else None
                if c and c[0] == "spread":
                    line["spread_" + side] = c[1]
                    line["spread_" + ("home" if side == "away" else "away")] = -c[1]
                elif c and c[0] == "total":
                    line["total"] = c[1]
                    line["total_juice"] = c[2]
            r[key] = line or None
        rows.append(r)
    return rows


# ------------------------------------------------------------------------------------------------ Covers (contest picks; display only)
def parse_covers(h, sport, year):
    rows = []
    lg = "NFL" if sport == "nfl" else "NCAAF"
    for m in re.finditer(rf"{lg} (\S+) (\S+) \w{{3}}\. (\w{{3}}) (\d{{1,2}}) (\d{{1,2}}:\d{{2}} [ap]m) ET (\d{{1,3}})% (\d{{1,3}})%",
                         _text(h)):
        mon = _dt.datetime.strptime(m.group(3), "%b").month
        rows.append({"source": "covers", "sport": sport, "away": m.group(1), "home": m.group(2), "rot": None,
                     "kickoff": _local(f"{mon}/{m.group(4)}", m.group(5), ET, year),
                     "spread": {"away": {"picks": float(m.group(6))}, "home": {"picks": float(m.group(7))}}, "total": None})
    return rows


# ------------------------------------------------------------------------------------------------ collect all
def collect(year, get=fetch, sports=("nfl", "cfb")):
    """{source: {"status": "OK"|"ERROR: ...", "rows": [...]}} -- each site independent; failures recorded."""
    out = {}

    def run(name, fn):
        try:
            rows = fn()
            out[name] = {"status": "OK" if rows else "EMPTY (layout changed or no games)", "rows": rows}
        except Exception as e:                       # noqa: BLE001 -- one site failing never stops the others
            out[name] = {"status": f"ERROR: {type(e).__name__}: {e}"[:200], "rows": []}

    def dk():
        rows = []
        for sp in sports:
            for page in range(1, DK_MAX_PAGES + 1):
                got = parse_draftkings(get(URLS["draftkings"][sp].format(page=page)), sp, year)
                new = [r for r in got if (r["away"], r["home"]) not in {(x["away"], x["home"]) for x in rows}]
                if not new:
                    break
                rows += new
        return rows
    def per_sport(fn):                    # one league's page failing never drops the other league
        def go():
            rows, errs = [], []
            for sp in sports:
                try:
                    rows += fn(sp)
                except Exception as e:           # noqa: BLE001
                    errs.append(f"{sp}: {type(e).__name__}")
            if errs and not rows:
                raise RuntimeError("; ".join(errs))
            return rows
        return go
    run("draftkings", dk)
    run("scoresandodds", per_sport(lambda sp: parse_scoresandodds(get(URLS["scoresandodds"][sp]), sp)))
    run("sportsbettingdime", per_sport(lambda sp: parse_sportsbettingdime(get(URLS["sportsbettingdime"][sp]), sp)))
    run("betmgm", per_sport(lambda sp: parse_betmgm(get(URLS["betmgm"][sp]), sp)))
    run("wagertalk", lambda: [r for r in parse_wagertalk(get(URLS["wagertalk"]["all"]), year) if r["sport"] in sports])
    run("covers", per_sport(lambda sp: parse_covers(get(URLS["covers"][sp]), sp, year)))
    return out
