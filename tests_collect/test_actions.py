"""GitHub Actions path: credit gate across runs, persistence, safety switches, workflow lint, budget simulation."""
import calendar
import glob
import gzip
import json
import os
import random
import re
import shutil
import subprocess
import time

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
    assert sorted(crons) == sorted(cron_plan.cron_strings() + cron_plan.expanded_cron_strings())
    assert len(crons) == len(set(crons))


def _minutes_of(entry):
    """Every (dow, hour, minute) a 5-field workflow cron entry (lists only) matches."""
    m, h, _, _, d = entry.split()
    return {(int(dd), int(hh), int(mm)) for dd in d.split(",") for hh in h.split(",") for mm in m.split(",")}


def test_no_two_schedule_entries_start_runs_in_the_same_minute():
    # GitHub starts one run per matching entry: an overlap would bill two minutes for one slot
    entries = cron_plan.cron_strings() + cron_plan.expanded_cron_strings()
    seen = {}
    for e in entries:
        for x in _minutes_of(e):
            assert x not in seen, (x, e, seen.get(x))
            seen[x] = e


def test_tier_gated_runs_match_the_simulated_plan():
    job_if = _yaml("odds-collect.yml")["jobs"]["collect"]["if"]
    assert job_if.startswith("vars.V4_LIVE == 'on' && ((vars.V4_TIER == 'expanded' && !contains(fromJSON('")
    std_listed, exp_listed = [json.loads(x) for x in re.findall(r"fromJSON\('(\[.*?\])'\)", job_if)]
    assert std_listed == cron_plan.standard_only_cron_strings()
    assert exp_listed == cron_plan.expanded_cron_strings()
    assert "(vars.V4_TIER != 'expanded' && !contains(" in job_if
    import calendar
    wk = calendar.timegm((2026, 11, 1, 0, 0, 0))

    def executed(tier):            # what the workflow gate lets run, from the entries themselves
        skip = exp_listed if tier != "expanded" else std_listed
        return {x for e in cron_plan.cron_strings() + cron_plan.expanded_cron_strings() if e not in skip
                for x in _minutes_of(e)}

    def simulated(tier):
        out = set()
        for t in cron_plan.run_times(wk, tier):
            g = time.gmtime(t)
            out.add(((g.tm_wday + 1) % 7, g.tm_hour, g.tm_min))
        return out
    for tier in ("expanded", "balanced", "test"):
        assert executed(tier) == simulated(tier), tier
    # standard-only runs never sit in a kickoff-dense evening or weekend
    for d, hours in cron_plan.EXPANDED_SKIPS_BASE.items():
        assert d in (1, 2, 3, 4, 5)
        assert not (d in (4, 5) and set(hours) & {18, 21})


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
        assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", ref) or ref in ("./.github/workflows/site-build.yml",
                                                                             "./.github/workflows/splits-collect.yml"), ref
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
    assert set(_workflow_vars()) == {"V4_LIVE", "V4_MONTHLY_CAP", "V4_DAILY_CAP", "V4_TIER", "V4_BACKFILL_BUDGET"}


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


# ---------------------------------------------------------------- automatic site build

def _yaml(name):
    import yaml
    return yaml.safe_load(_wf(name))


def test_site_build_workflow_has_only_cloudflare_secrets_and_is_pinned():
    text = _wf("site-build.yml")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    assert "ODDS_API_KEY" not in text and "CFBD_API_KEY" not in text
    assert "pull_request" not in text
    for ref in re.findall(r"uses: ([^\s]+)", text):
        assert re.fullmatch(r"actions/(checkout|cache)@[0-9a-f]{40}", ref), ref       # GitHub's own, pinned
    cache = [s for s in _yaml("site-build.yml")["jobs"]["build"]["steps"] if "actions/cache@" in str(s.get("uses"))]
    assert len(cache) == 1 and "env" not in cache[0]                                  # never sees a secret
    assert set(cache[0]["with"]["path"].split()) == {"~/.cache/pip", "~/.npm"}         # download stores only
    assert "--prefer-offline" in open(os.path.join(REPO, ".github", "scripts", "pages_deploy.sh")).read()
    assert "permissions:\n  contents: read" in text and "persist-credentials: false" in text
    on = _yaml("site-build.yml")[True]
    assert set(on) == {"workflow_call", "workflow_dispatch"}          # never on a schedule of its own


@pytest.mark.parametrize("wf,need,output", [("odds-collect.yml", "collect", "fetched"),
                                            ("cfb-rankings.yml", "rankings", "saved")])
def test_site_job_runs_only_after_new_data_and_gets_no_secrets(wf, need, output):
    site = _yaml(wf)["jobs"]["site"]
    assert site["uses"] == "./.github/workflows/site-build.yml"
    if wf == "odds-collect.yml":      # also after a public-splits snapshot (2026-11-12), only when collection succeeded
        assert site["needs"] == [need, "splits"]
        assert site["if"] == (f"always() && needs.{need}.result == 'success' && (needs.{need}.outputs.{output} == 'true' "
                              "|| needs.splits.result == 'success')")
    else:
        assert site["needs"] == need and site["if"] == f"needs.{need}.outputs.{output} == 'true'"
    assert site["secrets"] == {"CLOUDFLARE_API_TOKEN": "${{ secrets.CLOUDFLARE_API_TOKEN }}",
                               "CLOUDFLARE_ACCOUNT_ID": "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}"}   # never 'inherit'
    assert not re.search(r"secrets:\s*inherit", _wf(wf))
    assert site["permissions"] == {"contents": "write"}


FAKE_GH_OK = "#!/bin/bash\nexit 0\n"


@pytest.mark.parametrize("with_shard", [True, False])
def test_state_save_signals_new_shard(tmp_path, with_shard):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH_OK)
    (bin_dir / "gh").chmod(0o755)
    (tmp_path / "out").mkdir()
    if with_shard:
        (tmp_path / "out" / "quotes_20261027T004034Z.jsonl.gz").write_bytes(b"x")
    gho = tmp_path / "gh_output"
    gho.write_text("")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", GITHUB_OUTPUT=str(gho))
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "state_save.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert ("fetched=true" in gho.read_text()) == with_shard
    env.pop("GITHUB_OUTPUT")                                          # outside Actions: still succeeds
    assert subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "state_save.sh")], cwd=tmp_path,
                          env=env, capture_output=True).returncode == 0


