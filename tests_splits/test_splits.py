"""Public splits plays (splits_v4): parsers on small made-up samples of each site's layout, slate timing, matching,
the frozen play rule, grading, and the workflow wiring (no secrets in the splits job)."""
import json
import os
import time

import yaml

from analysis_v4 import book_moves as BM  # noqa: F401  (imported by plays)
from market_v4 import normalize, store
from splits_v4 import match as M
from splits_v4 import plays as P
from splits_v4 import slates as SL
from splits_v4 import snapshot as SN
from splits_v4 import sources as S

ROOT = os.path.join(os.path.dirname(__file__), "..")
NFL = "americanfootball_nfl"


# ------------------------------------------------------------------ parsers (made-up samples in each site's layout)
DK = '''<div class="tb-se rounded-lg"><h5 class="t"><a href="#"><img/> WAS Commanders @ <img/>NY Giants </a></h5>
<span>Thu 11/12 · 8:15 PM ET</span><div>Selection</div><div>Odds</div><div>Handle</div><div>Bets</div>
<div>Spread</div><div>NY Giants</div><div>-2.5</div><div>−110</div><div>88%</div><div>73%</div>
<div>WAS Commanders</div><div>+2.5</div><div>−110</div><div>12%</div><div>27%</div>
<div>Total</div><div>Over 40.5</div><div>−118</div><div>66%</div><div>68%</div>
<div>Under 40.5</div><div>−102</div><div>34%</div><div>32%</div></div>'''


def test_draftkings():
    r = S.parse_draftkings(DK, "nfl", 2026)[0]
    assert (r["away"], r["home"]) == ("WAS Commanders", "NY Giants")
    assert r["spread"]["home"] == {"line": -2.5, "price": -110, "money": 88.0, "bets": 73.0}
    assert r["total"]["over"]["bets"] == 68.0 and r["total"]["under"]["line"] == 40.5
    assert r["kickoff"] == time.mktime(time.strptime("2026-11-13 01:15", "%Y-%m-%d %H:%M")) - time.timezone


SO = '''<div class="trend-card consensus consensus-table-spread--0 "><span class="team-name"> <span>Commanders</span></span>
<span data-role="localtime" data-value="2026-11-13T01:15:00Z"></span><span class="team-name"> <span>Giants</span></span>
WAS (+2.5) % of Bets NYG (-2.5) 51% 49% 67% 33% % of Money</div>
<div class="trend-card consensus consensus-table-total--0 "><span class="team-name"> <span>Commanders</span></span>
<span data-role="localtime" data-value="2026-11-13T01:15:00Z"></span><span class="team-name"> <span>Giants</span></span>
Over (o40.5) % of Bets Under (u40.5) 60% 40% 44% 56% % of Money</div>'''


def test_scoresandodds():
    r = S.parse_scoresandodds(SO, "nfl")[0]
    assert r["spread"]["away"] == {"bets": 51.0, "money": 67.0, "line": 2.5} and r["spread"]["home"]["bets"] == 49.0
    assert r["total"]["over"] == {"bets": 60.0, "money": 44.0, "line": 40.5}


def test_sportsbettingdime():
    body = json.dumps({"data": [{"scheduled": "2026-11-13T01:15:00+00:00", "competitors": {
        "away": {"market": "Washington", "name": "Commanders", "rotation_number": 113},
        "home": {"market": "New York", "name": "Giants", "rotation_number": 114}},
        "bettingSplits": {"spread": {"away": {"betsPercentage": 50.9, "stakePercentage": 64.4},
                                     "home": {"betsPercentage": 49.1, "stakePercentage": 35.6}}}}]})
    r = S.parse_sportsbettingdime(body, "nfl")[0]
    assert r["away"] == "Washington Commanders" and r["rot"] == [113, 114] and r["spread"]["home"]["bets"] == 49.1
    assert r["total"] is None


