"""
need_v6/freeze.py -- immutable pre-game NEED records and their grading.

A frozen record is written ONCE per (sport, event, market) into the week's v4-need release folder as
v6need_<sport>_<event>_<market>.json.gz. If a record already exists (in the saved release or this build's output)
it is never rewritten; later information can never revise a frozen selection. Grading uses the exact frozen side,
number and price.
"""

import glob
import gzip
import json
import os
import time

from need_v6 import VERSION
from sportsbook_v4 import settlement as ST
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.util import to_epoch

PREFIX = "v6need_"         # must NOT start with "need_": the Need tracker and seal job load need_*.json.gz
WRITE_BEFORE_FINAL_S = 3 * 3600      # no NFL/CFB game is final within 3 h of kickoff


def record_name(sport, event_id, market):
    return f"{PREFIX}{sport}_{event_id}_{market}.json.gz"


def release_tag(kickoff_iso):
    t = time.gmtime(to_epoch(kickoff_iso))
    return "v4-need-" + time.strftime("%G-W%V", t)


def existing(need_dir):
    """{file name: record} of every saved v6 frozen record."""
    out = {}
    for p in glob.glob(os.path.join(need_dir or "", "**", PREFIX + "*.json.gz"), recursive=True):
        try:
            out[os.path.basename(p)] = json.load(gzip.open(p, "rt"))
        except (OSError, ValueError):
            continue
    return out


def write_new(cards_frozen, sport, need_dir, out_dir, now):
    """Write frozen cards whose freeze time has passed and that have no record yet. Returns written names."""
    have = existing(need_dir)
    have.update(existing(out_dir))
    written = []
    for (event_id, market), card in sorted(cards_frozen.items()):
        if not card or to_epoch(card["kickoff"]) > now:
            continue                      # freeze only once the game is past its freeze point and kicked off
        name = record_name(sport, event_id, market)
        if name in have:
            continue
        rec = {"format": "need_v6_frozen_v1", "version": VERSION, "sport": sport, "frozen_at": card["as_of"],
               "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
               # True only if written before any final score could exist; late-written records (e.g. games that had
               # already finished when v6 was first switched on) are kept but never count as prospective validation
               "written_before_final": now - to_epoch(card["kickoff"]) <= WRITE_BEFORE_FINAL_S, "card": card}
        folder = os.path.join(out_dir, release_tag(card["kickoff"]))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        if os.path.exists(path):
            continue
        with gzip.open(path, "wt") as f:
            json.dump(rec, f, sort_keys=True, default=str)
        written.append(name)
    return written


def grade_record(rec, final):
    """{type: {side, point, price, result, units}} for official / financial / desk at the frozen terms."""
    c = rec["card"]
    out = {}
    for typ, blk in (("official", c["arbitredge_need"]), ("financial", c["financial_need"]), ("desk", c["trading_desk_need"])):
        side = blk.get("side")
        if side is None:
            out[typ] = None
            continue
        off = (c["book"]["offers"] or {}).get(side) or {}
        point, price = off.get("point"), off.get("price")
        if point is None and c["book"].get("posted_line") is not None:
            pl = c["book"]["posted_line"]
            point = pl if (c["market"] == "total" or side == "home") else -pl
        if final is None or point is None:
            out[typ] = {"side": side, "point": point, "price": price, "result": "NO_RESULT", "units": None}
            continue
        hs, as_ = final
        r = ST.wager_result(side, point, hs - as_) if c["market"] == "spread" else ST.total_result(side, point, hs + as_)
        units = 0.0 if r == "push" else (profit_on_win(1.0, price) if r == "win" else -1.0) if price else None
        out[typ] = {"side": side, "point": point, "price": price, "result": r, "units": units}
    return out


def validation(records, finals):
    """Per NEED type: selections, record, ROI at frozen price, NONE count. records: [frozen record].
    Only records written before a final could exist count; the number excluded is reported."""
    agg = {}
    late = sum(1 for r in records if not r.get("written_before_final", False))
    for rec in records:
        if not rec.get("written_before_final", False):
            continue
        c = rec["card"]
        g = grade_record(rec, finals.get((rec["sport"], c["event_id"])))
        for typ, x in g.items():
            a = agg.setdefault(typ, {"game_markets": 0, "selections": 0, "none": 0, "w": 0, "l": 0, "p": 0,
                                     "units": 0.0, "risk": 0, "pending": 0})
            a["game_markets"] += 1
            if x is None:
                a["none"] += 1
                continue
            a["selections"] += 1
            if x["result"] == "NO_RESULT":
                a["pending"] += 1
                continue
            a[{"win": "w", "loss": "l", "push": "p"}[x["result"]]] += 1
            if x["units"] is not None and x["result"] != "push":
                a["units"] += x["units"]
                a["risk"] += 1
    for a in agg.values():
        a["record"] = f"{a['w']}-{a['l']}-{a['p']}"
        a["roi_at_frozen_price"] = round(a["units"] / a["risk"], 4) if a["risk"] else None
        a["units"] = round(a["units"], 3)
    agg["excluded_late_written"] = late
    return agg
