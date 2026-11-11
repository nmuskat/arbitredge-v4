import json, math, random, sqlite3, sys, collections
sys.path.insert(0, "/home/user/arbitredge-v4")
from need_v6.market import GameMarket
from sportsbook_v4 import settlement as ST
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch
S = sys.argv[1]
rep = json.load(open(S + "/audit/replay_h.json"))
old = json.load(open(S + "/v6/replay_v6.json"))
same = [a["card"] == b["card"] for a, b in zip(rep["frozen"], old["frozen"])]
print("reproduction identical:", all(same), len(same))
conn = sqlite3.connect(S + "/v6/market.db")
finals = {(f["sport"], f["card"]["event_id"]): f["final"] for f in rep["frozen"] if f["final"]}
GM = {}
def gm(sport, e, m, kick):
    k = (sport, e, m)
    if k not in GM: GM[k] = GameMarket(conn, sport, e, m, kick)
    return GM[k]

def row(sport, c, typ):
    blk = {"official": c["arbitredge_need"], "financial": c["financial_need"], "desk": c["trading_desk_need"]}[typ]
    side = blk.get("side")
    if not side: return None
    off = (c["book"]["offers"] or {}).get(side) or {}
    pt, pr = off.get("point"), off.get("price")
    if pt is None and c["book"]["posted_line"] is not None:
        pl = c["book"]["posted_line"]; pt = pl if (c["market"] == "total" or side == "home") else -pl
    if pt is None or not pr: return None
    kick = to_epoch(c["kickoff"]); g = gm(sport, c["event_id"], c["market"], kick)
    t = to_epoch(c["as_of"]); now, close = g.state(t), g.state(kick - 1)
    if now.get("status") != "OK" or close.get("status") != "OK": return None
    pw0, pp0 = g.fair_prob(now["fair_mu"], side, pt); pw1, pp1 = g.fair_prob(close["fair_mu"], side, pt)
    fin = finals.get((sport, c["event_id"]))
    res = units = None
    if fin:
        hs, as_ = fin["home"], fin["away"]
        res = ST.wager_result(side, pt, hs - as_) if c["market"] == "spread" else ST.total_result(side, pt, hs + as_)
        units = 0.0 if res == "push" else profit_on_win(1.0, pr) if res == "win" else -1.0
    return {"sport": sport, "game": c["event_id"], "market": c["market"], "side": side, "point": pt, "price": pr,
            "res": res, "units": units, "p_fair_now": pw0 / (1 - pp0), "p_fair_close": pw1 / (1 - pp1),
            "ev_now": pw0 * profit_on_win(1, pr) - (1 - pw0 - pp0), "clv": pw1 * profit_on_win(1, pr) - (1 - pw1 - pp1),
            "mins_before": (kick - t) / 60, "card": c}

def boot(rows, f, n=1000):
    by = collections.defaultdict(list)
    for r in rows: by[r["game"]].append(r)
    keys = list(by); rng = random.Random(1); vals = []
    for _ in range(n):
        s = [r for k in (rng.choice(keys) for _ in keys) for r in by[k]]
        v = f(s)
        if v is not None: vals.append(v)
    vals.sort()
    return (vals[int(.025 * len(vals))], vals[int(.975 * len(vals)) - 1]) if vals else (None, None)

def roi(s):
    d = [r for r in s if r["res"] in ("win", "loss")]
    return sum(r["units"] for r in d) / len(d) if d else None
def clv(s):
    return sum(r["clv"] for r in s) / len(s) if s else None

def summ(rows):
    g = [r for r in rows if r["res"]]
    w = sum(r["res"] == "win" for r in g); l = sum(r["res"] == "loss" for r in g); p = len(g) - w - l
    d = [r for r in g if r["res"] != "push"]
    exp = sum(r["p_fair_close"] for r in d); var = sum(r["p_fair_close"] * (1 - r["p_fair_close"]) for r in d)
    z = (w - exp) / math.sqrt(var) if var else 0
    r_, c_ = roi(g), clv(rows)
    rl, rh = boot(g, roi); cl, ch = boot(rows, clv)
    return {"n": len(rows), "rec": f"{w}-{l}-{p}", "roi": r_, "roi_ci": (rl, rh), "clv": c_, "clv_ci": (cl, ch),
            "ev_now": sum(r["ev_now"] for r in rows) / len(rows) if rows else None,
            "wins_vs_fair": (w - exp, z)}

def fmt(name, s):
    pc = lambda x: "  -  " if x is None else f"{100*x:+.1f}%"
    return (f"| {name} | {s['n']} | {s['rec']} | {pc(s['roi'])} [{pc(s['roi_ci'][0])}, {pc(s['roi_ci'][1])}] | "
            f"{pc(s['clv'])} [{pc(s['clv_ci'][0])}, {pc(s['clv_ci'][1])}] | {pc(s['ev_now'])} | "
            f"{s['wins_vs_fair'][0]:+.1f} (z {s['wins_vs_fair'][1]:+.2f}) |")

HDR = "| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |\n|---|---|---|---|---|---|---|"
out = []
def table(title, groups):
    out.append(f"\n### {title}\n{HDR}")
    for name, rows in groups:
        if rows: out.append(fmt(name, summ(rows)))