MGM = '''<caption class="match-date"> NOV 12, 8:15 PM ET </caption>
<tr class="tr" style="x"> data-event-name="Washington Commanders @ New York Giants" data-match-time="2026-11-12 20:15:00"
<span class="bet-type ">+2.0</span><strong class="bet-point">-108</strong><div class="bet-percent"> 17% </div>
<span class="bet-type ">O 41.0</span><div class="bet-percent"> 62% </div><div class="bet-percent"> 31% </div>
<tr class="tr" style="y"><span class="bet-type ">-2.0</span><div class="bet-percent"> 83% </div>
<span class="bet-type ">U 41.0</span><div class="bet-percent"> 38% </div><div class="bet-percent"> 69% </div>'''


def test_betmgm():
    r = S.parse_betmgm(MGM, "nfl")[0]
    assert r["spread"]["away"] == {"bets": 17.0, "line": 2.0} and r["spread"]["home"]["bets"] == 83.0
    assert r["total"]["over"]["bets"] == 62.0 and r["total"]["under"]["bets"] == 38.0
    assert r["kickoff"] == 1794532500.0


WT = '''<tr class="head1"><td><div class="headTD2">NFL WEEK 10</div></td></tr>
<tr class="reg"><th class="time c1"><div>11/12</div><div class="time">5:15p</div></th>
<th class="gnum c2"><div>113</div><div>114</div></th><th class="team c3"><div>WAS</div><div>NYG</div></th>
<td class="book"><div>o68%</div><div>73%</div></td><td class="book"><div>o66%</div><div>88%</div></td>
<td class="book"><div>43u-11</div><div>-1-12</div></td><td class="book"><div>40½o-18</div><div>-2½-10</div></td>
<td class="book"><div>41</div><div>-2-10</div></td><td class="book"><div>41</div><div>-2½-10</div></td></tr>'''


def test_wagertalk_numbers_and_circa():
    r = S.parse_wagertalk(WT, 2026)[0]
    assert r["sport"] == "nfl" and r["rot"] == [113, 114] and r["kickoff"] == 1794532500.0
    assert r["spread"]["home"] == {"bets": 73.0, "money": 88.0} and r["spread"]["away"]["bets"] == 27.0
    assert r["total"]["over"]["bets"] == 68.0 and r["total"]["under"]["money"] == 34.0
    assert r["circa"] == {"total": 41.0, "total_juice": "", "spread_home": -2.5, "spread_away": 2.5}
    assert r["open"]["total"] == 43.0 and r["open"]["spread_home"] == -1.0


def test_covers_contest_picks():
    h = "<td>NFL Ne Det Sun. Nov 15 9:30 am ET 24% 76% +2.5 -2.5 140 445 Details</td>"
    r = S.parse_covers(h, "nfl", 2026)[0]
    assert (r["away"], r["home"]) == ("Ne", "Det") and r["spread"]["home"] == {"picks": 76.0}


def test_collect_isolates_failures():
    def get(url):
        if "draftkings" in url:
            raise OSError("down")
        return ""
    out = S.collect(2026, get=get, sports=("nfl",))
    assert out["draftkings"]["status"].startswith("ERROR") and out["covers"]["status"].startswith("EMPTY")


# ------------------------------------------------------------------ slates and matching
def test_slates_and_due():
    t = 1_800_000_000
    ks = [t, t + 1800, t + 3 * 3600, t + 3 * 3600 + 1200, t + 7 * 3600]
    assert SL.slates(ks) == [t, t + 3 * 3600, t + 7 * 3600]
    assert SL.due(t - 95 * 60, ks, [])[0] is None
    assert SL.due(t - 85 * 60, ks, []) == (t, "ON TIME")
    assert SL.due(t - 60 * 60, ks, []) == (t, "ON TIME")
    assert SL.due(t - 30 * 60, ks, []) == (t, "LATE")
    assert SL.due(t - 60 * 60, ks, [t])[0] is None                    # taken once only
    assert [e[0] for e in SL.members([["a", t + 600, "", ""], ["b", t + 3 * 3600, "", ""]], t)] == ["a"]


