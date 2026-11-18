"""Circa 99% engine (owner's buddy's rules, rebuilt): reading checks, game matching, tiers, leans and grading."""
import datetime as dt
import json
import os
import time

import yaml

from market_v4 import normalize, store
from splits_v4 import circa_engine as E
from splits_v4 import circa_ocr as C

ROOT = os.path.join(os.path.dirname(__file__), "..")
CFB = "americanfootball_ncaaf"


def test_percentage_pairs_only_accept_one_reading_that_adds_to_100():
    assert C._pct_pair("1100%", "0%") == (100.0, 0.0)          # box border read as an extra 1
    assert C._pct_pair("83%", "171%") == (83.0, 17.0)
    assert C._pct_pair("191%", "9%") == (91.0, 9.0)
    assert C._pct_pair("95%", "4%") == (None, None)            # 99: does not add to exactly 100 -> rejected
    assert C._pct_pair("", "50%") == (None, None)
    assert C._pct_pair("50%.", "50%")[0] == 50.0


def test_sheet_date():
    assert E.sheet_date("CFB - TUESDAY, NOV 17", 2026) == dt.date(2026, 11, 17)
    assert E.sheet_date("ChE - TUESUAY, NOV", 2026) is None


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


KICK = 1794873600.0          # 2026-11-17 00:00 UTC is Mon 19:00 ET; use a kickoff on Tue 17 Nov ET below
KICK = 1794956400.0          # 2026-11-17 23:00 UTC = Tue 17 Nov 18:00 ET


def _store(home_lines, total=50.0):
    conn = store.connect()
    for i, hl in enumerate(home_lines):
        t = KICK - (len(home_lines) - i) * 3 * 3600
        bk = [{"key": b, "last_update": _iso(t), "markets": [
            {"key": "spreads", "last_update": _iso(t), "outcomes": [
                {"name": "Ohio Bobcats", "price": -110, "point": hl}, {"name": "Ball State Cardinals", "price": -110, "point": -hl}]},
            {"key": "totals", "last_update": _iso(t), "outcomes": [
                {"name": "Over", "price": -110, "point": total}, {"name": "Under", "price": -110, "point": total}]},
            {"key": "h2h", "last_update": _iso(t), "outcomes": [
                {"name": "Ohio Bobcats", "price": -400}, {"name": "Ball State Cardinals", "price": 325}]}]}
              for b in ("pinnacle", "betonlineag", "draftkings")]
        q, an = normalize.normalize([{"id": "g1", "sport_key": CFB, "commence_time": _iso(KICK), "home_team": "Ohio Bobcats",
                                      "away_team": "Ball State Cardinals", "bookmakers": bk}], _iso(t))
        store.ingest(conn, q, an, _iso(t), CFB, "odds", "fp")
    return conn


def _sheet(home_handle=100, home_bets=87, line=10.0, total=50.0, over_handle=50, over_bets=50):
    return [{"sport_label": "CFB - TUESDAY, NOV 17", "sport": "cfb", "away": "Ball ST Cardinals", "home": "Ohio Bobcats",
             "status": "OK", "problems": [],
             "spread": {"away": {"line": line, "handle": 100.0 - home_handle, "bets": 100.0 - home_bets},
                        "home": {"line": line, "handle": float(home_handle), "bets": float(home_bets)}},
             "total": {"over": {"line": total, "handle": float(over_handle), "bets": float(over_bets)},
                       "under": {"line": total, "handle": 100.0 - over_handle, "bets": 100.0 - over_bets}},
             "money": {"away": {"line": 325.0, "handle": 0.0, "bets": 0.0}, "home": {"line": 400.0, "handle": 100.0, "bets": 100.0}}}]


def _sig(conn, sheet, market="spread", at=KICK - 2 * 3600):
    return [s for s in E.signals(sheet, conn, at, 2026) if s["market"] == market]


def test_match_signs_and_tiers():
    conn = _store([-9.0, -10.0])                                        # Ohio favoured; line moved -9 -> -10 (WITH Ohio money)
    s = _sig(conn, _sheet(home_bets=87))[0]
    assert s["status"] == "SIGNAL" and s["game"] == "Ball State Cardinals @ Ohio Bobcats" and s["sport"] == "cfb"
    assert s["line_heavy"] == -10.0 and s["line_fade"] == 10.0          # sign from the market, not the image
    assert s["tier"] == "NORMAL" and s["movement"] == "WITH" and s["lean"] == "PASS"
    m = _sig(conn, _sheet(), "money")[0]
    assert m["tier"] == "THIN" and m["lean"] == "LOG" and m["line_heavy"] == -400.0 and m["line_fade"] == 325.0


def test_flip_like_and_live():
    conn = _store([-9.0, -10.0])
    assert _sig(conn, _sheet(home_bets=60))[0]["lean"] == "FLIP"       # moved with the money, one big ticket
    conn2 = _store([-11.0, -10.0])                                      # moved AGAINST the 100% Ohio money
    s = _sig(conn2, _sheet(home_bets=87))[0]
    assert s["movement"] == "AGAINST" and s["lean"] == "LIKE"
    assert _sig(conn2, _sheet(home_bets=87), at=KICK + 600)[0]["lean"] == "PASS"     # uploaded after kickoff


def test_unmatched_rows_are_never_guessed():
    conn = _store([-10.0])
    s = _sig(conn, _sheet(line=3.0, total=140.0))[0]                     # basketball-like numbers
    assert s["status"] == "NOT MATCHED"


def test_grading_fade_follow_lean():
    rec = {"status": "SIGNAL", "market": "spread", "heavy": "home", "fade": "away", "line_heavy": -10.0, "line_fade": 10.0,
           "price_heavy": -110, "price_fade": -110, "lean": "LIKE"}
    g = E.grade(rec, (24, 17))                                           # Ohio by 7: Ball State +10 covers
    assert g["fade"] == {"result": "W", "units": 0.909} and g["follow"]["result"] == "L" and g["lean"] == g["fade"]
    ml = dict(rec, market="money", line_heavy=-400.0, line_fade=325.0, price_heavy=-400.0, price_fade=325.0, lean="FLIP")
    assert E.grade(ml, (24, 17))["lean"] == {"result": "W", "units": 0.25}
    assert E.grade(dict(rec, status="NOT MATCHED"), (1, 0)) is None


def test_circa_workflow_has_no_odds_key_and_only_cloudflare_secrets():
    txt = open(os.path.join(ROOT, ".github", "workflows", "circa.yml")).read()
    wf = yaml.safe_load(txt)
    import re
    assert "ODDS_API_KEY" not in txt and not re.search(r"secrets:\s*inherit", txt)
    assert wf[True]["push"]["paths"] == ["circa_uploads/**"]
    assert "secrets." not in json.dumps(wf["jobs"]["read"])
    assert set(wf["jobs"]["site"]["secrets"]) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    ci = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "v4-ci.yml")))
    assert "circa_uploads/**" in ci[True]["push"]["paths-ignore"]     # an image upload never runs the test suite
