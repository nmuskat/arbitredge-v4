"""
web_v4/live_site.py -- the automatically rebuilt V4 site, from COLLECTED data only.

Inputs (all already produced by the GitHub Actions pipeline; no API calls here):
  * odds shards (release odds-data-*)  -> market store (OBSERVED quotes)
  * credit ledger (release v4-state)   -> collection health
  * CFBD AP-poll snapshots (release cfb-rankings, optional) -> CFB Top-25 gating
Outputs: site_snapshot.json and index.html (one self-contained page: no external
scripts, fonts or requests; data embedded as JSON).

Labels the page always shows:
  OBSERVED      real quotes collected by V4, with the time they were observed
  MODELED       synthetic sportsbook customers (sportsbook_v4.live_book); never real splits
  EXPERIMENTAL  uncalibrated heuristics (sharp-money score)
  PAPER/WATCH   EV candidates from an unvalidated strategy: no official plays
Freshness is computed IN THE BROWSER from the observation time, so a page opened
hours later downgrades itself (LIVE <= 45 min, DELAYED <= 6 h, STALE beyond) and
never presents old odds as current.

  python -m web_v4.live_site --shards shards --ledger state/ledger.jsonl \\
      --rankings-dir rankings --out site
"""

import argparse
import calendar
import glob
import json
import os
import sqlite3
import time

from data_v4 import rankings
from sportsbook_v4 import live_book
from sportsbook_v4.schema import ensure_schema
from web_v4 import live

SCHEMA = "v4-live-site-1"
FRESH = {"live_max_min": 45, "delayed_max_min": 360}
SPORTS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}


def _iso(t):
    return None if t is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _epoch(iso):
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")) if iso else None


def load_rankings(rankings_dir):
    if not rankings_dir or not os.path.isdir(rankings_dir):
        return []
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(rankings_dir, "rankings_*.json")))]


def _rank_tag(snaps, home, away, kickoff_iso):
    if not snaps:
        return {"status": "UNAVAILABLE", "reason": "AP rankings not connected (CollegeFootballData key)"}, None
    st = rankings.top25_status(home, away, _epoch(kickoff_iso), snaps)
    return st, (st["top25"] if st["status"] == "OK" else None)


def _clean(o):
    if isinstance(o, float):
        return None if o != o else round(o, 6)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    return o


def build_snapshot(conn, ledger_rows, snaps, now, book_conn=None, bettors=10_000, commit=None):
    """Everything the page shows, from the market store as of `now`."""
    out = {"schema": SCHEMA, "generated_at": _iso(now), "commit": commit, "freshness_policy": FRESH,
           "sports": {}, "collection": live.collection_health(conn, ledger_rows, now),
           "plays": [], "plays_note": "No strategy has passed the evidence gate (rebuild plan section G). "
                                      "Default decision: NO BET.",
           "splits_real": {"status": "UNAVAILABLE",
                           "reason": "No licensed betting-splits source is connected. Only MODELED splits "
                                     "from the synthetic sportsbook are shown, and they are not market data."},
           "sims": {"status": "NOT CONNECTED",
                    "detail": "The original ArbitrEdge SIMS source has not been provided, so no SIMS projections "
                              "are shown and none are substituted. V4's Engine C (NFL) ties the closing market "
                              "in walk-forward tests and is used only for key-number pricing in the EV scanner."}}
    latest_snap = max(snaps, key=lambda s: s["fetched_at"]) if snaps else None
    for sport, key in SPORTS.items():
        b = live.board(conn, key, now)
        rows, hist = live.scanner_rows(conn, key, now)
        sharp = live.sharp_rows(conn, key, now)
        s = {"key": key, "label": "NFL" if sport == "nfl" else "College football (AP Top-25)",
             "observed_at": b["observed_at"], "games": b["games"], "scanner": rows, "sharp": sharp,
             "performance": hist}
        if sport == "cfb":
            tags = {}
            for g in s["games"]:
                g["rankings"], g["top25"] = _rank_tag(snaps, g["home"], g["away"], g["kickoff"])
                tags[g["event_id"]] = g["top25"]
            for r in s["scanner"]:
                r["top25"] = tags.get(r["game_id"], _rank_tag(snaps, r["home"], r["away"], r["kickoff"])[1])
            for r in s["sharp"]:
                r["top25"] = tags.get(r["event_id"], _rank_tag(snaps, r["home"], r["away"], r["kickoff"])[1])
            s["rankings"] = ({"status": "OK", "source": latest_snap["source"], "fetched_at": _iso(latest_snap["fetched_at"]),
                              "poll": rankings.AP_POLL} if latest_snap else
                             {"status": "UNAVAILABLE", "reason": "AP rankings not connected: the CollegeFootballData "
                                                                 "free API key has not been added yet."})
        out["sports"][sport] = s
    if book_conn is not None:
        bk = live_book.build(book_conn, conn, now, snaps, bettors=bettors)
        for sport in SPORTS:
            out["sports"][sport]["book"] = bk[sport]
    return _clean(out)


