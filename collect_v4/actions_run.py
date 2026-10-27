"""
collect_v4/actions_run.py -- one collection pass inside GitHub Actions.

Everything a run needs is passed as files; nothing persists on the runner:
  STATE_IN/ledger.jsonl            credit ledger (every request V4 has made)
  STATE_IN/collector_state.json    last fetch time + known kickoffs per sport
  OUT/                             new quote shard(s) + updated ledger/state

The credit gate is the same code as the server/Mac gateway (credits.py via
odds_gateway.Gateway), run in-process: the V4 monthly cap, 24 h cap,
per-request ceiling and the V3 floor (from the provider's own
x-requests-remaining header) are checked BEFORE every request.

Defence in depth: nothing is requested unless V4_LIVE == "on" (the owner's
repository variable) -- the workflow also skips the whole job otherwise.

  python -m collect_v4.actions_run --state-in S --out O [--probe]
"""

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "infra", "devhost", "gateway"))
import odds_gateway as og  # noqa: E402

from collect_v4 import schedule_policy  # noqa: E402
from collect_v4.collector import BOOKS, MARKETS, SPORTS, Collector  # noqa: E402
from edge_v4.odds_client import GatewayRefused  # noqa: E402

HARD_MONTHLY_MAX = 6000            # owner-approved V4 ceiling; variables can never exceed it
LEDGER_COLS = ("ts", "app", "endpoint", "fingerprint", "est_cost", "actual_cost", "hdr_used", "hdr_remaining",
               "status", "note")


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class InProcessClient:
    """Same interface as edge_v4.odds_client.OddsClient, backed by an in-process Gateway."""

    def __init__(self, gw):
        self.gw = gw

    def request(self, endpoint, sport=None, params=None, event_id=None, purpose="", cache_ttl_s=0):
        try:
            status, body = self.gw.handle({"endpoint": endpoint, "sport": sport, "event_id": event_id,
                                           "params": params or {}, "purpose": purpose, "cache_ttl_s": cache_ttl_s})
        except og.Refused as e:
            raise GatewayRefused(e.status, {"refused": str(e)})
        if status != 200:
            raise GatewayRefused(status, body)
        return body

    def status(self):
        return self.gw.status()


def caps_from_env(env):
    def num(name, default):
        try:
            return int(env.get(name) or default)
        except ValueError:
            return default
    monthly = min(num("V4_MONTHLY_CAP", 500), HARD_MONTHLY_MAX)
    return {"v4_monthly_cap": max(0, monthly), "v4_daily_cap": num("V4_DAILY_CAP", 60),
            "max_single_request": min(num("V4_MAX_REQUEST", 10), 30),
            "v3_floor_remaining": max(num("V4_V3_FLOOR", 2000), 2000)}     # the floor can be raised, never lowered


def load_ledger(gw, path):
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            gw.conn.execute(f"INSERT INTO credit_ledger ({', '.join(LEDGER_COLS)}) VALUES ({', '.join('?' * len(LEDGER_COLS))})",
                            [r.get(c) for c in LEDGER_COLS])
            n += 1
    gw.conn.commit()
    return n


def dump_ledger(gw, path):
    rows = gw.conn.execute(f"SELECT {', '.join(LEDGER_COLS)} FROM credit_ledger ORDER BY id").fetchall()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(dict(zip(LEDGER_COLS, r)), sort_keys=True) + "\n")
    os.replace(tmp, path)
    return len(rows)


def export_shard(conn, path):
    """All rows of this run's quote store -> gzip JSON lines (fetch rows, quotes, anomalies)."""
    n = 0
    with gzip.open(path, "wt") as f:
        for table in ("mkt_fetch", "mkt_quote", "mkt_anomaly"):
            cur = conn.execute(f"SELECT * FROM {table}")
            cols = [d[0] for d in cur.description]
            for row in cur:
                rec = dict(zip(cols, row))
                rec.pop("id", None)
                f.write(json.dumps({"t": table, **rec}, sort_keys=True) + "\n")
                n += 1
    return n


