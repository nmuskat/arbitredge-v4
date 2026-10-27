"""GitHub Actions path: credit gate across runs, persistence, safety switches, workflow lint, budget simulation."""
import calendar
import glob
import gzip
import json
import os
import random
import re
import shutil

import pytest

import odds_gateway as og
from analysis_v4 import actions_analyze
from collect_v4 import actions_run as AR
from collect_v4 import cron_plan
from collect_v4 import schedule_policy as SP
from market_v4 import store

KEY = "".join(["q7", "w8", "e9", "r0"]) * 8
NOW = store.iso_to_epoch("2026-11-14T15:47:00Z")          # Saturday
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def payload(sport, t):
    games = [("n" * 31 + "1", NOW + 2 * 3600), ("n" * 31 + "2", NOW + 26 * 3600)] if sport.endswith("nfl") else \
            [("c" * 31 + "1", NOW + 1800), ("c" * 31 + "2", NOW + 5 * 3600)]
    out = []
    for gid, kick in games:
        bks = [{"key": b, "last_update": iso(t - 30), "markets": [
            {"key": "spreads", "last_update": iso(t - 30), "outcomes": [
                {"name": "H", "price": hp, "point": -3.0}, {"name": "A", "price": ap, "point": 3.0}]},
            {"key": "h2h", "last_update": iso(t - 30), "outcomes": [{"name": "H", "price": -150}, {"name": "A", "price": 130}]}]}
               for b, hp, ap in (("pinnacle", -108, -102), ("lowvig", -107, -103), ("draftkings", 105, -125))]
        out.append({"id": gid, "sport_key": sport, "commence_time": iso(kick), "home_team": "H", "away_team": "A",
                    "bookmakers": bks})
    return out


class Upstream:
    def __init__(self, clock, remaining=15000):
        self.clock, self.calls, self.remaining = clock, [], remaining

    def __call__(self, path, q):
        self.calls.append(path)
        cost = 0 if path == "/v4/sports" else 3
        self.remaining -= cost
        hdr = {"x-requests-last": str(cost), "x-requests-remaining": str(self.remaining),
               "x-requests-used": str(20000 - self.remaining)}
        if path == "/v4/sports":
            return 200, hdr, b'[{"key": "americanfootball_nfl"}]'
        sport = path.split("/")[3]
        return 200, hdr, json.dumps(payload(sport, self.clock())).encode()


def env(**kw):
    e = {"V4_LIVE": "on", "ODDS_KEY_ENV": "TEST_ODDS_KEY", "TEST_ODDS_KEY": KEY}
    e.update(kw)
    return e


@pytest.fixture
def run_chain(tmp_path, monkeypatch):
    """Runs actions_run repeatedly, feeding each run's outputs to the next (like the release assets)."""
    clock = {"t": NOW}
    up = Upstream(lambda: clock["t"])
    state = tmp_path / "state"
    state.mkdir()
    n = {"i": 0}

    def go(e=None, probe=False, advance_min=0):
        clock["t"] += advance_min * 60
        e = env() if e is None else e
        for k, v in e.items():
            monkeypatch.setenv(k, v)
        if "V4_LIVE" not in e:
            monkeypatch.delenv("V4_LIVE", raising=False)
        n["i"] += 1
        out = tmp_path / f"out{n['i']}"
        s = AR.run(str(state), str(out), env=e, clock=lambda: clock["t"], opener=up, probe=probe)
        for f in ("ledger.jsonl", "collector_state.json"):
            if (out / f).exists():
                shutil.copy(out / f, state / f)
        return s, out
    return go, up, tmp_path


def test_switch_off_requests_nothing(run_chain, monkeypatch):
    go, up, _ = run_chain
    monkeypatch.delenv("TEST_ODDS_KEY", raising=False)
    s, out = go(e={"V4_LIVE": "off"})
    assert "OFF" in s["status"] and up.calls == []
    s, out = go(e={})
    assert "OFF" in s["status"] and up.calls == []


def test_missing_secret_is_an_error_and_requests_nothing(run_chain, monkeypatch):
    go, up, _ = run_chain
    monkeypatch.delenv("TEST_ODDS_KEY", raising=False)
    s, _ = go(e={"V4_LIVE": "on", "ODDS_KEY_ENV": "TEST_ODDS_KEY"})
    assert s["status"].startswith("no API key") and up.calls == []