def rebuild_store(shards_dir, db_path=":memory:"):
    from analysis_v4.actions_analyze import rebuild
    paths = sorted(glob.glob(os.path.join(shards_dir, "**", "quotes_*.jsonl.gz"), recursive=True))
    conn, counts = rebuild(paths, db_path)
    return conn, counts, len(paths)


# ------------------------------------------------------------------ page

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>ArbitrEdge V4</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#5d6470;--line:#e2e5ea;--acc:#2456c7;
--ok:#1d7a46;--warn:#9a6200;--bad:#b42318;--chip:#eef1f6;--obs:#1d7a46;--mod:#7a3fb0;--exp:#9a6200}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaee;--mute:#9aa2ae;--line:#2a2f39;
--acc:#7aa2ff;--ok:#4cc584;--warn:#e0a63a;--bad:#ff6b5e;--chip:#222733;--obs:#4cc584;--mod:#c08cf0;--exp:#e0a63a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);
padding:10px 16px calc(8px) 16px}
.top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.brand{font-weight:700}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--ink);padding:6px 12px;font:inherit;cursor:pointer}
.seg button.on{background:var(--acc);color:#fff}
nav{display:flex;gap:4px;overflow-x:auto;margin-top:8px;padding-bottom:2px;scrollbar-width:none}
nav a{white-space:nowrap;text-decoration:none;color:var(--mute);padding:5px 10px;border-radius:999px;font-size:14px}
nav a.on{background:var(--chip);color:var(--ink);font-weight:600}
main{max-width:1100px;margin:0 auto;padding:12px 16px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
h2{font-size:18px;margin:6px 0 2px}h3{font-size:15px;margin:4px 0}
.mute{color:var(--mute)}.small{font-size:13px}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;padding:2px 7px;border-radius:999px;
border:1px solid currentColor;margin-right:4px;vertical-align:middle}
.b-LIVE,.b-OBSERVED,.b-OK{color:var(--ok)}.b-DELAYED,.b-EXPERIMENTAL,.b-WATCH,.b-LOW{color:var(--warn)}
.b-STALE,.b-UNAVAILABLE,.b-NOBET{color:var(--bad)}.b-MODELED{color:var(--mod)}.b-INFO{color:var(--mute)}
.banner{border-radius:8px;padding:8px 12px;margin:8px 0;font-size:14px}
.banner.stale{background:color-mix(in srgb,var(--bad) 14%,transparent);border:1px solid var(--bad)}
.banner.delayed{background:color-mix(in srgb,var(--warn) 14%,transparent);border:1px solid var(--warn)}
.banner.info{background:var(--chip)}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
.filters label{font-size:12px;color:var(--mute);display:flex;flex-direction:column;gap:2px}
select,input{font:inherit;font-size:14px;padding:5px 7px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--ink);max-width:180px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 6px;border-bottom:1px solid var(--line);
vertical-align:top}th{font-size:12px;color:var(--mute);font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
.game{cursor:pointer}.game:hover{background:var(--chip)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.kpi{background:var(--chip);border-radius:8px;padding:8px 10px}.kpi b{display:block;font-size:20px}
.bar{position:relative;height:16px;background:var(--chip);border-radius:4px;overflow:hidden;margin:2px 0}
.bar i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);opacity:.75}
.bar s{position:absolute;top:0;bottom:0;border-left:2px solid var(--ink);border-right:2px solid var(--ink);opacity:.35}
.dim{opacity:.45}.wrap{overflow-x:auto}
.ml{display:none;font-size:11px;font-weight:700;color:var(--mute);text-transform:uppercase}
@media (max-width:640px){.hide-s{display:none}
.odds thead{display:none}.odds tr{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 8px;padding:8px 0;border-bottom:1px solid var(--line)}
.odds td{border:0;padding:2px 0}.odds td:first-child{grid-column:1/-1;font-weight:600}.ml{display:block}}
</style></head><body>
<header><div class="top"><span class="brand">ArbitrEdge V4</span>
<span class="seg" id="sport"><button data-s="nfl">NFL</button><button data-s="cfb">CFB Top-25</button></span>
<span id="fresh" class="small"></span></div>
<nav id="nav"></nav></header>
<main id="main"></main>
<script type="application/json" id="data">__DATA__</script>
<script>
"use strict";
const D=JSON.parse(document.getElementById("data").textContent);
const SECTIONS=[["overview","Overview"],["odds","Odds Board"],["splits","Betting Splits"],["need","ArbitrEdge Need"],
["sharp","Sharp Money"],["ev","EV Scanner"],["sims","SIMS"],["performance","Performance"],["health","Health"]];
const MK={spreads:"Spread",totals:"Total",h2h:"Moneyline"};const BMK={spread:"spreads",total:"totals",moneyline:"h2h"};
const st={sport:"nfl",sec:"overview",game:null,f:{q:"",market:"",book:"",date:"",strength:"0",fresh:"all",unfiltered:false}};
try{const s=localStorage.getItem("v4sport");if(s==="nfl"||s==="cfb")st.sport=s}catch(e){}
function el(tag,attrs,...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs||{})){
 if(k==="class")e.className=v;else if(k==="on")for(const[ev,fn]of Object.entries(v))e.addEventListener(ev,fn);else e.setAttribute(k,v)}
 for(const k of kids.flat()){if(k==null||k===false)continue;e.append(k instanceof Node?k:document.createTextNode(String(k)))}return e}