FAKE_GH_SITE = r"""#!/bin/bash
case "$1" in
  api)
    case "$*" in
      *official_*) echo "${FAKE_OFF:-0}"; exit 0 ;;          # Official Needs records (counted by their own pattern)
      *releases/tags/v4-book*) [[ -n "${FAKE_BOOK:-}" ]] || exit 1; echo "${FAKE_BOOK%%:*}"; exit 0 ;;
      *releases/tags/v4-need-decisions*) [[ -n "${FAKE_DEC:-}" ]] || exit 1; echo "${FAKE_DEC%%:*}"; exit 0 ;;
      *releases/tags/v4-need-*|*releases/tags/v4-results*|*releases/tags/v4-settlement*|*releases/tags/v4-splits*) exit 1 ;;
      *) printf '%s\n' $FAKE_TAGS; exit 0 ;;
    esac ;;
  release)
    for a in "$@"; do last="$a"; done
    case "$*" in
      *official_dec_*) mkdir -p "$last"; for i in $(seq 1 "${FAKE_OFF:-0}"); do echo x > "$last/official_dec_$i.json.gz"; done ;;
      *official_snap_*) mkdir -p "$last"; for i in $(seq 1 "${FAKE_OFF:-0}"); do echo x > "$last/official_snap_nfl_$i.json.gz"; done ;;
      *v4-state*) [[ "${FAKE_NO_LEDGER:-0}" == 1 ]] && exit 1; mkdir -p "$last"; echo '{}' > "$last/ledger.jsonl" ;;
      *cfb-rankings*) [[ "${FAKE_RANKINGS:-0}" == 1 ]] || exit 1; mkdir -p "$last"; echo '{}' > "$last/rankings_x.json" ;;
      *v4-book*) mkdir -p "$last"; for i in $(seq 1 "${FAKE_BOOK##*:}"); do echo x > "$last/book_nfl_x_$i.json.gz"; done ;;
      *v4-need-decisions*) mkdir -p "$last"; for i in $(seq 1 "${FAKE_DEC##*:}"); do echo x > "$last/decisions_$i.json.gz"; done ;;
      *) mkdir -p "$last"; echo x > "$last/quotes_1.jsonl.gz" ;;
    esac; exit 0 ;;
esac
exit 0
"""


@pytest.mark.parametrize("tags,no_ledger,rankings,ok", [
    ("odds-data-2026-W44 odds-data-2026-W45", 0, 0, True),    # no rankings yet: still builds (CFB shows UNAVAILABLE)
    ("odds-data-2026-W44", 0, 1, True),
    ("", 0, 0, False),                                       # no shards: refuse (never an empty "live" page)
    ("odds-data-2026-W44", 1, 0, False),                     # ledger missing: refuse (health would be wrong)
])
def test_site_inputs(tmp_path, tags, no_ledger, rankings, ok):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH_SITE)
    (bin_dir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_TAGS=tags, FAKE_NO_LEDGER=str(no_ledger),
               FAKE_RANKINGS=str(rankings), GITHUB_REPOSITORY="o/r")
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "site_inputs.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    if ok:
        assert len(list((tmp_path / "shards").rglob("quotes_*.jsonl.gz"))) == len(tags.split())
        assert list((tmp_path / "book").glob("*")) == []          # no v4-book release yet: starts empty
        assert (tmp_path / "state" / "ledger.jsonl").exists()
        assert len(list((tmp_path / "rankings").glob("rankings_*.json"))) == rankings


# ---------------------------------------------------------------- Cloudflare Pages deploy

def test_cloudflare_deploy_steps_are_gated_and_the_check_holds_no_secret():
    steps = _yaml("site-build.yml")["jobs"]["build"]["steps"]
    by = {st.get("name"): st for st in steps}
    chk, dep = by["Check owner-only protection (no secrets)"], by["Deploy to Cloudflare Pages"]
    assert chk["if"] == dep["if"] == "vars.CF_PAGES_DEPLOY == 'on'"
    assert "secrets." not in json.dumps(chk)
    assert set(dep["env"]) == {"CF_PAGES_PROJECT", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    names = [st.get("name") for st in steps]
    assert names.index("Check owner-only protection (no secrets)") < names.index("Deploy to Cloudflare Pages")
    assert names[-2:] == ["Deploy to Cloudflare Pages", "Publish deploy report (no secrets)"]
    assert not any("secrets." in json.dumps(st) for st in steps if st is not dep)
    secrets = _yaml("site-build.yml")[True]["workflow_call"]["secrets"]
    assert set(secrets) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}


def test_wrangler_is_pinned_by_lockfile():
    pkg = json.load(open(os.path.join(REPO, "deploy", "cloudflare", "package.json")))
    assert re.fullmatch(r"\d+\.\d+\.\d+", pkg["dependencies"]["wrangler"])          # exact, no ranges
    lock = json.load(open(os.path.join(REPO, "deploy", "cloudflare", "package-lock.json")))
    pk = lock["packages"]
    assert pk["node_modules/wrangler"]["version"] == pkg["dependencies"]["wrangler"]
    assert all(v.get("integrity", "").startswith("sha512-") for k, v in pk.items() if k and not v.get("link"))
    script = open(os.path.join(REPO, ".github", "scripts", "pages_deploy.sh")).read()
    assert "npm ci --prefix deploy/cloudflare --ignore-scripts" in script and "npx" not in script


def test_security_headers():
    h = open(os.path.join(REPO, "deploy", "cloudflare", "_headers")).read()
    for line in ("X-Robots-Tag: noindex", "Cache-Control: no-store", "X-Frame-Options: DENY",
                 "default-src 'none'", "connect-src 'none'", "frame-ancestors 'none'"):
        assert line in h


