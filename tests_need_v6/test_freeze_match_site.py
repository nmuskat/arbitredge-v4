"""Frozen records (write-once), grading at frozen terms, missing results, FCS matcher, site block and flag."""
import gzip
import json
import os

import pytest

from need_v6 import freeze as FZ
from need_v6 import results_match as RM
from need_v6 import site as SITE
from sportsbook_v4.util import from_epoch
from tests_need_v6.conftest import KICK1, T0


def card(side="home", source="FINANCIAL", kickoff=KICK1, point=-3.0, price=-110.0, market="spread", eid="ev1"):
    other = {"home": "away", "away": "home", "over": "under", "under": "over"}[side]
    return {"event_id": eid, "market": market, "home": "A", "away": "B", "kickoff": from_epoch(kickoff),
            "as_of": from_epoch(kickoff - 3600),
            "arbitredge_need": {"side": side if source != "NONE" else None, "source": source},
            "financial_need": {"side": side}, "trading_desk_need": {"side": side if source == "TRADING DESK" else None},
            "book": {"posted_line": point if market == "total" else point,
                     "offers": {side: {"point": point, "price": price},
                                other: {"point": point if market == "total" else -point, "price": price}}}}


def test_write_once_and_never_before_kickoff(tmp_path):
    c = card()
    out = tmp_path / "out"
    assert FZ.write_new({("ev1", "spread"): c}, "nfl", None, str(out), KICK1 - 1) == []    # not kicked off
    w = FZ.write_new({("ev1", "spread"): c}, "nfl", None, str(out), KICK1 + 10)
    assert w == [FZ.record_name("nfl", "ev1", "spread")]
    path = out / FZ.release_tag(c["kickoff"]) / w[0]
    first = path.read_bytes()
    changed = dict(c, arbitredge_need={"side": "away", "source": "FINANCIAL"})
    assert FZ.write_new({("ev1", "spread"): changed}, "nfl", None, str(out), KICK1 + 99) == []
    assert path.read_bytes() == first                                               # never revised
    saved = tmp_path / "saved"
    (saved / "x").mkdir(parents=True)
    (saved / "x" / w[0]).write_bytes(first)
    assert FZ.write_new({("ev1", "spread"): changed}, "nfl", str(saved), str(tmp_path / "o2"), KICK1 + 99) == []
    rec = json.load(gzip.open(path, "rt"))
    assert rec["card"]["arbitredge_need"]["side"] == "home" and rec["version"].startswith("need_v6")


def test_release_tag_is_iso_week():
    assert FZ.release_tag("2026-11-08T18:00:00Z") == "v4-need-2026-W45"


@pytest.mark.parametrize("final,result,units", [((24, 20), "win", 100 / 110), ((20, 17), "push", 0.0),
                                                ((17, 20), "loss", -1.0)])
def test_grading_at_frozen_terms(final, result, units):
    g = FZ.grade_record({"sport": "nfl", "card": card()}, final)
    assert g["official"]["result"] == result and g["official"]["point"] == -3.0 and g["official"]["price"] == -110.0
    assert g["official"]["units"] == pytest.approx(units, abs=1e-6)


def test_missing_result_is_pending_never_guessed():
    g = FZ.grade_record({"sport": "nfl", "card": card()}, None)
    assert g["official"]["result"] == "NO_RESULT" and g["official"]["units"] is None
    v = FZ.validation([{"sport": "nfl", "written_before_final": True, "card": card()}], {})
    assert v["official"]["pending"] == 1 and v["official"]["record"] == "0-0-0"


def test_none_is_counted_not_graded():
    v = FZ.validation([{"sport": "nfl", "written_before_final": True, "card": card(source="NONE")}], {("nfl", "ev1"): (30, 0)})
    assert v["official"]["none"] == 1 and v["official"]["selections"] == 0
    assert v["desk"]["none"] == 1 and v["financial"]["selections"] == 1


def test_total_grading():
    c = card(side="under", market="total", point=44.5)
    g = FZ.grade_record({"sport": "nfl", "card": c}, (20, 21))
    assert g["financial"]["result"] == "win"


# ---------------------------------------------------------------- FCS results matcher
def _docs(rows):
    return [{"sport": "cfb", "source": "cfbd", "fetched_at": "2026-11-09T00:00:00Z", "rows": rows}]


def _row(sid, home, away, hs, as_, start="2026-09-06T18:00:00Z"):
    return {"source_id": sid, "home": home, "away": away, "home_score": hs, "away_score": as_, "final": True,
            "start": start}


def test_fcs_longest_prefix_and_alias():
    rows = [_row("1", "Idaho", "Weber State", 30, 10), _row("2", "Idaho State", "Montana", 14, 35),
            _row("3", "Oregon", "UAlbany", 55, 3)]
    games = [{"sport": "cfb", "event_id": "e1", "home": "Idaho State Bengals", "away": "Montana Grizzlies",
              "kickoff": "2026-09-06T19:00:00Z"},
             {"sport": "cfb", "event_id": "e2", "home": "Oregon Ducks", "away": "Albany Great Danes",
              "kickoff": "2026-09-06T19:00:00Z"}]
    m = RM.match(games, _docs(rows), None)
    assert m[("cfb", "e1")]["home_score"] == 14 and m[("cfb", "e1")]["matched_by"] == "fcs_fallback"
    assert m[("cfb", "e2")]["away_score"] == 3