const badge=(t,c)=>el("span",{class:"badge b-"+(c||t).replace(/\s/g,"")},t);
const pct=x=>x==null?"–":(100*x).toFixed(1)+"%";const am=p=>p==null?"–":(p>0?"+"+p:String(p));
const ln=x=>x==null?"":(x>0?"+"+x:String(x));
function ageMin(iso){return iso?(Date.now()-Date.parse(iso))/60000:Infinity}
function freshness(iso){const a=ageMin(iso),p=D.freshness_policy;return a<=p.live_max_min?"LIVE":a<=p.delayed_max_min?"DELAYED":"STALE"}
function fmtAge(m){if(!isFinite(m))return"never";if(m<90)return Math.round(m)+" min";if(m<48*60)return(m/60).toFixed(1)+" h";return(m/1440).toFixed(1)+" days"}
function local(iso){if(!iso)return"–";const d=new Date(iso);return d.toLocaleString([], {weekday:"short",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"})}
const S=()=>D.sports[st.sport];
function cfbGate(rowTop25){if(st.sport!=="cfb")return true;if(st.f.unfiltered)return true;return rowTop25===true}
function upcoming(g){return Date.parse(g.kickoff)>Date.now()}
function gameMatch(g){const f=st.f;if(f.q&&!(g.home+" "+g.away).toLowerCase().includes(f.q.toLowerCase()))return false;
 if(f.date&&(new Date(g.kickoff)).toISOString().slice(0,10)!==f.date)return false;return true}
function games(){const s=S();return(s.games||[]).filter(g=>upcoming(g)&&cfbGate(g.top25)&&gameMatch(g))}
function gameById(id){for(const s of Object.values(D.sports))for(const g of s.games||[])if(g.event_id===id)return g;return null}
function freshBanner(){const s=S(),f=freshness(s.observed_at),a=fmtAge(ageMin(s.observed_at));
 if(!s.observed_at)return el("div",{class:"banner stale"},"No odds have been collected for this sport yet.");
 if(f==="STALE")return el("div",{class:"banner stale"},badge("STALE")," Odds were observed "+a+" ago ("+local(s.observed_at)+"). They are NOT current prices; shown for reference only.");
 if(f==="DELAYED")return el("div",{class:"banner delayed"},badge("DELAYED")," Odds observed "+a+" ago. Prices may have moved since.");
 return el("div",{class:"banner info"},badge("LIVE")," Odds observed "+a+" ago (collection cadence varies; faster near kickoff).")}
function cfbBanner(){if(st.sport!=="cfb")return null;const r=S().rankings||{};
 const pend=(S().games||[]).filter(g=>upcoming(g)&&g.rankings&&g.rankings.status==="PENDING").length;
 if(r.status==="OK")return el("div",{class:"banner info"},"AP Top-25 per game uses the poll applicable at kickoff. Source: "+r.source+", fetched "+local(r.fetched_at)+".",
  pend?" "+pend+" later game(s) wait for next week's poll (PENDING; shown with the non-Top-25 games).":"",
  st.f.unfiltered?el("b",{}," Showing ALL collected FBS games (unfiltered)."):null);
 return el("div",{class:"banner stale"},badge("UNAVAILABLE")," "+(r.reason||"AP rankings unavailable")+" CFB Top-25 views are empty until rankings are connected; nothing is guessed.",
  st.f.unfiltered?el("b",{}," Showing ALL collected FBS games, Top-25 status UNKNOWN."):null)}
function filters(opts){const f=st.f,set=(k)=>e=>{f[k]=e.target.type==="checkbox"?e.target.checked:e.target.value;render()};
 const sel=(k,label,choices)=>el("label",{},label,(()=>{const s=el("select",{on:{change:set(k)}});for(const[v,t]of choices){const o=el("option",{value:v},t);if(String(f[k])===v)o.selected=true;s.append(o)}return s})());
 const books=[...new Set((D.collection.books||[]).flatMap(b=>b.books))].sort();
 const dates=[...new Set((S().games||[]).filter(upcoming).map(g=>new Date(g.kickoff).toISOString().slice(0,10)))].sort();
 const kids=[el("label",{},"Game",el("input",{type:"search",placeholder:"team",value:f.q,on:{input:e=>{f.q=e.target.value;clearTimeout(window._t);window._t=setTimeout(render,250)}}})),
  sel("date","Date",[["","All dates"],...dates.map(d=>[d,d])])];
 if(opts.market)kids.push(sel("market","Market",[["","All markets"],["spreads","Spread"],["totals","Total"],["h2h","Moneyline"]]));
 if(opts.book)kids.push(sel("book","Sportsbook",[["","All books"],...books.map(b=>[b,b])]));
 if(opts.strength)kids.push(sel("strength","Signal strength (EV low bound)",[["0","Any"],["0.01","≥ 1%"],["0.02","≥ 2%"],["0.04","≥ 4%"]]));
 if(opts.fresh)kids.push(sel("fresh","Freshness",[["all","All quotes"],["live","Fresh at observation only"]]));
 if(st.sport==="cfb")kids.push(el("label",{},"CFB scope",el("span",{},(()=>{const c=el("input",{type:"checkbox",on:{change:set("unfiltered")}});c.checked=f.unfiltered;return c})()," show non-Top-25")));
 return el("div",{class:"filters"},kids)}
const card=(...k)=>el("div",{class:"card"},...k);
function gameHead(g){return el("span",{},g.away+" @ "+g.home,g.rankings&&g.rankings.status==="OK"?el("span",{class:"mute small"}," ("+(g.rankings.away_rank?"#"+g.rankings.away_rank:"NR")+" @ "+(g.rankings.home_rank?"#"+g.rankings.home_rank:"NR")+")"):null)}
function openGame(id){location.hash="game/"+id}

// ---------- sections
const R={};
R.overview=()=>{const s=S(),c=D.collection,gs=games(),bk=s.book||{};
 const scan=(s.scanner||[]).filter(r=>cfbGate(r.top25));
 return[freshBanner(),cfbBanner(),card(el("h2",{},"Overview: "+s.label),
  el("div",{class:"grid"},el("div",{class:"kpi"},el("b",{},gs.length),"upcoming games"),
   el("div",{class:"kpi"},el("b",{},scan.length),"EV candidates (WATCH, paper)"),
   el("div",{class:"kpi"},el("b",{},bk.audit?bk.audit.wagers:"–"),"modeled wagers"),
   el("div",{class:"kpi"},el("b",{},D.plays.length),"official plays")),
  el("p",{},badge("NO BET","NOBET")," "+D.plays_note)),
  card(el("h3",{},"What each label means"),el("p",{class:"small"},badge("OBSERVED")," real quotes V4 collected · ",badge("MODELED")," synthetic customers, not real bettors · ",
   badge("EXPERIMENTAL")," uncalibrated heuristic · ",badge("WATCH")," paper candidate from an unvalidated strategy")),
  card(el("h3",{},"Line-shopping test (closing EV)"),perfLine(s.performance))]};
function perfLine(h){if(!h)return el("p",{class:"mute"},"No analysis yet.");
 const ci=h.closing_ev;return el("p",{},badge(h.verdict==="INSUFFICIENT DATA"?"INSUFFICIENT DATA":h.verdict,"INFO")," ",
  h.opportunities+" opportunities, "+h.close_observed+" with an observed close"+(ci&&ci.mean!=null?", mean closing EV "+pct(ci.mean)+" (95% CI "+pct(ci.lo)+" to "+pct(ci.hi)+")":"")+".")}
R.odds=()=>{const s=S(),f=st.f,ms=f.market?[f.market]:["spreads","totals","h2h"];const rows=[];
 for(const g of games()){const tr=el("tr",{class:"game",on:{click:()=>openGame(g.event_id)}},el("td",{},gameHead(g),el("div",{class:"mute small"},local(g.kickoff))));
  for(const m of ms){const x=g.markets[m]||{};const sides=Object.keys(x.best||{});
   let txt;if(x.status!=="OK")txt=el("span",{class:"mute"},x.status||"–");else{
    txt=el("div",{},...sides.map(sd=>{let b=x.best[sd];if(f.book){const q=(x.books||{})[f.book];b=q&&q[sd]?{book:f.book,price:q[sd][0],point:q[sd][1],fresh:q.fresh}:null}
     const label=m==="h2h"?sd:(m==="spreads"?sd+" "+ln(sd==="home"?x.line:(x.line==null?null:-x.line)):sd+" "+x.line);
     if(f.fresh==="live"&&b&&b.fresh===false)b=null;
     return el("div",{class:"small"},label+": ",el("b",{},b?am(b.price):"–"),b?el("span",{class:"mute"}," "+b.book):null," ",el("span",{class:"mute"},"fair "+pct(x.fair&&x.fair[sd])))}))}
   tr.append(el("td",{},el("span",{class:"ml"},MK[m]),txt))}
  rows.push(tr)}
 const stale=freshness(s.observed_at)==="STALE";
 return[freshBanner(),cfbBanner(),card(el("h2",{},badge("OBSERVED"),"Odds board"),el("p",{class:"small mute"},"Best price per side among books on the consensus line; fair = market-maker consensus with vig removed. Tap a game for every book."),
  filters({market:1,book:1,fresh:1}),el("div",{class:"wrap"+(stale?" dim":"")},el("table",{class:"odds"},el("thead",{},el("tr",{},el("th",{},"Game"),...ms.map(m=>el("th",{},MK[m])))),el("tbody",{},rows))),
  rows.length?null:el("p",{class:"mute"},"No upcoming games match."))]};
function splitBar(sd,b){const w=sd.ticket_pct,h=sd.handle_pct;return el("div",{},
 el("div",{class:"small"},"tickets "+w+"%",b?el("span",{class:"mute"}," (range "+b.ticket_pct[0]+"–"+b.ticket_pct[1]+")"):null),
 el("div",{class:"bar"},el("i",{style:"width:"+w+"%"}),b?el("s",{style:"left:"+b.ticket_pct[0]+"%;width:"+(b.ticket_pct[1]-b.ticket_pct[0])+"%"}):null),
 el("div",{class:"small"},"handle "+h+"%",b?el("span",{class:"mute"}," (range "+b.handle_pct[0]+"–"+b.handle_pct[1]+")"):null),
 el("div",{class:"bar"},el("i",{style:"width:"+h+"%"}),b?el("s",{style:"left:"+b.handle_pct[0]+"%;width:"+(b.handle_pct[1]-b.handle_pct[0])+"%"}):null))}
function bookBlock(){const bk=S().book;if(!bk)return card(el("p",{class:"mute"},"Synthetic book not built."));
 if(bk.status==="UNAVAILABLE")return card(badge("UNAVAILABLE")," "+(bk.coverage.reason||""));
 if(bk.status==="NO_GAMES")return card(el("p",{class:"mute"},"No games in the current window."));return null}
function bookGames(){const bk=S().book||{};return(bk.games||[]).filter(g=>upcoming(g)&&gameMatch(g))}
R.splits=()=>{const f=st.f,bk=S().book||{};const head=[card(el("h2",{},"Betting splits"),
  el("p",{},badge("UNAVAILABLE")," Real splits: "+D.splits_real.reason),
  el("p",{class:"small"},badge("MODELED")," Below: the synthetic sportsbook's own customers ("+(bk.audit?bk.audit.bettors.toLocaleString():"10,000")+" persistent simulated bettors for this sport) betting into the real collected prices. Tickets come from wager counts and handle from stakes. Ranges show how far each split moves under alternative behaviour assumptions ("+((bk.sensitivity||{}).variants||[]).join(", ")+"). These are not DraftKings, FanDuel or market-wide splits."),
  filters({market:1}))];
 const b=bookBlock();if(b)return[...head,b];const out=[];
 for(const g of bookGames()){const ms=f.market?[f.market]:["spreads","totals","h2h"];const rows=[];
  for(const[bm,row]of Object.entries(g.splits)){const m=BMK[bm];if(!ms.includes(m))continue;
   if(row.status!=="OK"){rows.push(el("div",{class:"small mute"},MK[m]+": no modeled wagers yet"));continue}
   rows.push(el("div",{},el("h3",{},MK[m]," ",row.sample==="LOW"?badge("LOW SAMPLE","LOW"):null,el("span",{class:"mute small"}," "+row.tickets+" tickets, $"+Math.round(row.handle).toLocaleString()+" handle")),
    ...Object.entries(row.sides).map(([sd,v])=>el("div",{},el("b",{class:"small"},sd),splitBar(v,(g.bands[bm]||{})[sd])))))}
  out.push(card(el("div",{class:"game",on:{click:()=>openGame(g.event_id)}},el("h3",{},badge("MODELED"),gameHead(g)),el("div",{class:"mute small"},local(g.kickoff))),...rows))}
 return[...head,...(out.length?out:[card(el("p",{class:"mute"},"No modeled wagers on upcoming games yet. The synthetic book only trades while real prices are being collected."))])]};
R.need=()=>{const bk=S().book||{};const head=[card(el("h2",{},"ArbitrEdge Need"),el("p",{class:"small"},badge("MODELED"),
 " The outcome the synthetic book's own liability most needs, computed exactly from its accepted wagers (P&L over every final score). It is a liability fact about simulated customers, NOT a prediction, NOT fading the public, and NOT a betting recommendation."),filters({}))];
 const b=bookBlock();if(b)return[...head,b];const out=[];
 for(const g of bookGames()){const n=g.need;if(!n){continue}const sp=n.spread||{},to=n.total||{},ml=n.moneyline||{};
  out.push(card(el("div",{class:"game",on:{click:()=>openGame(g.event_id)}},el("h3",{},gameHead(g))),
   el("table",{},el("tbody",{},
    sp.status?el("tr",{},el("td",{},"Spread"),el("td",{},sp.financial_need_side?el("b",{},sp.financial_need_side+" covers"):"balanced"),el("td",{class:"num"},"swing $"+Math.round(sp.pnl_swing||0).toLocaleString())):null,
    to.status?el("tr",{},el("td",{},"Total"),el("td",{},to.financial_need_side?el("b",{},to.financial_need_side):"balanced"),el("td",{class:"num"},to.handle!=null?"handle $"+Math.round(to.handle).toLocaleString():"")):null,
    ml.status?el("tr",{},el("td",{},"Moneyline"),el("td",{},ml.financial_need_side?el("b",{},ml.financial_need_side+" wins"):"balanced"),el("td",{class:"num"},ml.handle!=null?"handle $"+Math.round(ml.handle).toLocaleString():"")):null)),
   n.joint?el("p",{class:"small mute"},"Joint book: best $"+Math.round(n.joint.best_case.pnl).toLocaleString()+" at "+n.joint.best_case.home_score+"–"+n.joint.best_case.away_score+" (home–away), worst $"+Math.round(n.joint.worst_case.pnl).toLocaleString()+" at "+n.joint.worst_case.home_score+"–"+n.joint.worst_case.away_score+"."):null))}
 return[...head,...(out.length?out:[card(el("p",{class:"mute"},"No modeled liability yet on upcoming games."))])]};
R.sharp=()=>{const f=st.f;const rows=(S().sharp||[]).filter(r=>cfbGate(r.top25)&&(!f.market||r.market===f.market)&&upcoming(r)&&gameMatch(r));
 const ev=rows.filter(r=>r.status!=="INSUFFICIENT EVIDENCE");
 return[freshBanner(),cfbBanner(),card(el("h2",{},badge("EXPERIMENTAL"),"Sharp money"),
  el("p",{class:"small"},"Evidence from real price movement at market-making books versus retail books. Reverse line movement and ticket/handle divergence need real betting splits and are UNAVAILABLE. The score is uncalibrated; when the quote history is too thin the page says INSUFFICIENT EVIDENCE instead of guessing."),
  filters({market:1}),el("p",{},el("b",{},ev.length)," of "+rows.length+" game-markets show evidence."),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Market"),el("th",{},"Status"),el("th",{class:"hide-s"},"Detail"))),
   el("tbody",{},rows.map(r=>el("tr",{class:"game",on:{click:()=>openGame(r.event_id)}},el("td",{},r.away+" @ "+r.home),el("td",{},MK[r.market]),
    el("td",{},r.status==="INSUFFICIENT EVIDENCE"?badge("INSUFFICIENT EVIDENCE","INFO"):el("span",{},badge(r.status,"WATCH")," "+(r.suspected_side||""))),
    el("td",{class:"hide-s small mute"},(r.supporting||[]).slice(0,2).join("; ")||(r.reason||""),
     (r.timing_unresolved||[]).length?el("div",{},"Timing unresolved: "+r.timing_unresolved.join("; ")):null)))))))]};
R.ev=()=>{const f=st.f;const rows=(S().scanner||[]).filter(r=>cfbGate(r.top25)&&(!f.market||r.market===f.market)&&(!f.book||r.book===f.book)
  &&upcoming(r)&&gameMatch(r)&&(r.ev_interval?r.ev_interval[0]:0)>=parseFloat(f.strength));
 const stale=freshness(S().observed_at)!=="LIVE";
 return[freshBanner(),cfbBanner(),card(el("h2",{},"EV scanner"),el("p",{class:"small"},badge("WATCH")," Prices that beat the market-maker consensus after vig removal (least favourable of three methods) and model uncertainty. The strategy is NOT validated: these are paper candidates, never official plays."+(stale?" Prices are not live: re-check before acting on anything.":"")),
  filters({market:1,book:1,strength:1}),
  el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Game"),el("th",{},"Bet"),el("th",{},"Book"),el("th",{class:"num"},"Price"),el("th",{class:"num"},"EV (range)"))),
   el("tbody",{},rows.map(r=>el("tr",{class:"game"+(stale?" dim":""),on:{click:()=>openGame(r.game_id)}},el("td",{},r.away+" @ "+r.home,el("div",{class:"mute small"},local(r.kickoff))),
    el("td",{},MK[r.market]+" "+r.selection+(r.line!=null?" "+ln(r.line):"")),el("td",{},r.book),el("td",{class:"num"},am(r.american)),
    el("td",{class:"num"},pct(r.ev_per_unit),el("div",{class:"mute small"},r.ev_interval?pct(r.ev_interval[0])+"–"+pct(r.ev_interval[1]):""))))))),
  rows.length?null:el("p",{class:"mute"},"No candidates match."))]};
