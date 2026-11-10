"""One-time historical backfill: plan, separate allowance, credit gate, persistence, workflow wiring."""
import json
import os
import re
import time

import pytest

import odds_gateway as og
from collect_v4 import actions_run as AR
from collect_v4 import backfill as B
from collect_v4 import backfill_run as BR
from market_v4 import store

KEY = "".join(["z1", "x2", "c3", "v4"]) * 8
NOW = B.epoch("2026-11-11T15:00:00Z")
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class HistUpstream:
    """Fake provider: /v4/sports (free), /v4/historical/.../odds (10 x 3 markets = 30). Each snapshot lists two
    games of the requested week, kicking off Sunday 17:00 and Monday 00:20 UTC of that week."""

    def __init__(self, remaining=15000):
        self.calls, self.remaining = [], remaining

    def games(self, sport, t):
        ws, _ = [w for w in B.windows("nfl" if sport.endswith("nfl") else "cfb") if w[0] <= t < w[1]][0] \
            if t < B.epoch(B.LIVE_FROM) else (t, t)
        sun = ws + 5 * 86400 - 12 * 3600
        return [("a" * 31 + str(int(ws) % 7), sun + 17 * 3600), ("b" * 31 + str(int(ws) % 7), sun + 86400 + 1200)]

    def __call__(self, path, q):
        self.calls.append((path, dict(q)))
        cost = 0 if path == "/v4/sports" else 30
        self.remaining -= cost
        hdr = {"x-requests-last": str(cost), "x-requests-remaining": str(self.remaining),
               "x-requests-used": str(20000 - self.remaining)}
        if path == "/v4/sports":
            return 200, hdr, b"[]"
        assert path.startswith("/v4/historical/sports/")
        sport = path.split("/")[4]
        t = B.epoch(q["date"])
        snap = t - 120                                  # the provider's snapshot at or before `date`
        data = []
        for gid, kick in self.games(sport, t):
            bks = [{"key": b, "last_update": iso(snap - 60), "markets": [
                {"key": "spreads", "last_update": iso(snap - 60), "outcomes": [
                    {"name": "H", "price": -110, "point": -3.5}, {"name": "A", "price": -110, "point": 3.5}]},
                {"key": "totals", "last_update": iso(snap - 60), "outcomes": [
                    {"name": "Over", "price": -110, "point": 44.5}, {"name": "Under", "price": -110, "point": 44.5}]}]}
                   for b in ("pinnacle", "fanatics")]
            data.append({"id": gid, "sport_key": sport, "commence_time": iso(kick), "home_team": "H",
                         "away_team": "A", "bookmakers": bks})
        return 200, hdr, json.dumps({"timestamp": iso(snap), "previous_timestamp": iso(snap - 300),
                                     "next_timestamp": iso(snap + 300), "data": data}).encode()


def env(**kw):
    e = {"V4_LIVE": "on", "ODDS_KEY_ENV": "TEST_HIST_KEY", "TEST_HIST_KEY": KEY, "V4_BACKFILL_BUDGET": "12000"}
    e.update(kw)
    return e


def _run(tmp_path, monkeypatch, scope, state, e=None, up=None, clock=lambda: NOW, n=[0]):
    e = env() if e is None else e
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    n[0] += 1
    out = tmp_path / f"out{n[0]}"
    up = up or HistUpstream()
    s = BR.run(str(state), str(out), scope, env=e, clock=clock, opener=up, sports=("nfl", "cfb"))
    for f in ("ledger.jsonl", "backfill_state.json", "collector_state.json"):        # like the release assets
        if (out / f).exists():
            (state / f).write_text((out / f).read_text())
    return s, out, up


# ------------------------------------------------------------------ plan

def test_windows_cover_the_season_up_to_the_first_live_week():
    nfl, cfb = B.windows("nfl"), B.windows("cfb")
    assert len(nfl) == 7 and len(cfb) == 9
    assert iso(nfl[-1][1]) == iso(cfb[-1][1]) == B.LIVE_FROM
    assert all(time.gmtime(ws).tm_wday == 1 and time.gmtime(ws).tm_hour == 12 for ws, _ in nfl + cfb)


def test_plan_has_pick_and_close_snapshots_spaced_and_in_the_past():
    ws, we = B.windows("nfl")[2]
    sun = ws + 5 * 86400 - 12 * 3600
    kicks = [sun + 17 * 3600, sun + 20 * 3600 + 300, sun + 20 * 3600 + 1500, sun + 86400 + 1200]
    plan = B.plan_times(ws, we, kicks, NOW)
    assert plan == sorted(plan) and all(t < NOW for t in plan)
    assert all(b - a >= 5 * 60 for a, b in zip(plan, plan[1:]))
    for k in (sun + 17 * 3600, sun + 86400 + 1200):
        assert any(k - 3600 - 600 <= t <= k - 3600 + 600 for t in plan)     # final-pick snapshot
        assert any(k - 900 <= t < k for t in plan)                          # observed close
    assert B.plan_times(ws, we, [], NOW) == []
    assert B.plan_times(ws, we, kicks, ws) == []                          # never a snapshot from the future


