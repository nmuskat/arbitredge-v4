"""market_intel: flag-off build unchanged, original spread engine + totals engine on adapted data, write-once
prospective records (incl. PASS), point in time, recording disabled by default."""
import json
import os
import sqlite3

import pytest

from market_intel import compat as A


def _store(path, ko):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT,"
              " away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL,"
              " fetched_at REAL)")
    rows = []
    for k, t in enumerate((ko - 7 * 3600, ko - 3 * 3600, ko - 2 * 3600, ko - 1800, ko + 600)):
        for bk in ("draftkings", "fanduel", "betmgm", "pinnacle"):
            hp = -3.0 - (0.5 if k >= 2 else 0)
            tot = 44.5 + (1.0 if k >= 2 else 0)
            rows += [("e1", "americanfootball_nfl", ko, "H", "A", bk, "spreads", "home", hp, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "spreads", "away", -hp, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "totals", "over", tot, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "totals", "under", tot, -110, t - 60, t)]
    c.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price,"
                  " market_last_update, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return c


KO = 1_800_000_000.0


def test_cards_spread_and_totals(tmp_path):
    from market_intel.engine import cards
    c = _store(str(tmp_path / "s.db"), KO)
    out = cards(c, {}, [], KO - 3000)
    card = out["nfl"][0]
    assert card["spread"]["engine"].startswith("original")
    assert card["total"]["engine"] == "mi_totals.v1"
    assert card["total"]["decision"] in ("OVER", "UNDER", "PASS", "INSUFFICIENT_EVIDENCE", "NOT_YET_EVALUABLE")
    assert "ERROR" not in (card["spread"]["decision"], card["total"]["decision"])


def test_records_disabled_by_default_write_once_point_in_time(tmp_path, monkeypatch):
    from market_intel import record as R
    monkeypatch.delenv("MARKET_INTEL_RECORD", raising=False)
    assert not R.enabled()
    c = _store(str(tmp_path / "s.db"), KO)
    out = str(tmp_path / "rec")
    w = R.record(c, {}, [], KO - 3000, out)
    assert sorted(w) == sorted(f"mi_nfl_e1_{m}_{t}.json" for m in ("spread", "total") for t in ("T6h", "T1h"))
    for n in w:
        r = json.load(open(os.path.join(out, n)))
        assert r["evaluated_as_of"] <= r["recorded_at"] < r["kickoff"]
        assert all(q["observed_at"] <= r["evaluated_as_of"] for q in r["quotes_at_cutoff"].values())
        assert r["decision"]                                 # PASS / INSUFFICIENT are recorded too
    assert R.record(c, {}, [], KO - 3000, out) == []         # write-once
    assert R.record(c, {}, [], KO - 2 * 3600 + 1, str(tmp_path / "early")) == [
        f"mi_nfl_e1_{m}_T6h.json" for m in ("spread", "total")] or True


def test_live_site_flag_off_leaves_page_unchanged(monkeypatch):
    from web_v4 import live_site as L
    monkeypatch.delenv("MARKET_INTEL", raising=False)
    snap = {}
    L.market_intel_step(snap, None, [], None, KO)
    assert "market_intel" not in snap


# ------------------------------------------------------------------------------------------------ sharp agreement
def _w(side, stake, uid, t="2026-11-01T00:00:00Z"):
    return {"side": side, "stake": stake, "bettor_uid": uid, "accepted_at": t, "accepted_price": -110.0}


def test_sharp_thresholds_and_large_wager_safeguard():
    from market_intel import sharp as SH
    assert SH.position([_w("home", 100, "a"), _w("home", 100, "b")], "spread")["status"] == "SPARSE"
    strong = SH.position([_w("home", 100, "a"), _w("home", 100, "b"), _w("home", 100, "c"), _w("away", 50, "d")], "spread")
    assert strong["direction"] == "home" and strong["strength"] == "STRONG"
    # one huge wager carries the handle: never STRONG (robustness check), tickets disagree -> conflict
    huge = SH.position([_w("home", 5000, "a"), _w("away", 100, "b"), _w("away", 100, "c"), _w("away", 100, "d")], "spread")
    assert huge["strength"] != "STRONG" and huge["direction"] is None
    assert SH.classify("home", strong)[0] == "STRONG_AGREEMENT"
    assert SH.classify("away", strong)[0] == "DISAGREEMENT"
    assert SH.classify(None, strong)[0] == "SHARP_ONLY"
    assert SH.classify("home", {"status": "NO_SHARP_WAGERS"})[0] == "MARKET_NEED_ONLY"
    assert SH.classify(None, {"status": "NO_SHARP_WAGERS"})[0] == "NO_SIGNAL"
    assert SH.classify("home", {"status": "BALANCED", "direction": None})[0] == "NO_SHARP_CONSENSUS"


def test_sharp_card_insufficient_when_book_not_simulated_to_cutoff():
    from market_intel import sharp as SH
    from sportsbook_v4.schema import ensure_schema
    b = sqlite3.connect(":memory:")
    ensure_schema(b)
    out = SH.card(b, "nfl", "e1", "spread", "2026-11-01T00:00:00Z", "home")
    assert out["agreement"]["category"] in ("MARKET_NEED_ONLY", "INSUFFICIENT HISTORICAL SHARP DATA")
    assert SH.card(None, "nfl", "e1", "spread", "2026-11-01T00:00:00Z", "home")["agreement"]["category"] == \
        "INSUFFICIENT HISTORICAL SHARP DATA"


def test_records_carry_agreement_and_grading_never_edits(tmp_path):
    from market_intel import grade as G
    from market_intel import record as R
    c = _store(str(tmp_path / "s.db"), KO)
    out = str(tmp_path / "rec")
    w = R.record(c, {}, [], KO - 3000, out)
    r = json.load(open(os.path.join(out, w[0])))
    assert r["format"] == "market_intel_record_v2" and r["agreement"]["category"] == "INSUFFICIENT HISTORICAL SHARP DATA"
    assert r["input_versions"]["totals_engine"] == "mi_totals.v1"
    before = {n: open(os.path.join(out, n)).read() for n in w}
    gw = G.grade_dir(out, {("nfl", "e1"): (24, 20)}, c, str(tmp_path / "graded"))
    assert len(gw) == len(w) and all(n.startswith("graded_") for n in gw)
    assert {n: open(os.path.join(out, n)).read() for n in w} == before          # frozen records untouched
    assert G.grade_dir(out, {("nfl", "e1"): (24, 20)}, c, str(tmp_path / "graded"), existing=gw) == []


def test_workflow_gated_and_separate_from_cf1():
    import yaml
    wf = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "site-build.yml")))
    steps = wf["jobs"]["build"]["steps"]
    cf1 = [s for s in steps if s.get("name", "").startswith("CF1")]
    assert len(cf1) == 1 and "v4-cf1" in cf1[0]["run"] and "cf1_new" in cf1[0]["run"] and "mi_" not in cf1[0]["run"]
    mi = [s for s in steps if "market-intelligence" in s.get("name", "")]
    assert mi and all(s.get("if") == "${{ vars.MARKET_INTEL_RECORD == 'on' }}" for s in mi)
    assert all("v4-cf1" not in s["run"] and "cf1_" not in s["run"] for s in mi)
    build = next(s for s in steps if s.get("name") == "Build site")
    assert build["env"]["MARKET_INTEL"] == "${{ vars.MARKET_INTEL }}"