R.sims=()=>[card(el("h2",{},"SIMS"),el("p",{},badge(D.sims.status,"UNAVAILABLE")," "+D.sims.detail))];
R.performance=()=>{const out=[];for(const[k,s]of Object.entries(D.sports)){const h=s.performance;if(!h){out.push(card(el("h3",{},s.label),el("p",{class:"mute"},"No analysis yet.")));continue}
 const brk=(o,t)=>Object.keys(o||{}).length?el("div",{},el("h3",{},t),el("table",{},el("tbody",{},Object.entries(o).map(([kk,v])=>el("tr",{},el("td",{},kk),el("td",{class:"num"},v?v.n+" opp / "+v.games+" games":"–"),el("td",{class:"num"},v?pct(v.mean)+" ("+pct(v.lo)+" to "+pct(v.hi)+")":"–")))))):null;
 out.push(card(el("h3",{},s.label),perfLine(h),el("p",{class:"small mute"},"Closing EV = the price taken, evaluated at the observed closing consensus. Opportunities are counted once, at first sighting; a game whose close was not observed is excluded, never filled in. Confidence intervals are clustered by game."),
  brk(h.by_book,"By book"),brk(h.by_market,"By market"),brk(h.by_hours_to_kickoff,"By hours to kickoff")))}
 out.push(card(el("h3",{},"Realized results"),el("p",{class:"mute"},"Graded results appear once final scores are ingested (NFL: nflverse). No strategy has passed the evidence gate.")));
 return[card(el("h2",{},"Performance center"),el("p",{class:"small"},"Proof standard: positive closing EV with a confidence interval above zero over enough independent games, then walk-forward and multiple-testing checks. Until then every strategy stays WATCH/PAPER.")),...out]};