def test_bootstrap_then_not_due_then_close(run_chain):
    go, up, tmp = run_chain
    s, out = go()
    assert [f["sport"] for f in s["fetches"]] == ["americanfootball_nfl", "americanfootball_ncaaf"]
    assert s["usage"]["v4"] == 6 and len(glob.glob(str(out / "quotes_*.jsonl.gz"))) == 1
    s, _ = go(advance_min=15)            # CFB kickoff now 15 min away: already covered by the last fetch (30 min before)
    assert s["fetches"] == [] and {x["sport"] for x in s["skipped"]} == {"americanfootball_nfl", "americanfootball_ncaaf"}
    s, _ = go(advance_min=75)            # NFL kickoff 30 min away, last fetch 90 min ago -> observed-close snapshot
    assert [f["sport"] for f in s["fetches"]] == ["americanfootball_nfl"] and "close" in s["fetches"][0]["reason"]
    assert s["usage"]["v4"] == 9         # ledger carried across runs


def test_monthly_cap_holds_across_runs(run_chain):
    go, up, _ = run_chain
    s, _ = go(e=env(V4_MONTHLY_CAP="5"))
    assert len(s["fetches"]) == 1 and s["refused"][0]["status"] == 402
    s, _ = go(e=env(V4_MONTHLY_CAP="5"), advance_min=100)
    assert s["fetches"] == [] and s["usage"]["v4"] == 3
    assert sum(1 for c in up.calls if c.endswith("/odds")) == 1


def test_v3_floor_from_provider_header(run_chain):
    go, up, _ = run_chain
    up.remaining = 2004                  # after the first call the provider reports 2,001 left
    s, _ = go()
    assert len(s["fetches"]) == 1 and "V3 floor" in s["refused"][0]["detail"]


def test_caps_cannot_exceed_approved_limits():
    c = AR.caps_from_env({"V4_MONTHLY_CAP": "99999", "V4_V3_FLOOR": "10", "V4_MAX_REQUEST": "500"})
    assert c["v4_monthly_cap"] == 6000 and c["v3_floor_remaining"] == 2000 and c["max_single_request"] == 30
    assert AR.caps_from_env({})["v4_monthly_cap"] == 500           # initial test budget by default
    assert AR.caps_from_env({"V4_MONTHLY_CAP": "abc"})["v4_monthly_cap"] == 500


def test_probe_is_zero_credit_and_recorded(run_chain):
    go, up, _ = run_chain
    s, out = go(probe=True)
    assert up.calls == ["/v4/sports"] and s["probe"]["provider_status"] == 200 and s["usage"]["v4"] == 0
    assert len(open(out / "ledger.jsonl").read().splitlines()) == 1


def test_key_never_in_any_output(run_chain):
    go, up, tmp = run_chain
    go()
    go(advance_min=90)
    for p in glob.glob(str(tmp / "**" / "*"), recursive=True):
        if os.path.isfile(p):
            data = gzip.open(p).read() if p.endswith(".gz") else open(p, "rb").read()
            assert KEY.encode() not in data, p


def test_shards_rebuild_exactly_and_duplicates_do_not_double_count(run_chain):
    go, up, tmp = run_chain
    go()
    go(advance_min=90)
    shards = glob.glob(str(tmp / "out*" / "quotes_*.jsonl.gz"))
    conn, counts = actions_analyze.rebuild(shards)
    conn2, counts2 = actions_analyze.rebuild(shards + shards)
    assert counts["mkt_quote"] > 0 and counts2["mkt_quote"] == counts["mkt_quote"]
    assert conn2.execute("SELECT COUNT(*) FROM mkt_fetch").fetchone()[0] == counts["mkt_fetch"] == 3


# ---------------------------------------------------------------- workflow lint

def _wf(name):
    return open(os.path.join(REPO, ".github", "workflows", name)).read()


def test_collect_workflow_crons_match_simulated_plan():
    crons = re.findall(r'- cron: "([^"]+)"', _wf("odds-collect.yml"))
    assert crons == cron_plan.cron_strings()


@pytest.mark.parametrize("wf", ["odds-collect.yml", "odds-analyze.yml"])
def test_scheduled_jobs_are_off_until_owner_switch(wf):
    assert "if: vars.V4_LIVE == 'on'" in _wf(wf)