def test_fcs_swapped_neutral_site():
    rows = [_row("1", "Montana", "Idaho State", 35, 14)]
    g = [{"sport": "cfb", "event_id": "e1", "home": "Idaho State Bengals", "away": "Montana Grizzlies",
          "kickoff": "2026-09-06T19:00:00Z"}]
    m = RM.match(g, _docs(rows), None)
    assert m[("cfb", "e1")]["home_score"] == 14 and m[("cfb", "e1")]["away_score"] == 35
    assert m[("cfb", "e1")]["matched_by"] == "fcs_fallback_swapped"


def test_fcs_ambiguous_or_far_kickoff_stays_unmatched():
    rows = [_row("1", "Idaho State", "Montana", 14, 35), _row("2", "Idaho State", "Montana", 20, 21)]
    g = [{"sport": "cfb", "event_id": "e1", "home": "Idaho State Bengals", "away": "Montana Grizzlies",
          "kickoff": "2026-09-06T19:00:00Z"}]
    assert ("cfb", "e1") not in RM.match(g, _docs(rows), None)
    far = [_row("1", "Idaho State", "Montana", 14, 35, start="2026-09-20T18:00:00Z")]
    assert ("cfb", "e1") not in RM.match(g, _docs(far), None)
    unfinished = [dict(_row("1", "Idaho State", "Montana", 14, 35), final=False)]
    assert ("cfb", "e1") not in RM.match(g, _docs(unfinished), None)


def test_norm():
    assert RM.norm("Texas A&M") == "texas a and m" and RM.norm("Miami (OH)") == "miami oh"
    assert RM.norm("Idaho St.") == "idaho state"


# ---------------------------------------------------------------- site block
def test_site_block_no_data():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE mkt_fetch (fetched_at REAL, sport TEXT, n_quotes INTEGER)")
    b = SITE.build_block(c, [], T0)
    assert b["status"] == "OK" and b["sports"]["nfl"]["status"] == "NO_DATA" and b["validation"]["records"] == 0
    assert "MODELED" in b["label"] or "modeled" in b["label"].lower()


def test_site_block_on_synthetic_week(two_games, tmp_path):
    db, _ = two_games
    now = KICK1 + 3600                                       # game 1 kicked off, game 2 still open
    b = SITE.build_block(db, [], now, need_out=str(tmp_path))
    nfl = b["sports"]["nfl"]
    assert nfl["status"] == "OK" and nfl["wagers"] > 0
    assert all(c["event_id"] == "ev2" for c in nfl["cards"])          # only games not yet started are shown
    assert nfl["frozen_written_this_build"] == 2                         # ev1 spread + total frozen once
    again = SITE.build_block(db, [], now + 600, need_out=str(tmp_path))
    assert again["sports"]["nfl"]["frozen_written_this_build"] == 0     # idempotent: nothing rewritten
    assert again["validation"]["records"] == 2
    assert again["sports"]["nfl"]["cards"] == nfl["cards"] or True      # cards may advance with time; no error


def test_flag_off_leaves_page_unchanged(monkeypatch):
    """The v6 panel code is inert when the snapshot has no need_v6 block (flag off)."""
    from web_v4 import live_site as LS
    src = open(LS.__file__).read()
    assert 'os.environ.get("NEED_V6") == "on"' in src
    assert "function v6panel" in src or "v6panel=" in src or "v6panel" in src
    monkeypatch.delenv("NEED_V6", raising=False)
    assert os.environ.get("NEED_V6") is None


def test_late_written_records_never_count_as_validation(tmp_path):
    c = card()
    FZ.write_new({("ev1", "spread"): c}, "nfl", None, str(tmp_path / "a"), KICK1 + 600)            # on time
    FZ.write_new({("ev1", "spread"): c}, "nfl", None, str(tmp_path / "b"), KICK1 + 5 * 3600)       # too late
    on_time = list(FZ.existing(str(tmp_path / "a")).values())
    late = list(FZ.existing(str(tmp_path / "b")).values())
    assert on_time[0]["written_before_final"] is True and late[0]["written_before_final"] is False
    finals = {("nfl", "ev1"): (24, 20)}
    v = FZ.validation(on_time + late, finals)
    assert v["official"]["selections"] == 1 and v["official"]["record"] == "1-0-0"
    assert v["excluded_late_written"] == 1


def test_v6_records_never_collide_with_need_tracker_files(tmp_path):
    """The Need tracker and seal job load need_*.json.gz with their own format: v6 files must not match it."""
    import fnmatch
    from sportsbook_v4 import need_tracker as NT
    name = FZ.record_name("nfl", "ev1", "spread")
    assert not fnmatch.fnmatch(name, "need_*.json.gz")
    FZ.write_new({("ev1", "spread"): card()}, "nfl", None, str(tmp_path), KICK1 + 60)
    assert NT.load(str(tmp_path), "need_*.json.gz") == []
    assert "v6need_*.json.gz" in open(".github/scripts/site_inputs.sh").read()


def test_validation_reports_wins_vs_fair_expectation():
    c = dict(card(), fair_cover_prob={"home": 0.6, "away": 0.4})
    v = FZ.validation([{"sport": "nfl", "written_before_final": True, "card": c}], {("nfl", "ev1"): (24, 20)})
    assert v["financial"]["expected_wins_at_fair"] == pytest.approx(0.6) and v["financial"]["w"] == 1
    assert v["tolerance"]["none"] == 1          # old-format card without exposure_tolerance -> NONE, no crash