R.health=()=>{const c=D.collection;return[card(el("h2",{},"Collection health"),
 el("table",{},el("tbody",{},el("tr",{},el("td",{},"V4 credits this month"),el("td",{class:"num"},c.v4_credits_this_month)),
  el("tr",{},el("td",{},"Requests this month"),el("td",{class:"num"},c.requests_this_month)),
  el("tr",{},el("td",{},"Provider credits remaining (shared with V3)"),el("td",{class:"num"},c.provider_remaining==null?"–":c.provider_remaining.toLocaleString())),
  el("tr",{},el("td",{},"Cost mismatches vs estimate"),el("td",{class:"num"},c.cost_mismatches)),
  el("tr",{},el("td",{},"Data anomalies"),el("td",{class:"num"},c.anomalies)))),
 el("h3",{},"Fetches"),el("table",{},el("tbody",{},(c.fetches||[]).map(x=>el("tr",{},el("td",{},x.sport),el("td",{class:"num"},x.fetches+" fetches"),el("td",{class:"small"},"last "+local(x.last)+" ("+fmtAge(ageMin(x.last))+" ago)"))))),
 el("p",{class:"small mute"},"Page built "+local(D.generated_at)+(D.commit?" from commit "+D.commit:"")+". Freshness labels are recomputed in your browser every minute.")),
 ...Object.entries(D.sports).map(([k,s])=>s.book&&s.book.audit?card(el("h3",{},badge("MODELED"),"Synthetic book audit: "+s.label),
  el("table",{class:"small"},el("tbody",{},...["population_id","population_hash","params_hash","engine_version","run_id","ledger_hash","wagers","arrivals","advanced_to","market_source"].map(f=>el("tr",{},el("td",{},f),el("td",{style:"word-break:break-all"},String(s.book.audit[f]))))))):null)]};