def test_secret_only_in_collect_and_verify_steps_and_never_on_pull_requests():
    for wf in os.listdir(os.path.join(REPO, ".github", "workflows")):
        text = _wf(wf)
        uses_secret = "secrets.ODDS_API_KEY" in text
        assert uses_secret == (wf in ("odds-collect.yml", "odds-verify.yml")), wf
        if uses_secret:
            assert "pull_request" not in text and text.count("secrets.ODDS_API_KEY") == 1


@pytest.mark.parametrize("wf", ["odds-collect.yml", "odds-verify.yml", "odds-analyze.yml"])
def test_actions_pinned_and_permissions_minimal(wf):
    text = _wf(wf)
    for ref in re.findall(r"uses: ([^\s]+)", text):
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", ref), ref
    assert "permissions:\n  contents: read" in text
    assert "persist-credentials: false" in text
    assert re.search(r"cancel-in-progress: false", text) and "group: v4-odds-state" in text


# ---------------------------------------------------------------- budget simulation (3 weeks, score the middle one)

def _ts(y, mo, d, h, mi):
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


WEEK = _ts(2026, 11, 8, 0, 0)        # Sunday, after the switch to EST
NFL = ([_ts(2026, 11, 13, 1, 15)] + [_ts(2026, 11, 8, 18, 0)] * 9 + [_ts(2026, 11, 8, 21, 5)] * 2 +
       [_ts(2026, 11, 8, 21, 25)] * 3 + [_ts(2026, 11, 9, 1, 20), _ts(2026, 11, 10, 1, 15)])
CFB = ([_ts(2026, 11, 14, 17, 0)] * 12 + [_ts(2026, 11, 14, 20, 30)] * 10 + [_ts(2026, 11, 14, 21, 0)] * 5 +
       [_ts(2026, 11, 15, 0, 0)] * 6 + [_ts(2026, 11, 15, 0, 30)] * 6 + [_ts(2026, 11, 15, 1, 0)] * 4 +
       [_ts(2026, 11, 15, 3, 30)] * 4)


def simulate(kicks, tier, drop=0.0, delay_max_min=0, seed=3):
    rng = random.Random(seed)
    allk = [k + w * 7 * 86400 for w in range(3) for k in kicks]
    runs = [t for w in range(3) for t in cron_plan.run_times(WEEK + w * 7 * 86400)]
    last, fetches, covered = None, [], set()
    for t in runs:
        if rng.random() < drop:
            continue
        t2 = t + rng.randint(0, delay_max_min * 60)
        is_due, _ = SP.due(t2, allk if last else [], last, tier)
        if is_due:
            fetches.append(t2)
            last = t2
            covered |= {k for k in allk if 0 < k - t2 <= SP.CLOSE_WINDOW_MIN * 60}
    lo, hi = WEEK + 7 * 86400, WEEK + 14 * 86400
    mid = {k for k in allk if lo <= k < hi}
    return sum(1 for f in fetches if lo <= f < hi), len(mid & covered), len(mid)


def test_test_tier_fits_500_credits_and_observes_every_close():
    n_nfl, c_nfl, k_nfl = simulate(NFL, "test")
    n_cfb, c_cfb, k_cfb = simulate(CFB, "test")
    monthly = 3 * (n_nfl + n_cfb) * 4.35
    assert monthly <= 400, monthly                       # >= 20% headroom under the 500 test cap
    assert c_nfl == k_nfl and c_cfb == k_cfb             # every kickoff slot's close observed


def test_delays_and_drops_degrade_gracefully():
    n1, c1, k1 = simulate(NFL, "test", drop=0.25, delay_max_min=40)
    n2, c2, k2 = simulate(CFB, "test", drop=0.25, delay_max_min=40)
    assert 3 * (n1 + n2) * 4.35 <= 500
    assert (c1 + c2) / (k1 + k2) >= 0.75                 # most closes still observed under heavy disruption


def test_standard_tier_within_6000_and_actions_minutes_within_free_quota():
    n_nfl, _, _ = simulate(NFL, "standard")
    n_cfb, _, _ = simulate(CFB, "standard")
    assert 3 * (n_nfl + n_cfb) * 4.35 <= 6000
    runs_per_month = len(cron_plan.run_times(WEEK)) * 4.35
    analysis = 31 * 4                                     # daily analysis, <= 4 min
    ci = 40 * 3                                           # ~40 pushes x ~2-3 min
    assert runs_per_month + analysis + ci <= 1200         # well under GitHub Free's 2,000 minutes


