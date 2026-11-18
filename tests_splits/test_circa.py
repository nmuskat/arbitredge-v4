"""Circa 99% engine (owner's buddy's rules, rebuilt for every sport): reading checks, ESPN parsing, game matching,
signs from DraftKings, tiers, leans, line movement and grading."""
import json
import os
import re

import yaml

from splits_v4 import circa_engine as E
from splits_v4 import circa_ocr as C
from splits_v4 import espn

ROOT = os.path.join(os.path.dirname(__file__), "..")
import calendar
UP = float(calendar.timegm((2026, 11, 23, 15, 0, 0)))      # Mon 23 Nov, 10:00 ET (upload time)
KO = UP + 9 * 3600                                        # 19:00 ET tip-off, same day


def test_percentage_pairs_only_accept_one_reading_that_adds_to_100():
    assert C._pct_pair("1100%", "0%") == (100.0, 0.0)
    assert C._pct_pair("83%", "171%") == (83.0, 17.0)
    assert C._pct_pair("191%", "9%") == (91.0, 9.0)
    assert C._pct_pair("95%", "4%") == (None, None)
    assert C._pct_pair("", "50%") == (None, None)


def _espn_doc(home="Cleveland Cavaliers", away="Memphis Grizzlies", sp_open=-5.5, sp_close=-4.5, t_open=236.5,
              t_close=238.5, ml=(-196, 164), final=None):
    comp = {"competitors": [
        {"homeAway": "home", "team": {"displayName": home, "abbreviation": "H"}, "score": str(final[0]) if final else "0"},
        {"homeAway": "away", "team": {"displayName": away, "abbreviation": "A"}, "score": str(final[1]) if final else "0"}],
        "odds": [] if final else [{"provider": {"name": "DraftKings"}, "homeTeamOdds": {"favorite": sp_close < 0},
                                   "awayTeamOdds": {"favorite": sp_close > 0},
                                   "pointSpread": {"home": {"open": {"line": f"{sp_open:+g}", "odds": "-110"},
                                                            "close": {"line": f"{sp_close:+g}", "odds": "-113"}},
                                                   "away": {"open": {"line": f"{-sp_open:+g}", "odds": "-110"},
                                                            "close": {"line": f"{-sp_close:+g}", "odds": "-107"}}},
                                   "total": {"over": {"open": {"line": f"o{t_open}", "odds": "-110"}, "close": {"line": f"o{t_close}", "odds": "-110"}},
                                             "under": {"open": {"line": f"u{t_open}", "odds": "-110"}, "close": {"line": f"u{t_close}", "odds": "-110"}}},
                                   "moneyline": {"home": {"open": {"odds": f"{ml[0]:+d}"}, "close": {"odds": f"{ml[0]:+d}"}},
                                                 "away": {"open": {"odds": f"{ml[1]:+d}"}, "close": {"odds": f"{ml[1]:+d}"}}}}]}
    import time as _t
    return {"events": [{"id": "401", "date": _t.strftime("%Y-%m-%dT%H:%MZ", _t.gmtime(KO)),
                        "status": {"type": {"state": "post" if final else "pre", "completed": bool(final)}},
                        "competitions": [comp]}]}


def test_espn_parse():
    g = espn.parse(_espn_doc(), "nba")[0]
    assert g["home"] == "Cleveland Cavaliers" and g["kickoff"] == KO and g["odds"]["spread_home_close"] == -4.5
    assert g["odds"]["spread_away_open"] == 5.5 and g["odds"]["total_close"] == 238.5 and g["odds"]["ml_away_close"] == 164
    f = espn.parse(_espn_doc(final=(110, 101)), "nba")[0]
    assert f["final"] and f["home_score"] == 110 and f["odds"] is None


def _sheet(sp=4.5, total=238.5, home_handle=100, home_bets=87, away="Memphis Grizzlies", home="Cleveland Cavaliers"):
    return [{"sport_label": "NBA - MONDAY, NOV 23", "away": away, "home": home, "status": "OK", "problems": [],
             "spread": {"away": {"line": sp, "handle": 100.0 - home_handle, "bets": 100.0 - home_bets},
                        "home": {"line": sp, "handle": float(home_handle), "bets": float(home_bets)}},
             "total": {"over": {"line": total, "handle": 50.0, "bets": 50.0}, "under": {"line": total, "handle": 50.0, "bets": 50.0}},
             "money": {"away": {"line": 164.0, "handle": 0.0, "bets": 0.0}, "home": {"line": 196.0, "handle": 100.0, "bets": 100.0}}}]


def _sig(sheet, doc=None, market="spread", at=UP):
    evs = espn.parse(doc or _espn_doc(), "nba")
    return [s for s in E.signals(sheet, evs, at) if s["market"] == market]