R.game=(id)=>{const g=gameById(id);if(!g)return[card(el("p",{},"Game not found (it may have kicked off)."),el("a",{href:"#odds"},"Back"))];
 const sp=Object.entries(D.sports).find(([k,s])=>(s.games||[]).includes(g));const s=sp[1];
 const bkg=((s.book||{}).games||[]).find(x=>x.event_id===id);
 const out=[el("p",{},el("a",{href:"#"+(st.prev||"odds")},"← Back")),card(el("h2",{},gameHead(g)),el("p",{class:"mute"},local(g.kickoff)+" · "+s.label+" · observed "+fmtAge(ageMin(s.observed_at))+" ago ",badge(freshness(s.observed_at))),
  g.rankings?el("p",{class:"small"},g.rankings.status==="OK"?"AP poll week "+g.rankings.poll_week+" ("+g.rankings.season_type+"), fetched "+local(new Date(g.rankings.fetched_at*1000).toISOString())+", source "+g.rankings.source:
   [badge(g.rankings.status==="PENDING"?"PENDING":"UNAVAILABLE",g.rankings.status==="PENDING"?"WATCH":"UNAVAILABLE")," "+(g.rankings.reason||""),
    g.rankings.provisional?el("span",{class:"mute"}," Provisional (current poll week "+g.rankings.provisional.poll_week+"): "+g.away+" "+(g.rankings.provisional.away_rank?"#"+g.rankings.provisional.away_rank:"NR")+", "+g.home+" "+(g.rankings.provisional.home_rank?"#"+g.rankings.provisional.home_rank:"NR")+"."):null]):null)];
 for(const m of["spreads","totals","h2h"]){const x=g.markets[m]||{};const sides=m==="totals"?["over","under"]:["home","away"];
  const rows=Object.entries(x.books||{}).map(([b,q])=>el("tr",{class:q.fresh?"":"dim"},el("td",{},b,(x.consensus_books||[]).includes(b)?el("span",{class:"mute small"}," (consensus)"):null),
   ...sides.map(sd=>el("td",{class:"num"},q[sd]?(m==="h2h"?"":ln(q[sd][1])+" ")+am(q[sd][0]):"–")),el("td",{class:"num small mute"},fmtAge(q.age_s/60))));
  out.push(card(el("h3",{},badge("OBSERVED"),MK[m]+(x.line!=null?" (consensus line "+(m==="spreads"?"home "+ln(x.line):x.line)+")":"")),
   x.status==="OK"?el("p",{class:"small"},"Fair: "+sides.map(sd=>sd+" "+pct(x.fair[sd])).join(", ")+" · basis "+x.basis):el("p",{class:"mute"},x.status),
   el("div",{class:"wrap"},el("table",{},el("thead",{},el("tr",{},el("th",{},"Book"),...sides.map(sd=>el("th",{class:"num"},sd)),el("th",{class:"num"},"quote age at observation"))),el("tbody",{},rows)))))}
 const scan=(s.scanner||[]).filter(r=>r.game_id===id);if(scan.length)out.push(card(el("h3",{},badge("WATCH"),"EV candidates"),...scan.map(r=>el("p",{class:"small"},MK[r.market]+" "+r.selection+(r.line!=null?" "+ln(r.line):"")+" at "+r.book+" "+am(r.american)+": EV "+pct(r.ev_per_unit)+" (range "+pct(r.ev_interval[0])+"–"+pct(r.ev_interval[1])+"), paper only"))));
 const sh=(s.sharp||[]).filter(r=>r.event_id===id);if(sh.length)out.push(card(el("h3",{},badge("EXPERIMENTAL"),"Sharp money"),...sh.map(r=>el("p",{class:"small"},MK[r.market]+": "+r.status+(r.suspected_side?" ("+r.suspected_side+")":"")))));
 if(bkg){const rows=Object.entries(bkg.splits).filter(([m,r])=>r.status==="OK");out.push(card(el("h3",{},badge("MODELED"),"Synthetic book on this game"),
  rows.length?rows.map(([bm,r])=>el("div",{},el("b",{},MK[BMK[bm]]," ",r.sample==="LOW"?badge("LOW SAMPLE","LOW"):null),...Object.entries(r.sides).map(([sd,v])=>el("div",{},el("span",{class:"small"},sd),splitBar(v,(bkg.bands[bm]||{})[sd]))))):el("p",{class:"mute"},"No modeled wagers yet."),
  bkg.need&&bkg.need.spread?el("p",{class:"small"},"Spread Need (liability): "+(bkg.need.spread.financial_need_side||"balanced")):null))}
 return out};