FAKE_CURL = r"""#!/bin/bash
url="${@: -1}"
for a in "$@"; do [[ "$a" == Authorization:* ]] && echo "$a" > "$FAKE_DIR/auth_header"; done
case "$url" in
  https://api.cloudflare.com/*/deployments*) cat "$FAKE_DIR/deploys.json" ;;
  https://api.cloudflare.com/*) cat "$FAKE_DIR/project.json" ;;
  "https://$FAKE_PROJECT.pages.dev/") printf '%s' "$FAKE_MAIN" ;;
  *) printf '%s' "$FAKE_PREVIEW" ;;
esac
"""
FAKE_WRANGLER = "#!/bin/bash\necho \"$@\" > \"$FAKE_DIR/wrangler_args\"\nls dist > \"$FAKE_DIR/dist_files\"\necho deployed\n"
LOGIN = "302 https://owner-team.cloudflareaccess.com/cdn-cgi/access/login/x"


def _pages(tmp_path, mode, main=LOGIN, preview=LOGIN, project="arbitredge-v4-staging", token="tok_SECRET_123",
           project_json=None, deploy_status="success", site=True):
    d = tmp_path / "fake"
    d.mkdir(exist_ok=True)
    (d / "curl").write_text(FAKE_CURL)
    (d / "wrangler").write_text(FAKE_WRANGLER)
    for f in ("curl", "wrangler"):
        (d / f).chmod(0o755)
    (d / "project.json").write_text(json.dumps(project_json or {"success": True, "result": {
        "name": project, "production_branch": "main", "source": None}}))
    (d / "deploys.json").write_text(json.dumps({"success": True, "result": [
        {"id": "dep1", "url": f"https://ab12cd34.{project}.pages.dev",
         "latest_stage": {"name": "deploy", "status": deploy_status}}]}))
    work = tmp_path / "work"
    (work / "deploy" / "cloudflare").mkdir(parents=True, exist_ok=True)
    shutil.copy(os.path.join(REPO, "deploy", "cloudflare", "_headers"), work / "deploy" / "cloudflare" / "_headers")
    if site:
        (work / "site").mkdir(exist_ok=True)
        (work / "site" / "index.html").write_text("<!doctype html>dash")
        (work / "site" / "site_snapshot.json").write_text("{}")
    env = dict(os.environ, CURL=str(d / "curl"), WRANGLER=str(d / "wrangler"), FAKE_DIR=str(d),
               FAKE_PROJECT=project, FAKE_MAIN=main, FAKE_PREVIEW=preview, CF_PAGES_PROJECT=project,
               POLL_SECONDS="0", GITHUB_SHA="abc1234")
    if token:
        env.update(CLOUDFLARE_API_TOKEN=token, CLOUDFLARE_ACCOUNT_ID="acct123")
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "pages_deploy.sh"), mode], cwd=work,
                       env=env, capture_output=True, text=True)
    return r, d, work


def test_pages_check_passes_only_when_everything_is_behind_access(tmp_path):
    assert _pages(tmp_path, "check")[0].returncode == 0


@pytest.mark.parametrize("main,preview", [
    ("200 ", LOGIN),                                            # production address public
    (LOGIN, "200 "),                                            # previews / per-deployment URLs public
    (LOGIN, "404 "),                                            # preview hostnames not covered by Access
    ("522 ", LOGIN),
    ("302 https://evil.example.com/cloudflareaccess.com/", LOGIN),
    ("302 https://x.cloudflareaccess.com.evil.io/", LOGIN),
    ("000 ", LOGIN),                                            # unreachable: not proven protected
])
def test_pages_check_refuses_unprotected(tmp_path, main, preview):
    r, _, _ = _pages(tmp_path, "check", main=main, preview=preview)
    assert r.returncode != 0 and "Refusing to deploy" in r.stdout


@pytest.mark.parametrize("project", ["", "Bad_Name", "x;rm -rf /", "a" * 70])
def test_pages_rejects_bad_project_names(tmp_path, project):
    assert _pages(tmp_path, "check", project=project)[0].returncode != 0


def test_pages_deploy_uses_production_branch_and_uploads_only_the_page(tmp_path):
    r, d, work = _pages(tmp_path, "deploy",
                        project_json={"success": True, "result": {"name": "arbitredge-v4-staging",
                                                                  "production_branch": "v4-dev", "source": None}})
    assert r.returncode == 0, r.stdout + r.stderr
    args = (d / "wrangler_args").read_text()
    assert "pages deploy dist --project-name arbitredge-v4-staging --branch v4-dev" in args
    assert "--commit-hash abc1234" in args
    assert (d / "dist_files").read_text().split() == ["_headers", "index.html", "robots.txt"]   # no snapshot JSON
    assert (d / "auth_header").read_text().strip() == "Authorization: Bearer tok_SECRET_123"
    assert "tok_SECRET_123" not in r.stdout + r.stderr
    rep = [json.loads(x) for x in (work / "deploy_report.jsonl").read_text().splitlines()]
    assert [x.get("check") or x.get("deploy") for x in rep] == ["production", "after:production",
                                                                 "after:deployment-url"]
    assert rep[0]["deployment_id"] == "dep1" and rep[0]["branch"] == "v4-dev"
    assert rep[2]["host"] == "ab12cd34.arbitredge-v4-staging.pages.dev" and rep[2]["access_login"] == "yes"
    assert "tok_SECRET_123" not in (work / "deploy_report.jsonl").read_text()


def test_pages_check_report(tmp_path):
    r, _, work = _pages(tmp_path, "check", preview="200 ")
    rep = [json.loads(x) for x in (work / "deploy_report.jsonl").read_text().splitlines()]
    assert [(x["check"], x["access_login"]) for x in rep] == [("before:production", "yes"),
                                                              ("before:wildcard-preview", "NO")]


def test_deploy_fails_if_deployment_url_is_public_after_upload(tmp_path):
    # production address protected, but the new deployment's own URL answers 200: fail loudly
    r, _, _ = _pages(tmp_path, "deploy", preview="200 ")
    assert r.returncode != 0 and "NOT protected: https://ab12cd34" in r.stdout