def test_poll_week_k_ranks_cfb_week_k():
    polls = [{"season": 2026, "season_type": "regular", "week": w,
              "ranks": [{"rank": 1, "school": f"Team{w}"}]} for w in (1, 2, 9)]
    teams = [{"school": f"Team{w}", "alternate_names": []} for w in (1, 2, 9)] + \
            [{"school": "Other", "alternate_names": []}]
    snaps = B.poll_snapshots([{"fetched_at": NOW, "polls": polls, "teams": teams, "source": "CFBD"}])
    wk = {w: B.epoch(B.CFB_WEEK1_WINDOW) + 7 * 86400 * (w - 1) for w in range(0, 10)}
    sat = lambda w: wk[w] + 4 * 86400 + 6 * 3600                               # Saturday 18:00 UTC
    g = lambda home, w: {"home": home, "away": "Other", "kickoff": sat(w)}
    assert B.top25_filter([g("Team1", 0), g("Team1", 1), g("Team2", 2), g("Team9", 9)], snaps) == \
        [g("Team1", 0), g("Team1", 1), g("Team2", 2), g("Team9", 9)]
    assert B.top25_filter([g("Team9", 2), g("Team2", 9)], snaps) == []          # a later/earlier poll never applies


# ------------------------------------------------------------------ allowance

def test_allowance_requires_owner_budget_is_clamped_and_expires(tmp_path):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    import credits
    credits.CreditGate(conn)
    assert BR.allowance({}, conn, NOW)[0] == 0
    assert BR.allowance({"V4_BACKFILL_BUDGET": "abc"}, conn, NOW)[0] == 0
    assert BR.allowance({"V4_BACKFILL_BUDGET": "999999"}, conn, NOW)[0] == B.BACKFILL_HARD_MAX
    conn.execute("INSERT INTO credit_ledger (ts, app, endpoint, fingerprint, est_cost, actual_cost) VALUES "
                 "(?, 'v4', 'historical_odds', 'x', 30, 30)", (NOW - 60,))
    conn.execute("INSERT INTO credit_ledger (ts, app, endpoint, fingerprint, est_cost, actual_cost) VALUES "
                 "(?, 'v4', 'odds', 'y', 3, 3)", (NOW - 60,))
    assert BR.allowance({"V4_BACKFILL_BUDGET": "100"}, conn, NOW)[0] == 70          # live spend never counts
    assert BR.allowance({"V4_BACKFILL_BUDGET": "100"}, conn, B.epoch(B.BACKFILL_EXPIRES))[0] == 0


