"""
collect_v4/backfill_run.py -- one owner-started historical backfill pass inside GitHub Actions.

Started ONLY by the owner (odds-collect -> Run workflow -> mode backfill-pilot / backfill-all). Every paid
request goes through the same in-process gateway as live collection: balance preflight (fails closed),
V3 floor, per-request ceiling, breaker, append-only ledger. Spending is limited by the one-time backfill
allowance (collect_v4.backfill.BACKFILL_HARD_MAX, expiring BACKFILL_EXPIRES) and the owner's repository
variable V4_BACKFILL_BUDGET; live collection's monthly and daily caps are untouched.

Progress is saved after every snapshot (state_in/backfill_state.json -> out/), so a run that stops (time
limit, allowance used up, provider refusal) is continued by the next press without repeating anything.

  python -m collect_v4.backfill_run --state-in S --out O --scope pilot|all
"""

import argparse
import dataclasses
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "infra", "devhost", "gateway"))
import odds_gateway as og  # noqa: E402

from collect_v4 import backfill as B  # noqa: E402
from collect_v4.actions_run import (InProcessClient, _write_summary, dump_ledger, export_shard,  # noqa: E402
                                    load_ledger, load_rankings)
# The one-time historical backfill keeps the book list it was approved and run with (live collection swapped
# lowvig for circasports on 2026-11-12), so any further backfill pages match the ones already fetched.
BOOKS = ("pinnacle", "betonlineag", "lowvig", "draftkings", "fanduel", "betmgm",
         "williamhill_us", "betrivers", "espnbet", "fanatics")
from edge_v4.odds_client import GatewayRefused  # noqa: E402
from market_v4 import normalize, store  # noqa: E402

TIME_BUDGET_S = 35 * 60          # stop cleanly well inside the job's time limit; the next press continues
SCOPES = ("pilot", "all")


def historical_spend(conn, since):
    """Credits spent on historical endpoints since `since` (from the ledger)."""
    return conn.execute("SELECT COALESCE(SUM(COALESCE(actual_cost, est_cost)),0) FROM credit_ledger "
                        "WHERE ts>=? AND endpoint LIKE 'historical%'", (since,)).fetchone()[0]


def allowance(env, conn, now):
    """(remaining credits, reason). Zero outside the allowance period or when the owner has not set a budget."""
    if now >= B.epoch(B.BACKFILL_EXPIRES):
        return 0, f"the one-time backfill allowance expired {B.BACKFILL_EXPIRES}"
    try:
        budget = int(env.get("V4_BACKFILL_BUDGET") or 0)
    except ValueError:
        budget = 0
    budget = max(0, min(budget, B.BACKFILL_HARD_MAX))
    if budget == 0:
        return 0, "repository variable V4_BACKFILL_BUDGET is not set (or 0)"
    used = historical_spend(conn, B.epoch(B.BACKFILL_START))
    return max(0, budget - used), f"backfill budget {budget}, used {used}"


def backfill_caps(gate, env, remaining, now):
    """Caps for this backfill run: whatever V4 has already spent plus the remaining allowance (never more);
    per-request ceiling 30 (one 3-market historical snapshot, the existing hard maximum); V3 floor unchanged."""
    from credits import month_start
    month = gate._spent_since(month_start(now))
    day = gate._spent_since(now - 86400)
    try:
        floor = int(env.get("V4_V3_FLOOR") or 2000)
    except ValueError:
        floor = 2000
    return {"v4_monthly_cap": month + remaining, "v4_daily_cap": day + remaining, "max_single_request": 30,
            "v3_floor_remaining": max(floor, 2000)}


def _week_key(sport, ws):
    return f"{sport}|{B.iso(ws)}"


def fetch_snapshot(client, conn, sport, t):
    """One historical snapshot at or before t -> quote store. Returns (snapshot_epoch, n_quotes, events)."""
    params = {"bookmakers": ",".join(BOOKS), "markets": ",".join(B.HIST_MARKETS), "oddsFormat": "american",
              "dateFormat": "iso", "date": B.iso(t)}
    out = client.request("historical_odds", B.SPORT_KEYS[sport], params, purpose=f"historical backfill {B.iso(t)}")
    body = out.get("data") or {}
    ts_iso = body.get("timestamp")
    if not ts_iso:
        raise ValueError("historical response without a snapshot timestamp")
    snap_t = store.iso_to_epoch(ts_iso)
    fetched = B.iso(int(snap_t))
    quotes, anomalies = normalize.normalize(body, fetched, request_fp=f"hist:{sport}:{B.iso(t)}")
    quotes = [q for q in quotes if store.iso_to_epoch(q.commence_time) > snap_t]     # pre-game only
    n = store.ingest(conn, quotes, anomalies, fetched, B.SPORT_KEYS[sport], "historical_odds",
                     f"hist:{sport}:{B.iso(t)}")
    events = [{"event_id": e.get("id"), "home": e.get("home_team"), "away": e.get("away_team"),
               "kickoff": store.iso_to_epoch(e["commence_time"])}
              for e in (body.get("data") or []) if e.get("commence_time")]
    return snap_t, n, events