def test_spend_is_persisted_after_each_request_even_if_the_run_dies(run_chain, monkeypatch):
    go, up, tmp = run_chain
    real = AR.Collector.fetch_odds
    calls = {"n": 0}

    def flaky(self, sport):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("runner died mid-run")
        return real(self, sport)
    monkeypatch.setattr(AR.Collector, "fetch_odds", flaky)
    with pytest.raises(RuntimeError):
        go()
    out = tmp / "out1"
    rows = [json.loads(x) for x in open(out / "ledger.jsonl")]
    assert sum(r["actual_cost"] or r["est_cost"] for r in rows) == 3      # the first request is on record


def test_state_load_refuses_lost_ledger():
    text = open(os.path.join(REPO, ".github", "scripts", "state_load.sh")).read()
    assert "refusing to run" in text and "exit 1" in text
    assert "|| true" not in text                                       # downloads may never fail silently


FAKE_GH = r'''#!/bin/bash
# fake gh: behaviour driven by env FAKE_ASSETS, FAKE_DATA_RELEASES, FAKE_DOWNLOAD_FAIL
case "$1 $2" in
  "release view")
    if [[ "$*" == *"--json assets"* ]]; then printf '%s\n' $FAKE_ASSETS; fi; exit 0 ;;
  "release create") exit 0 ;;
  "release list") echo "${FAKE_DATA_RELEASES:-0}"; exit 0 ;;
  "release download")
    [[ "${FAKE_DOWNLOAD_FAIL:-0}" == 1 ]] && exit 1
    for a in "$@"; do last="$a"; done
    pat=""; prev=""; dir=""
    for a in "$@"; do [[ "$prev" == "-p" ]] && pat="$a"; [[ "$prev" == "-D" ]] && dir="$a"; prev="$a"; done
    echo '{}' > "$dir/$pat"; exit 0 ;;
esac
exit 0
'''


@pytest.mark.parametrize("assets,data_releases,fail,expect_ok", [
    ("", "0", "0", True),                                    # genuine first run
    ("collector_state.json", "0", "0", False),                # state without ledger -> ledger was lost
    ("", "3", "0", False),                                    # data exists, ledger gone
    ("ledger.jsonl collector_state.json", "3", "0", True),    # normal run
    ("ledger.jsonl collector_state.json", "3", "1", False),   # download failure must fail the job
])
def test_state_load_behaviour(tmp_path, assets, data_releases, fail, expect_ok):
    import subprocess
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_ASSETS=assets,
               FAKE_DATA_RELEASES=data_releases, FAKE_DOWNLOAD_FAIL=fail)
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "state_load.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert (r.returncode == 0) == expect_ok, r.stdout + r.stderr
    if expect_ok and "ledger.jsonl" in assets:
        assert (tmp_path / "state" / "ledger.jsonl").exists()


def test_worst_case_month_cannot_exceed_caps(run_chain, monkeypatch):
    """Scheduling logic broken (every run wants to fetch both sports), every scheduled run fires:
    the gate alone must hold V4 to the monthly and daily caps."""
    go, up, tmp = run_chain
    monkeypatch.setattr(SP, "due", lambda *a, **k: (True, "forced"))
    month_start = store.iso_to_epoch("2026-11-01T00:00:00Z")
    runs = [t for w in range(-1, 5) for t in cron_plan.run_times(store.iso_to_epoch("2026-11-01T00:00:00Z") + w * 7 * 86400)
            if month_start <= t < store.iso_to_epoch("2026-12-01T00:00:00Z")]
    clock_now = NOW
    per_day = {}
    for t in runs:
        s, _ = go(advance_min=(t - clock_now) / 60)
        clock_now = t
        for f in s["fetches"]:
            day = t // 86400
            per_day[day] = per_day.get(day, 0) + 3
    total = sum(per_day.values())
    assert total <= 500 and max(per_day.values()) <= 60
    assert total >= 450              # and the budget is actually usable, not blocked by a bug


# ---------------------------------------------------------------- regression: V4_TIER empty (GitHub undefined variable)

def _workflow_vars(wf="odds-collect.yml"):
    """env names the workflow fills from repository variables, e.g. V4_TIER: ${{ vars.V4_TIER }}"""
    return re.findall(r"^\s+(\w+): \$\{\{ vars\.\w+ \}\}", _wf(wf), flags=re.M)