def test_no_budget_variable_means_no_paid_request(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    s, out, up = _run(tmp_path, monkeypatch, "pilot", state, e=env(V4_BACKFILL_BUDGET=""))
    assert s["fetched"] == 0 and not any(p.startswith("/v4/historical") for p, _ in up.calls)
    s, out, up = _run(tmp_path, monkeypatch, "pilot", state, e=env(V4_LIVE="off"))
    assert up.calls == []


# ------------------------------------------------------------------ fetching

def test_pilot_fetches_one_nfl_week_through_the_gate_and_persists(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    s, out, up = _run(tmp_path, monkeypatch, "pilot", state)
    hist = [(p, q) for p, q in up.calls if p.startswith("/v4/historical")]
    assert s["status"] == "ok" and s["fetched"] == len(hist) >= 3
    assert all(p == "/v4/historical/sports/americanfootball_nfl/odds" for p, _ in hist)
    assert all(q["markets"] == "h2h,spreads,totals" and len(q["bookmakers"].split(",")) == 10 for _, q in hist)
    # every paid request is preceded by the free balance check
    paths = [p for p, _ in up.calls]
    assert all(paths[i - 1] == "/v4/sports" for i, p in enumerate(paths) if p.startswith("/v4/historical"))
    ledger = [json.loads(x) for x in (state / "ledger.jsonl").read_text().splitlines()]
    assert sum(r["actual_cost"] or 0 for r in ledger if r["endpoint"] == "historical_odds") == 30 * len(hist)
    assert s["credits_spent_this_run"] == 30 * len(hist)
    shard = list(out.glob("hquotes_*.jsonl.gz"))
    assert len(shard) == 1 and not list(out.glob("quotes_*.jsonl.gz"))          # never a live odds-data shard
    import gzip
    rows = [json.loads(x) for x in gzip.open(shard[0], "rt")]
    fetch_times = {r["fetched_at"] for r in rows if r["t"] == "mkt_fetch"}
    asked = {B.epoch(q["date"]) - 120 for _, q in hist}
    assert fetch_times == asked                                                   # stored at the SNAPSHOT time
    assert all(r["commence"] > r["fetched_at"] for r in rows if r["t"] == "mkt_quote")
    # a second pilot press repeats nothing
    s2, _, up2 = _run(tmp_path, monkeypatch, "pilot", state)
    assert s2["fetched"] == 0 and not any(p.startswith("/v4/historical") for p, _ in up2.calls)


def test_stops_when_the_allowance_is_used_up_and_continues_later(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    s, _, _ = _run(tmp_path, monkeypatch, "all", state, e=env(V4_BACKFILL_BUDGET="150"))
    assert s["fetched"] == 5 and s["allowance_remaining"] == 0 and "stopped" in s["status"]
    s2, _, up2 = _run(tmp_path, monkeypatch, "all", state, e=env(V4_BACKFILL_BUDGET="300"))
    assert s2["fetched"] == 5
    done = json.loads((state / "backfill_state.json").read_text())
    dates = [q["date"] for p, q in up2.calls if p.startswith("/v4/historical")]
    assert len(dates) == len(set(dates)) == 5


def test_v3_floor_still_refuses(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    s, _, up = _run(tmp_path, monkeypatch, "pilot", state, up=HistUpstream(remaining=2020))
    assert s["fetched"] == 0 and s["refused"] and "V3 floor" in s["refused"][0]["detail"]


def test_complete_backfill_covers_every_week(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    s, _, _ = _run(tmp_path, monkeypatch, "all", state)
    assert s["complete"] and s["status"] == "ok"
    assert len(s["weeks"]) == len(B.windows("nfl")) + len(B.windows("cfb"))


def test_time_budget_stops_cleanly(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    t = {"now": NOW}

    def clock():
        t["now"] += 400                     # every call to the clock moves 400 s on
        return t["now"]
    monkeypatch.setattr(BR, "TIME_BUDGET_S", 3000)
    s, _, _ = _run(tmp_path, monkeypatch, "all", state, clock=clock)
    assert "time budget" in s["status"] and not s["complete"]


# ------------------------------------------------------------------ live collection is unaffected

def test_live_caps_exclude_backfill_spend(tmp_path):
    cfg = dict(og.DEFAULT_CONFIG, state_dir=str(tmp_path), live_enabled=True, socket_group=None,
               v4_monthly_cap=500, v4_daily_cap=60, max_single_request=10, v3_floor_remaining=2000)
    gw = og.Gateway(cfg, KEY, clock=lambda: NOW)
    caps = {"v4_monthly_cap": 500, "v4_daily_cap": 60, "max_single_request": 10, "v3_floor_remaining": 2000}
    assert AR.exclude_backfill(gw, dict(caps), NOW) == caps                       # no backfill: unchanged
    for dt, ep, c in ((3600, "historical_odds", 30), (7200, "historical_odds", 30), (90000, "historical_odds", 30),
                      (60, "odds", 3)):
        gw.conn.execute("INSERT INTO credit_ledger (ts, app, endpoint, fingerprint, est_cost, actual_cost) "
                        "VALUES (?, 'v4', ?, 'f', ?, ?)", (NOW - dt, ep, c, c))
    out = AR.exclude_backfill(gw, dict(caps), NOW)
    assert out["v4_monthly_cap"] == 590 and out["v4_daily_cap"] == 120
    assert gw.gate.budget.v4_monthly_cap == 590 and gw.gate.budget.v3_floor_remaining == 2000


# ------------------------------------------------------------------ workflow wiring

def _wf(name):
    return open(os.path.join(REPO, ".github", "workflows", name)).read()


def test_backfill_only_by_owner_dispatch_and_secret_unchanged():
    import yaml
    wf = yaml.safe_load(_wf("odds-collect.yml"))
    on = wf[True] if True in wf else wf["on"]
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["workflow_dispatch"]["inputs"]["mode"]["default"] == "collect"
    steps = wf["jobs"]["collect"]["steps"]
    secret_steps = [s.get("name") for s in steps if "secrets." in json.dumps(s)]
    assert secret_steps == ["Collect (credit-gated)"]
    run = [s for s in steps if s.get("name") == "Collect (credit-gated)"][0]["run"]
    assert "backfill-pilot) python3 -m collect_v4.backfill_run" in run and \
        "*)              python3 -m collect_v4.actions_run" in run


def test_state_save_keeps_backfill_out_of_live_odds_releases():
    sh = open(os.path.join(REPO, ".github", "scripts", "state_save.sh")).read()
    line = [x for x in sh.splitlines() if "gh release upload odds-hist" in x][0]
    assert "--clobber" not in line
    assert re.search(r"shards=\(out/quotes_\*\.jsonl\.gz\)", sh)            # live shards: quotes_ only
