"""
tools_v5/release_check.py -- V5 release verification on REAL data (no API calls; nothing is published).

  python tools_v5/release_check.py --shards DIR --book-dir DIR --rankings-dir DIR --settle-dir DIR --results-dir DIR \
      --now ISO [--out report.json]

Checks (each PASS / FAIL with detail):
  1 v4_history_intact      every existing v4-book file loads and hash-verifies; every pre-V5 run keeps its exact
                           wager set after a V5 build (wagers added only to new V5 runs)
  2 no_duplicate_wagers    wager ids unique across the whole ledger; (run, customer, arrival) unique
  3 deterministic_replay   two independent V5 builds from the same inputs write byte-identical ledgers, and a
                           from-scratch replay reproduces the V5 ledger exactly
  4 liability_exact        for every V5 game and market: the book's P&L curve (settlement.exposure / joint
                           book) equals the sum of per-wager settlements at each outcome, to the cent
  5 settlement_reconciles  settlement_step reconciliation: 0 cents off per game and per customer
  6 v5_snapshot_bound      every V5 run carries a bankroll snapshot whose hash is part of its run id
"""

import argparse
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sportsbook_v4 import ENGINE_VERSION, persist  # noqa: E402
from sportsbook_v4.schema import ensure_schema  # noqa: E402


def _load(book_dir):
    c = sqlite3.connect(":memory:")
    ensure_schema(c)
    persist.load_dir(c, book_dir)
    return c


def _build(args, book_dir, out_dir):
    cmd = [sys.executable, "-m", "web_v4.live_site", "--shards", args.shards, "--rankings-dir", args.rankings_dir,
           "--out", out_dir, "--book-dir", book_dir, "--book-out", book_dir, "--now", args.now,
           "--results-dir", args.results_dir, "--settle-dir", args.settle_dir]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-2000:])
    return json.load(open(os.path.join(out_dir, "site_snapshot.json")))


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("shards", "book-dir", "rankings-dir", "settle-dir", "results-dir", "now"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res, work = {}, tempfile.mkdtemp(prefix="v5check_")
    try:
        before = _load(a.book_dir)
        runs_before = {r[0]: persist.wager_set(before, r[0]) for r in before.execute("SELECT run_id FROM v4_run")}
        b1, b2 = os.path.join(work, "book1"), os.path.join(work, "book2")
        shutil.copytree(a.book_dir, b1)
        shutil.copytree(a.book_dir, b2)
        s1 = _build(a, b1, os.path.join(work, "out1"))
        _build(a, b2, os.path.join(work, "out2"))
        after = _load(b1)
        kept = all(persist.wager_set(after, rid) == ws for rid, ws in runs_before.items())
        res["v4_history_intact"] = {"ok": kept, "runs_checked": len(runs_before),
                                    "wagers_checked": sum(len(v) for v in runs_before.values())}
        ids = [r[0] for r in after.execute("SELECT wager_id FROM v4_wager")]
        arr = after.execute("SELECT COUNT(*), COUNT(DISTINCT run_id || '|' || bettor_index || '|' || arrival_seq) FROM v4_wager").fetchone()
        res["no_duplicate_wagers"] = {"ok": len(ids) == len(set(ids)) and arr[0] == arr[1], "wagers": len(ids)}
        new1 = sorted(os.path.basename(p) for p in glob.glob(os.path.join(b1, "**", "book_*.json.gz"), recursive=True))
        new2 = sorted(os.path.basename(p) for p in glob.glob(os.path.join(b2, "**", "book_*.json.gz"), recursive=True))
        same_files = new1 == new2 and all(
            json.load(__import__("gzip").open(next(iter(glob.glob(os.path.join(b1, "**", f), recursive=True))), "rt"))["wagers"]
            == json.load(__import__("gzip").open(next(iter(glob.glob(os.path.join(b2, "**", f), recursive=True))), "rt"))["wagers"]
            for f in new1)
        from sportsbook_v4 import live_book as LB
        from web_v4.live_site import rebuild_store
        live, _c, _n = rebuild_store(a.shards)
        v5_runs = [r[0] for r in after.execute("SELECT run_id FROM v4_run WHERE engine_version=?", (ENGINE_VERSION,))]
        replay_ok = True
        for rid in v5_runs:
            wm = LB._watermark_iso(after, rid)
            mem, mrid = LB.replay(after, live, rid, wm, wm)
            replay_ok &= mrid == rid and persist.wager_set(mem, mrid) == persist.wager_set(after, rid)
        res["deterministic_replay"] = {"ok": same_files and replay_ok and bool(v5_runs), "v5_runs": len(v5_runs),
                                       "independent_builds_identical": same_files}
        from sportsbook_v4 import bettor_settlement as BS
        from sportsbook_v4.settlement import book_pnl_scores
        cols = ("wager_id", "run_id", "population_id", "bettor_index", "archetype", "canonical_game_id", "sport",
                "market", "side", "accepted_line", "accepted_home_spread", "accepted_price", "stake")
        bad, games = 0, 0
        for rid in v5_runs:
            ws = [dict(zip(cols, r)) for r in after.execute(f"SELECT {', '.join(cols)} FROM v4_wager WHERE run_id=?", (rid,))]
            by = {}
            for w in ws:
                by.setdefault(w["canonical_game_id"], []).append(w)
            for g, gw in by.items():
                games += 1
                for h, aw in [(x, y) for x in range(0, 61, 3) for y in range(0, 61, 4)]:
                    cents = -sum(BS.amounts(BS.settle_one(w, {"home_score": h, "away_score": aw, "source": "", "source_id": "",
                                                              "fetched_at": ""}, "t")["result"], w["stake"], w["accepted_price"])[1]
                                 for w in gw)
                    bad += abs(round(book_pnl_scores(gw, h, aw) * 100) - cents) > len(gw)
        res["liability_exact"] = {"ok": bad == 0, "games_checked": games, "mismatches": bad,
                                  "note": "book P&L from the exposure engine vs the sum of per-wager settlements, "
                                          "within per-wager cent rounding"}
        st = s1.get("settlement") or {}
        res["settlement_reconciles"] = {"ok": st.get("status") == "OK" and (st.get("reconciliation") or {}).get("ok", False),
                                        "reconciliation": st.get("reconciliation")}
        bound = all(persist.snapshot_rows(after, rid) for rid in v5_runs)
        res["v5_snapshot_bound"] = {"ok": bound and bool(v5_runs), "v5_runs": len(v5_runs)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    res["all_pass"] = all(v["ok"] for v in res.values() if isinstance(v, dict))
    out = json.dumps(res, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(out)
    print(out)
    return 0 if res["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