def test_workflow_variable_list_is_what_we_test():
    assert set(_workflow_vars()) == {"V4_LIVE", "V4_MONTHLY_CAP", "V4_DAILY_CAP", "V4_TIER"}


def _main_like_workflow(tmp_path, monkeypatch, overrides, live="on"):
    """Run collect_v4.actions_run.main() exactly as the workflow does: every vars.* env present,
    undefined ones as EMPTY STRINGS (GitHub's behaviour), upstream faked at the HTTPS layer."""
    calls = []
    up = Upstream(lambda: NOW)
    monkeypatch.setattr(og.Gateway, "_https_get", lambda self, path, q: (calls.append(path), up(path, q))[1])
    monkeypatch.setattr(AR.time, "time", lambda: NOW)
    for name in _workflow_vars():
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("V4_LIVE", live)
    monkeypatch.setenv("ODDS_KEY_ENV", "ODDS_API_KEY")
    monkeypatch.setenv("ODDS_API_KEY", KEY)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    state, out = tmp_path / "state", tmp_path / "out"
    state.mkdir(exist_ok=True)
    rc = AR.main(["--state-in", str(state), "--out", str(out)])
    summary = json.load(open(out / "run_summary.json")) if (out / "run_summary.json").exists() else None
    return rc, summary, calls


def test_empty_tier_runs_on_lowest_tier(tmp_path, monkeypatch):
    rc, s, calls = _main_like_workflow(tmp_path, monkeypatch, {})          # every variable empty, as on GitHub
    assert rc == 0 and s["tier"] == "test" and s["tier_source"].startswith("default")
    assert s["caps"]["v4_monthly_cap"] == 500 and s["caps"]["v4_daily_cap"] == 60   # empty caps -> safe defaults
    assert len(s["fetches"]) == 2 and s["usage"]["v4"] == 6


@pytest.mark.parametrize("value", ["", "   ", None])
def test_missing_tier_resolves_to_test(value):
    assert SP.resolve_tier(value)[0] == "test"


@pytest.mark.parametrize("value,expected", [("test", "test"), (" Standard ", "standard"), ("TEST", "test")])
def test_valid_tier_names(value, expected):
    assert SP.resolve_tier(value)[0] == expected


@pytest.mark.parametrize("value", ["premium", "max", "6000", "standard2", "unlimited"])
def test_invalid_tier_stops_before_any_request(tmp_path, monkeypatch, value):
    rc, s, calls = _main_like_workflow(tmp_path, monkeypatch, {"V4_TIER": value})
    assert rc == 2 and calls == [] and s is None                          # no request, no summary, no ledger write
    assert not (tmp_path / "out" / "ledger.jsonl").exists()
    with pytest.raises(SP.InvalidTier):
        SP.resolve_tier(value)


def test_invalid_tier_rejected_even_when_live_is_off(tmp_path, monkeypatch):
    rc, s, calls = _main_like_workflow(tmp_path, monkeypatch, {"V4_TIER": "premium"}, live="off")
    assert rc == 2 and calls == []


def test_due_rejects_unknown_tier_with_clear_error():
    with pytest.raises(SP.InvalidTier):
        SP.due(NOW, [], None, "")


def test_empty_cap_variables_never_raise_spending():
    c = AR.caps_from_env({"V4_MONTHLY_CAP": "", "V4_DAILY_CAP": "", "V4_MAX_REQUEST": "", "V4_V3_FLOOR": ""})
    assert c == {"v4_monthly_cap": 500, "v4_daily_cap": 60, "max_single_request": 10, "v3_floor_remaining": 2000}


def test_probe_path_also_tolerates_empty_tier(tmp_path, monkeypatch):
    calls = []
    up = Upstream(lambda: NOW)
    monkeypatch.setattr(og.Gateway, "_https_get", lambda self, path, q: (calls.append(path), up(path, q))[1])
    for k, v in {"V4_LIVE": "on", "V4_TIER": "", "V4_MONTHLY_CAP": "", "ODDS_KEY_ENV": "ODDS_API_KEY",
                 "ODDS_API_KEY": KEY}.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "s").mkdir()
    assert AR.main(["--state-in", str(tmp_path / "s"), "--out", str(tmp_path / "o"), "--probe"]) == 0
    assert calls == ["/v4/sports"]