def test_name_matching():
    assert M.name_score("NY Giants", "New York Giants", "nfl") > 0 and M.name_score("NYG", "New York Giants", "nfl") == 3
    assert M.name_score("NYJ", "New York Giants", "nfl") == 0
    assert M.name_score("Louisiana-Monroe", "UL Monroe Warhawks", "cfb") > 0
    assert M.name_score("USF", "South Florida Bulls", "cfb") > 0 and M.name_score("Memphis", "Memphis Tigers", "cfb") == 2.5
    evs = [{"event_id": "e1", "sport": "cfb", "kickoff": 1000.0, "home": "South Florida Bulls", "away": "Memphis Tigers"},
           {"event_id": "e2", "sport": "cfb", "kickoff": 1000.0, "home": "UL Monroe Warhawks", "away": "Louisiana Ragin Cajuns"}]
    got, un = M.match_rows([{"sport": "cfb", "away": "Memphis", "home": "South Florida", "kickoff": 1200.0, "rot": None},
                            {"sport": "cfb", "away": "Memphis", "home": "South Florida", "kickoff": 99999.0, "rot": None},
                            {"sport": "cfb", "away": "ULL", "home": "ULM", "kickoff": None, "rot": [7, 8]}], evs, {(7, 8): "e2"})
    assert set(got) == {"e1", "e2"} and un == 1                       # wrong kickoff -> unmatched, never guessed


# ------------------------------------------------------------------ the play rule
def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _store(pin_home_prices, kick, t0):
    conn = store.connect()
    for i, ph in enumerate(pin_home_prices):
        t = t0 + i * 3600
        bks = [{"key": b, "last_update": _iso(t), "markets": [
            {"key": "spreads", "last_update": _iso(t), "outcomes": [
                {"name": "Home", "price": hp, "point": -3.0}, {"name": "Away", "price": -100 if hp < -110 else -120 if hp > -110 else -110,
                                                               "point": 3.0}]},
            {"key": "totals", "last_update": _iso(t), "outcomes": [
                {"name": "Over", "price": -110, "point": 44.5}, {"name": "Under", "price": -110, "point": 44.5}]}]}
               for b, hp in (("pinnacle", ph), ("draftkings", -110), ("fanduel", -105))]
        q, an = normalize.normalize([{"id": "g1", "sport_key": NFL, "commence_time": _iso(kick), "home_team": "Home",
                                      "away_team": "Away", "bookmakers": bks}], _iso(t))
        store.ingest(conn, q, an, _iso(t), NFL, "odds", "fp")
    return conn


def _snap(t, home_bets=(75, 70, 80)):
    g = {"draftkings": {"spread": {"home": {"bets": home_bets[0]}, "away": {"bets": 100 - home_bets[0]}}},
         "scoresandodds": {"spread": {"home": {"bets": home_bets[1]}, "away": {"bets": 100 - home_bets[1]}}},
         "sportsbettingdime": {"spread": {"home": {"bets": 10}, "away": {"bets": 90}}},     # same feed: never counted twice
         "betmgm": {"spread": {"home": {"bets": home_bets[2]}, "away": {"bets": 100 - home_bets[2]}}}}
    return {"taken_at": t, "timing": "ON TIME", "events": [{"event_id": "g1", "sport": "nfl", "kickoff": t + 3600,
                                                            "home": "Home", "away": "Away"}], "games": {"g1": g}}


def test_public_side_counts_each_source_once():
    side, src, _ = P.public_side(_snap(0)["games"]["g1"], "spread")
    assert side == "home" and [s[1] for s in src] == ["draftkings", "scoresandodds", "betmgm"]
    assert P.public_side(_snap(0, (70, 50, 55))["games"]["g1"], "spread")[0] is None        # avg 58%: not heavy
    g = {"draftkings": {"spread": {"home": {"bets": 90}, "away": {"bets": 10}}}}
    assert P.public_side(g, "spread")[0] is None                                             # one source is not enough