@pytest.mark.parametrize("kw", [
    {"token": None},                                                              # secrets not set
    {"site": False},                                                              # nothing built
    {"project_json": {"success": False, "errors": [{"message": "Project not found"}]}},
    {"deploy_status": "failure"},
])
def test_pages_deploy_fails_loudly(tmp_path, kw):
    r, _, _ = _pages(tmp_path, "deploy", **kw)
    assert r.returncode != 0
    assert "tok_SECRET_123" not in r.stdout + r.stderr


@pytest.mark.parametrize("book,ok,n", [("3:3", True, 3),      # release lists 3 ledger files, all 3 arrive
                                       ("0:0", True, 0),      # release exists, no files yet
                                       ("3:2", False, None)])  # a ledger file failed to arrive: refuse
def test_site_inputs_synthetic_book_ledger(tmp_path, book, ok, n):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH_SITE)
    (bin_dir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_TAGS="odds-data-2026-W44",
               FAKE_BOOK=book, GITHUB_REPOSITORY="o/r")
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "site_inputs.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    if ok:
        assert len(list((tmp_path / "book").glob("book_*.json.gz"))) == n
    else:
        assert "ledger incomplete" in r.stderr


def test_site_build_saves_ledger_before_publishing_and_never_overwrites():
    steps = _yaml("site-build.yml")["jobs"]["build"]["steps"]
    names = [st.get("name") for st in steps]
    save = names.index("Save synthetic-book ledger (new files only, never overwritten)")
    assert names.index("Build site") < save < names.index("Publish")
    run = steps[save]["run"]
    assert "gh release upload v4-book" in run and "--clobber" not in run
    assert "--book-dir book --book-out book_new" in steps[names.index("Build site")]["run"]


# ---------------------------------------------------------------- owner-approved 2026-10-29: balanced tier, Top-25 CFB closes

LONDON = _ts(2026, 11, 8, 14, 30)                       # Sunday international NFL kickoff (9:30 ET)


def test_sunday_morning_window_gives_international_games_close_attempts():
    slots = [t for t in cron_plan.run_times(WEEK) if LONDON - 90 * 60 <= t <= LONDON - 5 * 60]
    assert len(slots) >= 4
    n, closed, total = simulate(NFL + [LONDON], "balanced", delay_max_min=50)
    assert closed == total                                # every NFL kickoff incl. the London game