function render(){const h=location.hash.slice(1);let sec=h||"overview",gid=null;if(h.startsWith("game/")){gid=h.slice(5);sec="game"}
 if(sec!=="game"&&!R[sec])sec="overview";if(sec!=="game")st.prev=sec;st.sec=sec;
 for(const b of document.querySelectorAll("#sport button"))b.classList.toggle("on",b.dataset.s===st.sport);
 const nav=document.getElementById("nav");nav.replaceChildren(...SECTIONS.map(([k,t])=>el("a",{href:"#"+k,class:k===sec?"on":""},t)));
 const s=S();const f=freshness(s.observed_at);document.getElementById("fresh").replaceChildren(badge(f),el("span",{class:"mute"},s.observed_at?fmtAge(ageMin(s.observed_at))+" old":"no data"));
 const main=document.getElementById("main");const y=window.scrollY;main.replaceChildren(...(sec==="game"?R.game(gid):R[sec]()).filter(Boolean));
 if(document.activeElement&&document.activeElement.type==="search"){}else window.scrollTo(0,y)}
document.getElementById("sport").addEventListener("click",e=>{const s=e.target.dataset&&e.target.dataset.s;if(!s)return;st.sport=s;
 try{localStorage.setItem("v4sport",s)}catch(e){}if(location.hash.startsWith("#game/"))location.hash=st.prev||"overview";else render()});