F = {t: [x for x in (row(f["sport"], f["card"], t) for f in rep["frozen"]) if x] for t in ("official", "financial", "desk")}
table("NEED type (frozen, all)", [(t, F[t]) for t in F])
for t in ("official", "financial"):
    table(f"{t}: sport x market", [(f"{s} {m}", [r for r in F[t] if r["sport"] == s and r["market"] == m])
                                   for s in ("nfl", "cfb") for m in ("spread", "total")])
for t in ("official", "financial"):
    table(f"{t}: desk action at freeze", [(a, [r for r in F[t] if r["card"]["trading_desk_need"]["action"] == a])
                                          for a in ("NO POSITION", "ACCEPT EXPOSURE", "BALANCE", "MOVE", "DEFEND")])
table("official: decision rule / source", [(s, [r for r in F["official"] if r["card"]["arbitredge_need"]["source"] == s])
                                            for s in ("TRADING DESK", "FINANCIAL")])
fin_only = [r for r in F["financial"] if not r["card"]["trading_desk_need"]["side"]]
table("financial vs desk", [("Financial NEED, desk also holds it", [r for r in F["financial"] if r["card"]["trading_desk_need"]["side"]]),
                            ("Financial NEED, desk NONE", fin_only), ("Trading Desk NEED", F["desk"])])
def pb(r):
    p = r["price"]
    return "≤ -125" if p <= -125 else "-124..-111" if p <= -111 else "-110..-101" if p < 0 else "+100 or better"
table("financial: price taken at frozen desk price", [(b, [r for r in F["financial"] if pb(r) == b]) for b in ("≤ -125", "-124..-111", "-110..-101", "+100 or better")])
def sb(r):
    a = abs(r["point"]); return "0-3" if a <= 3 else "3.5-7" if a <= 7 else "7.5-14" if a <= 14 else "14.5+"
table("financial spreads: size of number", [(b, [r for r in F["financial"] if r["market"] == "spread" and sb(r) == b]) for b in ("0-3", "3.5-7", "7.5-14", "14.5+")])
table("financial spreads: NEED on favourite vs underdog", [("favourite (laying points)", [r for r in F["financial"] if r["market"] == "spread" and r["point"] < 0]),
      ("underdog (getting points)", [r for r in F["financial"] if r["market"] == "spread" and r["point"] > 0])])
table("financial totals: over vs under", [(s, [r for r in F["financial"] if r["side"] == s]) for s in ("over", "under")])
table("financial: status", [(s, [r for r in F["financial"] if r["card"]["financial_need"]["status"] == s]) for s in ("MATERIAL", "MINOR")])
def rb(r):
    u = r["card"]["book"]["risk_used"] or 0; return "<25%" if u < .25 else "25-50%" if u < .5 else "50-100%" if u <= 1 else ">100% (over limit)"
table("financial: risk used at freeze", [(b, [r for r in F["financial"] if rb(r) == b]) for b in ("<25%", "25-50%", "50-100%", ">100% (over limit)")])
def shb(r):
    f = r["card"]["financial_need"]; s = f["swing"] / f["handle"] if f["handle"] else 0
    return "<20%" if s < .2 else "20-50%" if s < .5 else "≥50%"
table("financial: swing / handle (one-sidedness)", [(b, [r for r in F["financial"] if shb(r) == b]) for b in ("<20%", "20-50%", "≥50%")])
def tb(r):
    m = r["mins_before"]; return "30-60 min" if m < 60 else "1-3 h" if m < 180 else "3 h+"
table("financial: freeze time before kickoff", [(b, [r for r in F["financial"] if tb(r) == b]) for b in ("30-60 min", "1-3 h", "3 h+")])
Hh = collections.defaultdict(list)
for x in rep["horizons"]:
    r = row(x["sport"], x["card"], "financial")
    if r: Hh[x["h"]].append(r)
table("Financial NEED captured at T-48/24/6/1 h (each graded at the desk's price at that time)", [(f"T-{h}h", Hh[h]) for h in (48, 24, 6, 1)])
mmi = [f for f in rep["frozen"] if f["card"]["market_maker_inference"].get("pricing_opinion_side") not in (None, "UNKNOWN")]
def mrow(f):
    c = dict(f["card"]); s = c["market_maker_inference"]["pricing_opinion_side"]
    c["arbitredge_need"] = {"side": s}; return row(f["sport"], c, "official")
table("Market-maker 'pricing opinion' side (Pinnacle vs rec gap ≥ 0.5 pt), at desk price", [("pricing-opinion side", [x for x in map(mrow, mmi) if x])])
agree = [r for r in F["financial"] if r["card"]["market_maker_inference"].get("pricing_opinion_side") == r["side"]]
disag = [r for r in F["financial"] if r["card"]["market_maker_inference"].get("pricing_opinion_side") not in (None, "UNKNOWN", r["side"])]
table("Financial NEED vs market-maker pricing opinion", [("agree", agree), ("disagree", disag)])
open(S + "/audit/tables.md", "w").write("\n".join(out)); print("\n".join(out))