def run(state_in, out, env=os.environ, clock=time.time, opener=None, tier=None, sports=SPORTS, probe=False):
    """tier: None -> read V4_TIER from env. Resolved and validated BEFORE anything else,
    so a bad setting can never reach the API or the scheduler."""
    os.makedirs(out, exist_ok=True)
    tier, tier_source = schedule_policy.resolve_tier(env.get("V4_TIER") if tier is None else tier)
    work = os.path.join(out, ".work")
    os.makedirs(work, exist_ok=True)
    summary = {"at": _iso(clock()), "tier": tier, "tier_source": tier_source, "fetches": [], "skipped": [],
               "refused": []}
    if env.get("V4_LIVE") != "on":
        summary["status"] = "live collection is OFF (repository variable V4_LIVE != 'on'); nothing requested"
        _write_summary(out, summary)
        return summary
    caps = caps_from_env(env)
    cfg = dict(og.DEFAULT_CONFIG, state_dir=work, live_enabled=True, socket_group=None, **caps)
    key = og.load_key()
    if key is None:
        summary["status"] = "no API key available to this job (secret missing)"
        _write_summary(out, summary)
        return summary
    gw = og.Gateway(cfg, key, clock=clock, opener=opener)
    summary["ledger_rows_loaded"] = load_ledger(gw, os.path.join(state_in, "ledger.jsonl"))
    summary["caps"] = caps
    state_path = os.path.join(state_in, "collector_state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}

    if probe:
        summary["probe"] = gw.probe()          # GET /v4/sports: documented cost 0 credits
    else:
        client = InProcessClient(gw)
        col = Collector(client, os.path.join(work, "data"), clock=clock, sports=sports, books=BOOKS, markets=MARKETS)
        for sport in sports:
            st = state.setdefault(sport, {})
            is_due, why = schedule_policy.due(clock(), st.get("kickoffs", []), st.get("odds_at"), tier)
            if not is_due:
                summary["skipped"].append({"sport": sport, "reason": why})
                continue
            try:
                r = col.fetch_odds(sport)
            except GatewayRefused as e:
                summary["refused"].append({"sport": sport, "status": e.status, "detail": str(e)})
                continue
            kicks = sorted({k for (k,) in col.conn.execute(
                "SELECT DISTINCT commence FROM mkt_quote WHERE sport=?", (sport,))})
            st.update(odds_at=clock(), kickoffs=[k for k in kicks if k > clock()][:400], last_reason=why)
            summary["fetches"].append({**r, "reason": why})
            # persist the spend IMMEDIATELY: if anything later in this run fails, the
            # workflow's always-run save step still uploads a ledger containing this request
            _persist(gw, state, out)
        if summary["fetches"]:
            shard = os.path.join(out, f"quotes_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(clock()))}.jsonl.gz")
            summary["shard"] = os.path.basename(shard)
            summary["shard_rows"] = export_shard(col.conn, shard)
    summary["ledger_rows"] = _persist(gw, state, out)
    summary["usage"] = gw.status()["usage"]
    summary["status"] = "ok"
    _write_summary(out, summary)
    shutil.rmtree(work, ignore_errors=True)
    return summary


def _persist(gw, state, out):
    tmp = os.path.join(out, "collector_state.json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(out, "collector_state.json"))
    return dump_ledger(gw, os.path.join(out, "ledger.jsonl"))


def _write_summary(out, summary):
    with open(os.path.join(out, "run_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True, default=str)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-in", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--tier", default=None,
                    help="override V4_TIER; unset/empty means 'test'. Unknown values stop the run.")
    a = ap.parse_args(argv)
    try:
        s = run(a.state_in, a.out, tier=a.tier, probe=a.probe)
    except schedule_policy.InvalidTier as e:
        print(f"::error::{e}")
        return 2
    printable = {k: v for k, v in s.items() if k != "probe"}
    if "probe" in s:
        printable["probe"] = {k: s["probe"].get(k) for k in ("provider_status", "usage", "provider_error_code")}
    print(json.dumps(printable, indent=2, default=str))
    return 0 if s.get("status", "").startswith(("ok", "live collection is OFF")) else 1


if __name__ == "__main__":
    sys.exit(main())