def test_play_when_pinnacle_holds_or_moves_against():
    t0 = 1_800_000_000
    conn = _store([-110, -110, -105], t0 + 10 * 3600, t0)          # Pinnacle made the public (home) side CHEAPER
    rec = [r for r in P.decide(_snap(t0 + 2 * 3600 + 600), conn) if r["market"] == "spread"][0]
    assert rec["status"] == "PICK" and rec["play"]["side"] == "away" and rec["play"]["strength"] == "STRONG"
    assert rec["play"]["line"] == 3.0 and rec["public_side"] == "home"
    conn2 = _store([-110, -112, -120], t0 + 10 * 3600, t0)         # Pinnacle respected the public money
    rec2 = [r for r in P.decide(_snap(t0 + 2 * 3600 + 600), conn2) if r["market"] == "spread"][0]
    assert rec2["status"] == "NO PICK" and "Pinnacle moved toward" in rec2["reason"]


def test_circa_respecting_blocks_and_cfb_non_top25_not_counted():
    t0 = 1_800_000_000
    conn = _store([-110, -110, -110], t0 + 10 * 3600, t0)
    s = _snap(t0 + 2 * 3600 + 600)
    s["games"]["g1"]["wagertalk"] = {"circa": {"spread_home": -3.5}, "open": {"spread_home": -3.0}}
    rec = [r for r in P.decide(s, conn) if r["market"] == "spread"][0]
    assert rec["status"] == "NO PICK" and "Circa" in rec["reason"]
    s["events"][0]["sport"] = "cfb"
    assert P.decide(s, conn, {"g1": False})[0]["status"] == "NOT COUNTED"


def test_grade():
    rec = {"market": "spread", "play": {"side": "away", "line": 3.0, "price": -110}}
    assert P.grade(rec, (24, 21)) == {"result": "P", "units": 0.0}
    assert P.grade(rec, (24, 23)) == {"result": "W", "units": 0.909}
    assert P.grade(rec, (28, 21))["result"] == "L"
    assert P.grade({"market": "total", "play": {"side": "under", "line": 44.5, "price": 105}}, (21, 20)) == \
        {"result": "W", "units": 1.05}
    assert P.grade({"market": "total", "play": None}, (1, 0)) is None


def test_snapshot_with_failing_sites():
    st = {NFL: {"events": [["g1", 1000.0 + 3600, "New York Giants", "Washington Commanders"]]}}
    doc = SN.take(st, 1000.0 + 3600, "ON TIME", 1000.0, get=lambda u: (_ for _ in ()).throw(OSError("blocked")))
    assert doc["events"][0]["event_id"] == "g1" and all(v["status"].startswith("ERROR") for v in doc["sources"].values())


# ------------------------------------------------------------------ workflow wiring
def _wf(n):
    return yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", n)))


def test_splits_workflow_has_no_secrets_and_is_called_without_secrets():
    txt = open(os.path.join(ROOT, ".github", "workflows", "splits-collect.yml")).read()
    assert "secrets." not in txt and "ODDS_API_KEY" not in txt
    oc = _wf("odds-collect.yml")["jobs"]
    assert oc["splits"]["uses"] == "./.github/workflows/splits-collect.yml" and "secrets" not in oc["splits"]
    assert "splits" in oc["site"]["needs"] and "needs.collect.result == 'success'" in oc["site"]["if"]
    collect_steps = oc["collect"]["steps"]
    with_key = [s.get("name") for s in collect_steps if "ODDS_API_KEY" in json.dumps(s.get("env", {}))]
    assert with_key == ["Collect (credit-gated)"]                    # the key stays in its one step


def test_state_save_emits_splits_output(tmp_path):
    import subprocess
    out = tmp_path / "out"
    out.mkdir()
    (out / "splits_due").write_text("1800000000 ON_TIME\n")
    gho = tmp_path / "gho"
    gho.write_text("")
    env = dict(os.environ, GITHUB_OUTPUT=str(gho), PATH=f"{tmp_path}:{os.environ['PATH']}")
    (tmp_path / "gh").write_text("#!/bin/bash\nexit 0\n")
    os.chmod(tmp_path / "gh", 0o755)
    subprocess.run(["bash", os.path.join(ROOT, ".github", "scripts", "state_save.sh")], cwd=tmp_path, env=env, check=True)
    assert "splits_slate=1800000000" in gho.read_text() and "splits_timing=ON_TIME" in gho.read_text()