def test_match_signs_tiers_and_like():
    s = _sig(_sheet())[0]                        # DK opened CLE -5.5, now -4.5: moved AGAINST the 100% CLE money
    assert s["status"] == "SIGNAL" and s["sport"] == "NBA" and s["game"] == "Memphis Grizzlies @ Cleveland Cavaliers"
    assert s["line_heavy"] == -4.5 and s["line_fade"] == 4.5                     # sign from DraftKings, not the image
    assert s["movement"] == "AGAINST" and s["tier"] == "NORMAL" and s["lean"] == "LIKE"
    assert s["price_fade"] == -107 and s["price_heavy"] == -113                  # DK price at the same number
    m = _sig(_sheet(), market="money")[0]
    assert m["tier"] == "THIN" and m["lean"] == "LOG" and m["line_heavy"] == -196.0 and m["line_fade"] == 164.0


def test_flip_pass_and_live():
    with_money = _espn_doc(sp_open=-3.5, sp_close=-4.5)                            # moved WITH the CLE money
    assert _sig(_sheet(home_bets=60), with_money)[0]["lean"] == "FLIP"
    assert _sig(_sheet(home_bets=87), with_money)[0]["lean"] == "PASS"
    assert _sig(_sheet(), at=KO + 60)[0]["lean"] == "PASS"                         # uploaded after tip-off


def test_lost_decimal_and_unmatched_rows():
    assert _sig(_sheet(sp=45.0))[0]["line_fade"] == 4.5                            # "45" read for 4.5
    assert _sig(_sheet(sp=11.0, total=150.0))[0]["status"] == "NOT MATCHED"        # numbers fit no game: never guessed


def test_grading():
    rec = {"status": "SIGNAL", "market": "spread", "heavy": "home", "fade": "away", "line_heavy": -4.5, "line_fade": 4.5,
           "price_heavy": -113, "price_fade": -107, "lean": "LIKE"}
    g = E.grade(rec, (110, 107))                                                  # CLE by 3: MEM +4.5 covers
    assert g["fade"]["result"] == "W" and g["follow"]["result"] == "L" and g["lean"] == g["fade"]
    tot = dict(rec, market="total", heavy="over", fade="under", line_heavy=238.5, line_fade=238.5, lean="FLIP")
    assert E.grade(tot, (120, 120))["lean"]["result"] == "W"                       # 240 > 238.5: over (followed) wins


def test_circa_workflow_has_no_odds_key_and_only_cloudflare_secrets():
    txt = open(os.path.join(ROOT, ".github", "workflows", "circa.yml")).read()
    wf = yaml.safe_load(txt)
    assert "ODDS_API_KEY" not in txt and not re.search(r"secrets:\s*inherit", txt)
    assert wf[True]["push"]["paths"] == ["circa_uploads/**"]
    assert "secrets." not in json.dumps(wf["jobs"]["read"])
    assert set(wf["jobs"]["site"]["secrets"]) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    ci = yaml.safe_load(open(os.path.join(ROOT, ".github", "workflows", "v4-ci.yml")))
    assert "circa_uploads/**" in ci[True]["push"]["paths-ignore"]


def test_site_step_grades_with_espn_finals_and_saves_them_once(tmp_path):
    from web_v4 import live_site as LS
    rec_dir, out = tmp_path / "rec", tmp_path / "out"
    rec_dir.mkdir()
    sigs = E.signals(_sheet(), espn.parse(_espn_doc(), "nba"), UP)
    (rec_dir / "circa_a.png.json").write_text(json.dumps({"image": "a.png", "uploaded_at": UP, "signals": sigs, "read": []}))
    snap = {}
    LS.circa_step(snap, str(rec_dir), str(out), get=lambda url: _espn_doc(final=(110, 107)), now=KO + 6 * 3600)
    c = snap["circa"]
    assert c["status"] == "OK" and c["n_uploads"] == 1
    sp = next(s for s in c["signals"] if s["market"] == "spread")
    assert sp["final"] == [110, 107] and sp["graded"]["lean"]["result"] == "W"     # MEM +4.5 covered (lost by 3)
    saved = list(out.glob("circa_finals_nba_*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["finals"] == {"401": [110, 107]}
    snap2 = {}                                                   # next build reads the saved finals: no ESPN call
    (rec_dir / saved[0].name).write_text(saved[0].read_text())
    LS.circa_step(snap2, str(rec_dir), None, get=lambda url: (_ for _ in ()).throw(OSError("no network")), now=KO + 9 * 3600)
    assert next(s for s in snap2["circa"]["signals"] if s["market"] == "spread")["graded"]["lean"]["result"] == "W"