window.addEventListener("hashchange",()=>{render();window.scrollTo(0,0)});setInterval(()=>{if(document.activeElement&&document.activeElement.tagName==="INPUT")return;render()},60000);render();
</script></body></html>"""


def render_html(snapshot):
    data = json.dumps(snapshot, separators=(",", ":"), default=str)
    data = data.replace("<", "\\u003c")      # no markup at all inside the data block (JSON.parse restores it)
    return PAGE.replace("__DATA__", data)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", help="directory of quotes_*.jsonl.gz shards (searched recursively)")
    ap.add_argument("--live-db", help="an existing market store instead of --shards")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--rankings-dir", default=None)
    ap.add_argument("--out", default="site")
    ap.add_argument("--bettors", type=int, default=10_000)
    ap.add_argument("--now", default=None, help="ISO time to build as of (default: current time)")
    a = ap.parse_args(argv)
    now = _epoch(a.now) if a.now else time.time()
    if a.live_db:
        conn, n_shards = sqlite3.connect(f"file:{a.live_db}?mode=ro", uri=True), None
    else:
        conn, _counts, n_shards = rebuild_store(a.shards)
    ledger_rows = [json.loads(x) for x in open(a.ledger) if x.strip()] if a.ledger and os.path.exists(a.ledger) else []
    os.makedirs(a.out, exist_ok=True)
    bpath = os.path.join(a.out, "synthetic_book.db")
    if os.path.exists(bpath):
        os.remove(bpath)
    bconn = sqlite3.connect(bpath)
    ensure_schema(bconn)
    snap = build_snapshot(conn, ledger_rows, load_rankings(a.rankings_dir), now, bconn, a.bettors,
                          commit=(os.environ.get("GITHUB_SHA") or "")[:7] or None)
    snap["shards"] = n_shards
    bconn.close()
    with open(os.path.join(a.out, "site_snapshot.json"), "w") as fh:
        json.dump(snap, fh, separators=(",", ":"))
    with open(os.path.join(a.out, "index.html"), "w") as fh:
        fh.write(render_html(snap))
    s = snap["sports"]
    print(f"site built: NFL {len(s['nfl']['games'])} games, CFB {len(s['cfb']['games'])} games, "
          f"scanner {len(s['nfl']['scanner']) + len(s['cfb']['scanner'])} rows, "
          f"book NFL {s['nfl']['book']['status']} / CFB {s['cfb']['book']['status']}")


if __name__ == "__main__":
    main()