def run(state_in, out, scope, env=os.environ, clock=time.time, opener=None, sports=("nfl", "cfb")):
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, ".work")
    os.makedirs(work, exist_ok=True)
    start = clock()
    summary = {"mode": f"backfill-{scope}", "at": B.iso(start), "fetched": 0, "credits_est": 0, "weeks": {},
               "refused": []}
    if env.get("V4_LIVE") != "on":
        summary["status"] = "V4_LIVE is not 'on'; nothing requested"
        _write_summary(out, summary)
        return summary
    key = og.load_key()
    if key is None:
        summary["status"] = "no API key available to this job (secret missing)"
        _write_summary(out, summary)
        return summary
    cfg = dict(og.DEFAULT_CONFIG, state_dir=work, live_enabled=True, socket_group=None,
               v4_monthly_cap=0, v4_daily_cap=0, max_single_request=30, v3_floor_remaining=2000)
    gw = og.Gateway(cfg, key, clock=clock, opener=opener)
    summary["ledger_rows_loaded"] = load_ledger(gw, os.path.join(state_in, "ledger.jsonl"))
    remaining, why = allowance(env, gw.conn, clock())
    summary["allowance"] = {"remaining_at_start": remaining, "detail": why}
    caps = backfill_caps(gw.gate, env, remaining, clock())
    gw.gate.budget = dataclasses.replace(gw.gate.budget, **caps)
    summary["caps"] = caps
    state_path = os.path.join(state_in, "backfill_state.json")
    state = B.load_state(state_path)
    polls = B.poll_snapshots(load_rankings(os.path.join(state_in, "rankings")))
    conn = store.connect(os.path.join(work, "hist.db"))
    client = InProcessClient(gw)

    cs = os.path.join(state_in, "collector_state.json")
    if os.path.exists(cs):                 # live collector state passes through unchanged (state_save uploads it)
        shutil.copy(cs, os.path.join(out, "collector_state.json"))

    def persist():
        B.save_state(state, os.path.join(out, "backfill_state.json"))
        dump_ledger(gw, os.path.join(out, "ledger.jsonl"))

    stop = None if remaining >= 30 else why
    for sport in sports:
        wins = B.windows(sport)
        if scope == "pilot":
            wins = wins[:1] if sport == "nfl" else []
        for ws, we in wins:
            if stop:
                break
            wk = state["weeks"].setdefault(_week_key(sport, ws), {})
            try:
                if "plan" not in wk:
                    t0 = B.anchor_time(ws)
                    snap_t, n, events = fetch_snapshot(client, conn, sport, t0)
                    summary["fetched"] += 1
                    games = [e for e in events if ws <= e["kickoff"] < we]
                    if sport == "cfb":
                        games = B.top25_filter(games, polls) if polls else games
                    wk.update(anchor=B.iso(int(snap_t)), games=len(games),
                              plan=[B.iso(t) for t in B.plan_times(ws, we, [g["kickoff"] for g in games], start)],
                              done=[])
                    persist()
                for t_iso in wk["plan"]:
                    if t_iso in wk["done"]:
                        continue
                    if clock() - start > TIME_BUDGET_S:
                        stop = "time budget for this run reached; press Run again to continue"
                        break
                    fetch_snapshot(client, conn, sport, B.epoch(t_iso))
                    summary["fetched"] += 1
                    wk["done"].append(t_iso)
                    persist()
            except GatewayRefused as e:
                summary["refused"].append({"sport": sport, "week": B.iso(ws), "status": e.status, "detail": str(e)})
                stop = f"refused by the credit gate or provider: {e}"
            summary["weeks"][_week_key(sport, ws)] = {k: wk.get(k) if k != "done" else len(wk.get("done", []))
                                                      for k in ("anchor", "games", "done")} | \
                {"planned": len(wk.get("plan", [])) + 1}
    if summary["fetched"]:
        shard = os.path.join(out, f"hquotes_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(clock()))}.jsonl.gz")
        summary["shard"] = os.path.basename(shard)
        summary["shard_rows"] = export_shard(conn, shard)
    persist()
    summary["credits_spent_this_run"] = historical_spend(gw.conn, start)
    left, _ = allowance(env, gw.conn, clock())
    summary["allowance_remaining"] = left
    todo = sum(1 for w in state["weeks"].values() if "plan" not in w or len(w["done"]) < len(w["plan"]))
    all_weeks = sum(len(B.windows(s)) for s in sports)
    summary["complete"] = scope == "all" and not todo and len(state["weeks"]) >= all_weeks
    summary["status"] = "ok" + (f" (stopped: {stop})" if stop else "")
    _write_summary(out, summary)
    shutil.rmtree(work, ignore_errors=True)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-in", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scope", required=True, choices=SCOPES)
    a = ap.parse_args(argv)
    s = run(a.state_in, a.out, a.scope)
    print(json.dumps({k: v for k, v in s.items()}, indent=2, default=str))
    return 0 if str(s.get("status", "")).startswith(("ok", "V4_LIVE is not")) else 1


if __name__ == "__main__":
    sys.exit(main())