def _daily(kicks, tier, seed=3):
    rng = random.Random(seed)
    allk = [k + w * 7 * 86400 for w in range(3) for k in kicks]
    last, per_day = None, {}
    for t in [t for w in range(3) for t in cron_plan.run_times(WEEK + w * 7 * 86400)]:
        t2 = t + rng.randint(25 * 60, 50 * 60)
        if SP.due(t2, allk, last, tier)[0]:
            last = t2
            if WEEK + 7 * 86400 <= t2 < WEEK + 14 * 86400:
                per_day[t2 // 86400] = per_day.get(t2 // 86400, 0) + 3
    return per_day


TOP25 = [_ts(2026, 11, 14, 17, 0)] * 3 + [_ts(2026, 11, 14, 20, 30)] * 2 + [_ts(2026, 11, 15, 0, 30)] * 2 + \
        [_ts(2026, 11, 15, 1, 0)]


def test_balanced_tier_fits_approved_budget_and_daily_limit():
    n_nfl, c_nfl, k_nfl = simulate(NFL + [LONDON], "balanced", delay_max_min=50)
    n_cfb, c_cfb, k_cfb = simulate(TOP25, "balanced", delay_max_min=50)
    monthly = 3 * (n_nfl + n_cfb) * 4.35
    assert 500 < monthly <= 900, monthly                   # ~757 planned; recommended monthly cap 1,000
    assert c_nfl == k_nfl and c_cfb == k_cfb               # every NFL and Top-25 close observed
    a, b = _daily(NFL + [LONDON], "balanced"), _daily(TOP25, "balanced")
    peak = max(a.get(d, 0) + b.get(d, 0) for d in set(a) | set(b))
    assert peak <= 60, peak                                # inside the existing 60 credits/day limit


def test_tier_rules_are_ordered_and_closes_always_on():
    for name, t in SP.TIERS.items():
        assert all(h > 0 and g > 0 for h, g in t["rules"]), name
    # a close is due for every tier regardless of cadence rules
    k = NOW + 30 * 60
    for name in SP.TIERS:
        assert SP.due(NOW, [k], NOW - 3600, name)[0], name


def test_test_tier_rules_unchanged_by_the_rewrite():
    assert SP.TIERS["test"]["rules"] == [(24, 12), (72, 24)]
    assert SP.DEFAULT_TIER == "test"                       # unset V4_TIER never means more spend


def _poll(fetched_at):
    return {"source": "CollegeFootballData API", "fetched_at": fetched_at,
            "polls": [{"season": 2026, "season_type": "regular", "week": 10,
                       "ranks": [{"rank": 1, "school": "Ohio State"}, {"rank": 7, "school": "Texas"}]}],
            "teams": [{"school": s, "mascot": m, "alternate_names": []} for s, m in
                      (("Ohio State", "Buckeyes"), ("Texas", "Longhorns"), ("Purdue", "Boilermakers"),
                       ("Iowa", "Hawkeyes"), ("Rice", "Owls"), ("Tulane", "Green Wave"))]}


def test_cfb_priority_kickoffs_are_top25_plus_unknown_never_unranked():
    sat = _ts(2026, 11, 14, 17, 0)
    events = [["e1", sat, "Ohio State Buckeyes", "Purdue Boilermakers"],     # #1 vs unranked -> priority
              ["e2", sat + 3600, "Iowa Hawkeyes", "Purdue Boilermakers"],    # unranked vs unranked -> not
              ["e3", sat + 7200, "Rice Owls", "Texas Longhorns"],            # unranked vs #7 -> priority
              ["e4", sat + 9000, "Nowhere Tech Owls", "Tulane Green Wave"]]  # unmatched name -> unknown -> priority
    ks, mix = SP.priority_kickoffs("americanfootball_ncaaf", events, [_poll(sat - 4 * 86400)], sat - 86400)
    assert ks == [sat, sat + 7200, sat + 9000] and mix == {"top25": 2, "unknown": 1, "unranked": 1}
    # no poll at all: nothing is dropped (all count as unknown)
    ks, mix = SP.priority_kickoffs("americanfootball_ncaaf", events, [], sat - 86400)
    assert len(ks) == 4 and mix["unknown"] == 4
    # NFL: every game
    ks, _ = SP.priority_kickoffs("americanfootball_nfl", events, [], sat - 86400)
    assert len(ks) == 4


def test_unranked_cfb_close_is_skipped_but_top25_close_is_taken():
    sat = _ts(2026, 11, 14, 17, 0)
    events = [["e2", sat, "Iowa Hawkeyes", "Purdue Boilermakers"], ["e1", sat + 7200, "Ohio State Buckeyes", "Purdue Boilermakers"]]
    ks, _ = SP.priority_kickoffs("americanfootball_ncaaf", events, [_poll(sat - 4 * 86400)], sat - 3600)
    last = sat - 3600                       # recent fetch: no cadence rule is due, only a close could be
    assert not SP.due(sat - 30 * 60, ks, last, "balanced")[0]          # unranked game 30 min out: no fetch
    assert SP.due(sat + 7200 - 30 * 60, ks, sat + 3600, "balanced")[0]  # Top-25 game 30 min out: close fetch
    assert SP.due(sat + 7200 - 30 * 60, ks, sat + 3600, "balanced")[1].startswith("close")


@pytest.mark.parametrize("home,fetches", [("Iowa Hawkeyes", 0), ("Ohio State Buckeyes", 1)])
def test_collector_end_to_end_uses_top25_priority(tmp_path, monkeypatch, home, fetches):
    """Through the collector's run() as the workflow uses it: a CFB close 33 min out is fetched only for a
    game involving a Top-25 team (rankings read from state/rankings)."""
    state = tmp_path / "state"
    (state / "rankings").mkdir(parents=True)
    json.dump(_poll(NOW - 4 * 86400), open(state / "rankings" / "rankings_x.json", "w"))
    kick = NOW + 33 * 60
    json.dump({"americanfootball_ncaaf": {"odds_at": NOW - 3600, "kickoffs": [kick],
                                          "events": [["c1", kick, home, "Purdue Boilermakers"]]},
               "americanfootball_nfl": {"odds_at": NOW - 3600, "kickoffs": [], "events": []}},
              open(state / "collector_state.json", "w"))
    e = env(V4_TIER="balanced")
    for k, v in e.items():
        monkeypatch.setenv(k, v)                       # the key is read from the process environment
    s = AR.run(str(state), str(tmp_path / "out"), env=e, clock=lambda: NOW, opener=Upstream(lambda: NOW))
    assert s["status"] == "ok" and s["tier"] == "balanced" and s["rankings_snapshots"] == 1
    assert len(s["fetches"]) == fetches
    assert s["priority"]["americanfootball_ncaaf"] == ({"top25": 0, "unknown": 0, "unranked": 1} if fetches == 0
                                                       else {"top25": 1, "unknown": 0, "unranked": 0})
    assert s["caps"]["v4_monthly_cap"] == 500        # choosing a tier never raises the cap by itself
    if fetches:
        st = json.load(open(tmp_path / "out" / "collector_state.json"))
        assert all(len(e) == 4 for e in st["americanfootball_ncaaf"]["events"])   # games stored for next time


def test_balanced_tier_still_fails_closed_at_the_cap(run_chain, monkeypatch):
    go, up, tmp = run_chain
    s, _ = go(env(V4_TIER="balanced", V4_MONTHLY_CAP="3"))       # room for one 3-credit fetch only
    assert len(s["fetches"]) == 1 and len(s["refused"]) == 1 and s["usage"]["v4"] == 3


# ---------------------------------------------------------------- fail-closed provider balance preflight

class _Prov:
    """Provider fake: /v4/sports is the free balance check; odds cost 3."""
    def __init__(self, remaining=15000, sports_mode="ok"):
        self.remaining, self.mode, self.calls = remaining, sports_mode, []

    def __call__(self, path, q):
        self.calls.append(path)
        if path == "/v4/sports":
            if self.mode == "raise":
                raise OSError("network down")
            if self.mode == "500":
                return 500, {}, b"{}"
            if self.mode == "no_header":
                return 200, {"x-requests-last": "0"}, b"[]"
            return 200, {"x-requests-last": "0", "x-requests-remaining": str(self.remaining),
                         "x-requests-used": str(20000 - self.remaining)}, b"[]"
        self.remaining -= 3
        return 200, {"x-requests-last": "3", "x-requests-remaining": str(self.remaining),
                     "x-requests-used": str(20000 - self.remaining)}, json.dumps(payload(path.split("/")[3], NOW)).encode()


def _gw(tmp_path, prov):
    cfg = dict(og.DEFAULT_CONFIG, state_dir=str(tmp_path / "gw"), live_enabled=True, socket_group=None,
               v4_monthly_cap=6000, v4_daily_cap=400, max_single_request=30)
    return og.Gateway(cfg, KEY, clock=lambda: NOW, opener=prov)


def _odds(gw):
    return gw.handle({"endpoint": "odds", "sport": "americanfootball_nfl",
                      "params": {"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}})


@pytest.mark.parametrize("mode", ["raise", "500", "no_header"])
def test_unconfirmed_balance_refuses_the_paid_request(tmp_path, mode):
    prov = _Prov(sports_mode=mode)
    gw = _gw(tmp_path, prov)
    with pytest.raises(og.Refused) as e:
        _odds(gw)
    assert e.value.status == 503 and "failing closed" in str(e.value)
    assert not any(p.endswith("/odds") for p in prov.calls)           # nothing paid was requested


def test_fresh_balance_checked_before_every_paid_request(tmp_path):
    prov = _Prov(remaining=2005)                  # first fetch leaves 2002; a second (-3) would breach 2000
    gw = _gw(tmp_path, prov)
    assert _odds(gw)[0] == 200
    with pytest.raises(og.Refused) as e:
        _odds(gw)
    assert e.value.status == 402 and "V3 floor" in str(e.value)
    assert prov.calls == ["/v4/sports", "/v4/sports/americanfootball_nfl/odds", "/v4/sports"]


def test_first_request_of_the_month_cannot_skip_the_floor(tmp_path):
    # no ledger row this month (the old code then skipped the V3 check); the preflight shows 2,001 left
    prov = _Prov(remaining=2001)
    gw = _gw(tmp_path, prov)
    with pytest.raises(og.Refused) as e:
        _odds(gw)
    assert "V3 floor" in str(e.value) and prov.calls == ["/v4/sports"]


def test_preflight_is_recorded_as_free_and_v3_drop_between_runs_is_seen(tmp_path):
    prov = _Prov(remaining=5000)
    gw = _gw(tmp_path, prov)
    assert _odds(gw)[0] == 200
    prov.remaining = 2002                         # V3 spent ~3,000 credits since our last fetch
    with pytest.raises(og.Refused):
        _odds(gw)
    rows = gw.gate.conn.execute("SELECT endpoint, est_cost, actual_cost FROM credit_ledger ORDER BY rowid").fetchall()
    assert [r for r in rows if r[0] == "sports"] == [("sports", 0, 0), ("sports", 0, 0)]


# ---------------------------------------------------------------- EXPANDED tier (built 2026-10-29; not active)

def test_expanded_closes_are_never_delayed_by_quiet_backoff():
    k = NOW + 30 * 60
    assert SP.due(NOW, [k], NOW - 3600, "expanded", change_rate=0.0)[1].startswith("close")


def test_expanded_quiet_backoff_only_far_from_kickoff():
    far, near = NOW + 50 * 3600, NOW + 20 * 3600
    # 2.5 h since last fetch, next kickoff 50 h away: rule (72 h / 2 h) due when active, not when quiet (x2 -> 4 h)
    assert SP.due(NOW, [far], NOW - 2.5 * 3600, "expanded", change_rate=0.10)[0]
    assert not SP.due(NOW, [far], NOW - 2.5 * 3600, "expanded", change_rate=0.01)[0]
    # within 24 h of kickoff the backoff never applies
    assert SP.due(NOW, [near], NOW - 1.1 * 3600, "expanded", change_rate=0.0)[0]


def test_expanded_calendar_windows():
    sun_night = _ts(2026, 11, 8, 23, 30)            # Sunday 23:30 UTC: NFL openers window
    nxt = sun_night + 4 * 86400                     # next game Thursday, 96 h away (no kickoff rule <= 72 h)
    ok, why = SP.due(sun_night, [nxt], sun_night - 1.2 * 3600, "expanded", sport="americanfootball_nfl")
    assert ok and why.startswith("window")
    # CFB has its own Sunday-evening openers window (17-24 UTC)
    assert SP.due(sun_night, [nxt], sun_night - 1.2 * 3600, "expanded", sport="americanfootball_ncaaf")[1].startswith("window")
    sun_noon = _ts(2026, 11, 8, 12, 30)             # Sunday 12:30 UTC: neither sport's openers window yet
    assert not SP.due(sun_noon, [sun_noon + 100 * 3600], sun_noon - 1.2 * 3600, "expanded",
                      sport="americanfootball_ncaaf")[0]
    wed_noon = _ts(2026, 11, 11, 12, 30)            # Wednesday 12:30 UTC: no window for NFL
    assert not SP.due(wed_noon, [wed_noon + 100 * 3600], wed_noon - 1.2 * 3600, "expanded",
                      sport="americanfootball_nfl")[0]
    wed_eve = _ts(2026, 11, 11, 20, 30)             # Wednesday 20:30 UTC: NFL injury-report window
    assert SP.due(wed_eve, [wed_eve + 100 * 3600], wed_eve - 1.2 * 3600, "expanded",
                  sport="americanfootball_nfl")[1].startswith("window")


def test_piggyback_rides_only_with_another_fetch_and_only_in_expanded():
    k = NOW + 100 * 3600
    assert SP.piggyback(NOW, [k], NOW - 1.5 * 3600, "expanded")[0]
    assert not SP.piggyback(NOW, [k], NOW - 0.5 * 3600, "expanded")[0]        # fetched < 1 h ago
    assert not SP.piggyback(NOW, [NOW + 200 * 3600], NOW - 5 * 3600, "expanded")[0]   # nothing within 7 days
    for tier in ("test", "balanced"):
        assert not SP.piggyback(NOW, [k], NOW - 5 * 3600, tier)[0]


def _expanded_week(change_rate=None):
    """NFL (incl. London) + Top-25 CFB over the expanded grid with piggyback, 3 weeks, middle scored."""
    rng = random.Random(5)
    sports = {"nfl": [k + w * 7 * 86400 for w in range(3) for k in NFL + [LONDON]],
              "cfb": [k + w * 7 * 86400 for w in range(3) for k in TOP25]}
    last = {s: None for s in sports}
    fetches = {s: [] for s in sports}
    for t in [t for w in range(3) for t in cron_plan.run_times(WEEK + w * 7 * 86400, "expanded")]:
        t2 = t + rng.randint(5 * 60, 45 * 60)
        dec = {s: SP.due(t2, ks, last[s], "expanded", sport="americanfootball_nfl" if s == "nfl" else
                         "americanfootball_ncaaf", change_rate=change_rate)[0] for s, ks in sports.items()}
        if any(dec.values()):
            for s in sports:
                dec[s] = dec[s] or SP.piggyback(t2, sports[s], last[s], "expanded")[0]
        for s, ok in dec.items():
            if ok:
                fetches[s].append(t2)
                last[s] = t2
    lo, hi = WEEK + 7 * 86400, WEEK + 14 * 86400
    return sports, fetches, lo, hi


@pytest.mark.parametrize("change_rate", [None, 0.02])
def test_expanded_budget_closes_and_snapshots(change_rate):
    sports, fetches, lo, hi = _expanded_week(change_rate)
    all_f = sorted(f for s in fetches for f in fetches[s])
    monthly = 3 * sum(1 for f in all_f if lo <= f < hi) * 4.35
    assert 2300 <= monthly <= 5000, monthly    # dense enough, under the 5,000 cap (quiet backoff lowers spend)
    peak24 = max(3 * sum(1 for u in all_f if t - 86400 < u <= t) for t in all_f if lo <= t < hi)
    assert peak24 <= 250, peak24                               # proposed V4_DAILY_CAP 250 (rolling 24 h)
    for s, ks in sports.items():
        mid = sorted({k for k in ks if lo <= k < hi})
        closed = sum(1 for k in mid if any(0 < k - f <= 45 * 60 for f in fetches[s]))
        assert closed == len(mid), (s, closed, len(mid))       # every NFL and Top-25 close
        f72 = sorted(sum(1 for f in fetches[s] if 0 < k - f <= 72 * 3600) for k in mid)
        assert f72[len(f72) // 2] >= 40, (s, f72)              # dense final-72 h history per game


def test_other_tiers_never_run_expanded_only_cron_entries():
    import calendar
    wk = calendar.timegm((2026, 11, 1, 0, 0, 0))
    for tier in ("test", "balanced", None):
        assert cron_plan.run_times(wk, tier) == cron_plan.run_times(wk)



@pytest.mark.parametrize("now_off,last,kicks,reasons,want", [
    (0, None, [], ["final 72h"], True),                                 # first build
    (0, -10 * 60, [], [], False),                                       # no new odds: never
    (0, -179 * 60, [], ["final 7d"], False),                            # quiet: at most every 3 h
    (0, -180 * 60, [], ["final 7d"], True),
    (0, -44 * 60, [5 * 3600], ["final 6h"], False),                     # final 6 h: every 45 min
    (0, -45 * 60, [5 * 3600], ["final 6h"], True),
    (0, -44 * 60, [7 * 3600], ["final 24h"], False),                    # kickoff 7 h away: quiet rule
    (0, -60, [20 * 60], ["close (kickoff in 20 min)"], True),           # always right after a close
])
def test_site_due_policy(now_off, last, kicks, reasons, want):
    ok, why = SP.site_due(NOW + now_off, None if last is None else NOW + last,
                                       [NOW + k for k in kicks], reasons)
    assert ok == want, why


@pytest.mark.parametrize("count,last,reasons,want", [
    (SP.SITE_MONTH_SOFT - 1, -180 * 60, ["final 7d"], True),
    (SP.SITE_MONTH_SOFT, -120 * 60, ["final 7d"], False),               # soft limit: every 6 h ...
    (SP.SITE_MONTH_SOFT, -6 * 3600, ["final 7d"], True),
    (SP.SITE_MONTH_SOFT, -60, ["close (kickoff in 20 min)"], True),     # ... and still after closes
    (SP.SITE_MONTH_HARD, -86400, ["close (kickoff in 20 min)"], False),  # hard limit: nothing
])
def test_site_due_monthly_limits(count, last, reasons, want):
    ok, why = SP.site_due(NOW, NOW + last, [NOW + 3 * 3600], reasons, count)
    assert ok == want, why
    assert SP.SITE_MONTH_HARD + 20 < 500          # + cfb-rankings and manual builds stay under Cloudflare's 500


def test_worst_case_actions_minutes_stay_under_the_free_2000():
    # every scheduled expanded collect run (1 min) + every allowed build at 2 billed minutes + daily analysis +
    # rankings (9 runs + 9 rebuilds at 2 min) + 40 CI pushes at 4 min + 10 manual minutes
    import calendar
    from collect_v4 import cron_plan
    collect = round(len(cron_plan.run_times(calendar.timegm((2026, 11, 2, 0, 0, 0)), "expanded")) * 4.35)
    worst = collect + SP.SITE_MONTH_HARD * 2 + 31 + 27 + 40 * 4 + 10
    assert worst <= 1900, worst


def test_site_build_count_resets_each_month(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    kick = NOW + 3 * 86400
    json.dump({"_site_at": NOW - 86400, "_site_month": "1999-12", "_site_builds": SP.SITE_MONTH_HARD,
               "americanfootball_nfl": {"odds_at": NOW - 86400, "kickoffs": [kick], "events": [["n1", kick, "H", "A"]]},
               "americanfootball_ncaaf": {"odds_at": NOW - 600, "kickoffs": [], "events": []}},
              open(state / "collector_state.json", "w"))
    e = env(V4_TIER="expanded")
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    s = AR.run(str(state), str(tmp_path / "out"), env=e, clock=lambda: NOW, opener=Upstream(lambda: NOW))
    assert len(s["fetches"]) >= 1 and not (tmp_path / "out" / "site_skip").exists()
    st = json.load(open(tmp_path / "out" / "collector_state.json"))
    assert st["_site_month"] == time.strftime("%Y-%m", time.gmtime(NOW)) and st["_site_builds"] == 1


@pytest.mark.parametrize("tier", ["balanced", "expanded"])
def test_dashboard_rebuild_policy_applies_to_every_tier(tmp_path, monkeypatch, tier):
    state = tmp_path / "state"
    state.mkdir()
    kick = NOW + 3 * 86400
    json.dump({"_site_at": NOW - 30 * 60,
               "americanfootball_nfl": {"odds_at": NOW - 86400, "kickoffs": [kick], "events": [["n1", kick, "H", "A"]]},
               "americanfootball_ncaaf": {"odds_at": NOW - 600, "kickoffs": [], "events": []}},
              open(state / "collector_state.json", "w"))
    e = env(V4_TIER=tier)
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    s = AR.run(str(state), str(tmp_path / "out"), env=e, clock=lambda: NOW, opener=Upstream(lambda: NOW))
    assert len(s["fetches"]) >= 1
    assert (tmp_path / "out" / "site_skip").exists() and s["site"].startswith("skipped")
    assert json.load(open(tmp_path / "out" / "collector_state.json"))["_site_at"] == NOW - 30 * 60


@pytest.mark.parametrize("mins_to_kick,skipped", [(180, True), (30, False)])
def test_expanded_dashboard_rebuild_throttle_but_always_after_a_close(tmp_path, monkeypatch, mins_to_kick, skipped):
    state = tmp_path / "state"
    state.mkdir()
    kick = NOW + mins_to_kick * 60
    json.dump({"_site_at": NOW - 10 * 60,
               "americanfootball_nfl": {"odds_at": NOW - 3600, "kickoffs": [kick], "events": [["n1", kick, "H", "A"]]},
               "americanfootball_ncaaf": {"odds_at": NOW - 600, "kickoffs": [], "events": []}},
              open(state / "collector_state.json", "w"))
    e = env(V4_TIER="expanded")
    for k, v in e.items():
        monkeypatch.setenv(k, v)
    s = AR.run(str(state), str(tmp_path / "out"), env=e, clock=lambda: NOW, opener=Upstream(lambda: NOW))
    assert len(s["fetches"]) >= 1
    assert (tmp_path / "out" / "site_skip").exists() == skipped


def test_credit_gate_refuses_paid_request_when_balance_unknown(tmp_path):
    import sqlite3
    gate = og.credits.CreditGate(sqlite3.connect(":memory:"), og.credits.Budget(
        v4_monthly_cap=6000, v4_daily_cap=400, v3_floor_remaining=2000, max_single_request=30), clock=lambda: NOW)
    with pytest.raises(og.credits.CreditRefused, match="balance unknown"):
        gate.authorize("odds", {"p": 1}, 3, require_remaining=True)
    gate.authorize("odds", {"p": 1}, 3)                     # legacy callers (no preflight) unchanged


@pytest.mark.parametrize("since_min,want", [(58, True), (48, True), (47, False)])
def test_hourly_rule_tolerates_github_start_jitter(since_min, want):
    # 2026-10-29: runs scheduled an hour apart started 58 min apart; the hourly rule must still fetch
    kick = NOW + 14 * 3600                                   # final 24 h: expanded rule is 1 h
    ok, why = SP.due(NOW, [kick], NOW - since_min * 60, "expanded", sport="americanfootball_nfl")
    assert ok == want, why


def test_gap_tolerance_is_bounded():
    assert SP._gap_met(0.25 - 0.05, 0.25) and not SP._gap_met(0.25 - 0.051, 0.25)   # 15-min gap: 3 min
    assert SP._gap_met(4 - 0.25, 4) and not SP._gap_met(4 - 0.26, 4)               # long gaps: at most 15 min


@pytest.mark.parametrize("dec,ok,n,off", [(None, True, 0, 0), ("2:2", True, 2, 0), ("3:2", False, None, 0),
                                          ("2:2", True, 2, 3)])
def test_site_inputs_need_tracker_decisions(tmp_path, dec, ok, n, off):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH_SITE)
    (bin_dir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_TAGS="odds-data-2026-W44",
               GITHUB_REPOSITORY="o/r", FAKE_OFF=str(off), **({"FAKE_DEC": dec} if dec else {}))
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "site_inputs.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    if ok:
        assert len(list((tmp_path / "need").rglob("decisions_*.json.gz"))) == n
        assert len(list((tmp_path / "need").rglob("official_dec_*.json.gz"))) == off      # Official Needs decisions too


def test_need_tracker_records_saved_before_publishing_never_overwritten_and_no_secrets():
    steps = _yaml("site-build.yml")["jobs"]["build"]["steps"]
    names = [s.get("name") for s in steps]
    save = names.index("Save Need-tracker and settlement records (new files only, never overwritten)")
    assert names.index("Build site") < save < names.index("Publish")
    run = steps[save]["run"]
    assert "--clobber" not in run and "gh release upload" in run
    assert set(steps[save]["env"]) == {"GH_TOKEN"}
    assert "--need-dir need --results-dir results --need-out need_new" in steps[names.index("Build site")]["run"]
    assert "--settle-dir settle" in steps[names.index("Build site")]["run"]
    analyze = _wf("odds-analyze.yml")
    assert "secrets." not in analyze
    nfl = [s for s in _yaml("odds-analyze.yml")["jobs"]["analyze"]["steps"] if "data_v4.results" in s.get("run", "")]
    assert len(nfl) == 1 and "--sport nfl" in nfl[0]["run"] and "--clobber" not in nfl[0]["run"]


def test_site_inputs_retries_github_temporary_errors_but_not_missing_releases(tmp_path):
    # 2026-11-07/09: three site builds failed on a single HTTP 500 from GitHub's release-asset download
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh.real").write_text(FAKE_GH_SITE)
    (bin_dir / "gh.real").chmod(0o755)
    (bin_dir / "gh").write_text(r"""#!/bin/bash
if [[ "$1 $2" == "release download" && "$*" == *odds-data-* ]]; then
  [[ "$3" == --clobber ]] || { echo "retry must pass --clobber" >&2; exit 9; }
  n=$(cat "$FLAKY" 2>/dev/null || echo 0); echo $((n+1)) > "$FLAKY"
  (( n < 1 )) && { echo "HTTP 500 (https://api.github.com/x)" >&2; exit 1; }
fi
exec "$(dirname "$0")/gh.real" "$@"
""")
    (bin_dir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_TAGS="odds-data-2026-W44",
               GITHUB_REPOSITORY="o/r", FLAKY=str(tmp_path / "flaky"))
    r = subprocess.run(["bash", os.path.join(REPO, ".github", "scripts", "site_inputs.sh")], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GitHub temporary error (attempt 1 of 5)" in r.stderr
    assert (tmp_path / "flaky").read_text().strip() == "2"            # failed once, succeeded on the retry
    assert "no AP-poll snapshots yet" in r.stdout                      # a missing release (non-5xx) is not retried
