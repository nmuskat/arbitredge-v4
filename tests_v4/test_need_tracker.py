"""Book Need Tracker: metrics, pre-registered filter, Need/fade alignment, immutable records, sealing,
grading, and final-score ingestion."""

import gzip
import json
import os

import pytest

from data_v4 import results as R
from sportsbook_v4 import need_tracker as NT
from sportsbook_v4.settlement import exposure
from sportsbook_v4.util import to_epoch

KICK = "2026-11-01T18:00:00Z"
K = to_epoch(KICK)


def w(i, side, stake, arch="REC_CASUAL", bettor=None, line=None, price=-110, home_spread=-3.0):
    hs = home_spread
    return {"wager_id": f"w{i}", "bettor_index": i if bettor is None else bettor, "archetype": arch,
            "canonical_game_id": "g1", "market": "spread", "side": side,
            "accepted_line": (hs if side == "home" else -hs) if line is None else line,
            "accepted_home_spread": hs, "accepted_price": price, "stake": float(stake)}


def public_home(n=120, home_frac=0.8, stake=50.0):
    """n recreational bettors, home_frac of them (and their money) on home."""
    k = int(n * home_frac)
    return [w(i, "home" if i < k else "away", stake) for i in range(n)]


GAME = {"event_id": "g1", "home": "Home Team", "away": "Away Team", "kickoff": KICK}
OFFER = {"home_spread": -3.0, "home_price": -112, "away_price": -108, "price_book": "fanduel"}
RUN = {"run_id": "r" * 64, "engine_version": "sportsbook_v4.m4", "params_hash": "p" * 64}


def snap(wagers, handles=None, taken=K - 1800, built=K - 1700, offer=OFFER):
    return NT.snapshot(sport="nfl", run=RUN, game=GAME, spread_wagers=wagers,
                       week_handles=handles or {"g1": sum(x["stake"] for x in wagers), "g2": 1.0, "g3": 1.0, "g4": 1.0},
                       offer=offer, reference={"home_spread": -3.0}, taken_at=taken, built_at=built)


# ------------------------------------------------------------------ metrics and filter

def test_pressure_metrics_separate_recreational_and_sharp_and_aggregate_by_bettor():
    ws = public_home(100) + [w(500, "away", 1000, "SHARP"), w(501, "away", 300, bettor=7)]
    p = NT.pressure(ws)
    assert p["wagers"] == 102 and p["bettors"] == 101            # bettor 7 bet twice
    assert p["sharp_handle"] == 1000 and p["sharp_wagers"] == 1
    assert p["sharp_handle_share"] == round(1000 / p["handle"], 4)
    assert p["largest_bettor_handle"] == 1000                     # the sharp
    assert p["sides"]["home"]["rec_handle"] == 80 * 50
    assert p["sides"]["away"]["rec_handle"] == 20 * 50 + 300      # the sharp is not recreational
    assert p["sides"]["home"]["rec_tickets"] == 80 and p["sides"]["away"]["rec_tickets"] == 21


@pytest.mark.parametrize("n,frac,top,rank,ok,failing", [
    (120, 0.80, None, 1, True, []),
    (99, 0.80, None, 1, False, ["min_100_spread_wagers"]),
    (100, 0.80, None, 1, True, []),                               # exactly 100 passes
    (120, 0.70, None, 1, True, []),                               # exactly 70% passes
    (120, 0.65, None, 1, False, ["rec_handle_70pct_one_side"]),
    (120, 0.80, None, 2, False, ["weekly_handle_top_25pct"]),     # 4 games: top 25% = rank 1 only
    (120, 0.80, 0.30, 1, False, ["no_bettor_over_25pct"]),
])
def test_preregistered_filter_boundaries(n, frac, top, rank, ok, failing):
    ws = public_home(n, frac)
    if top:
        h = sum(x["stake"] for x in ws)
        ws.append(w(999, "home", top * h / (1 - top)))            # one bettor holds `top` of the handle
    q = NT.qualify(NT.pressure(ws), rank, 4)
    assert q["qualified"] == ok
    assert sorted(k for k, c in q["checks"].items() if not c["pass"]) == failing


def test_recreational_ticket_majority_must_be_on_the_money_side():
    # 70%+ of rec money on home from a few big tickets, but most rec TICKETS on away
    ws = [w(i, "home", 400) for i in range(30)] + [w(100 + i, "away", 20) for i in range(90)]
    q = NT.qualify(NT.pressure(ws), 1, 4)
    assert q["public_side"] == "home"
    assert not q["checks"]["rec_ticket_majority_same_side"]["pass"] and not q["qualified"]


def test_week_rank_and_top_share_ceiling():
    h = {f"g{i}": float(i) for i in range(1, 15)}                 # 14 games: top 25% = ceil(3.5) = 4
    assert NT.week_rank(h, "g14") == (1, 14) and NT.week_rank(h, "g11") == (4, 14)
    p = NT.pressure(public_home())
    assert NT.qualify(p, 4, 14)["checks"]["weekly_handle_top_25pct"]["pass"]
    assert not NT.qualify(p, 5, 14)["checks"]["weekly_handle_top_25pct"]["pass"]


# ------------------------------------------------------------------ Need from the book's actual P&L

def test_need_is_the_book_pnl_and_aligns_with_fading_the_public():
    ws = public_home()
    s = snap(ws)
    e = exposure(ws)
    assert s["need"]["side"] == e["financial_need_side"] == "away"
    assert s["need"]["liability"] == min(e["pnl_if_home_covers_all"], e["pnl_if_away_covers_all"]) < 0
    assert s["qualification"]["fade_side"] == "away" and s["alignment"] == "ALIGNED"
    assert s["research_pick"] == "away"
    assert s["need_pick"] == {"side": "away", "line": 3.0, "price": -108, "price_book": "fanduel", "offer": OFFER}


def test_disagreement_is_flagged_never_forced():
    # recreational money is on home, but sharp positions on away (each under 25% of the handle) leave the
    # book needing HOME: the game QUALIFIES on public pressure, yet is flagged and not picked
    ws = public_home(120, 0.8, stake=10.0) + [w(900 + i, "away", 225.0, "SHARP") for i in range(4)]
    s = snap(ws, handles={"g1": 99999.0, "g2": 1.0, "g3": 1.0, "g4": 1.0})
    assert s["qualification"]["qualified"] and s["qualification"]["public_side"] == "home"
    assert s["need"]["side"] == "home" and s["alignment"] == "DISAGREE"
    assert s["research_pick"] is None
    rep = NT.report(NT.seal([g()], [s], [], K + 60), {("nfl", "g1"): {"home_score": 30, "away_score": 0}})
    assert rep["research_subset"]["games"] == 0 and rep["qualified_disagreements"]["games"] == 1


def test_snapshot_must_be_before_kickoff_by_data_and_wall_clock():
    for taken, built in ((K, K - 10), (K - 10, K)):
        with pytest.raises(ValueError):
            snap(public_home(), taken=taken, built=built)


# ------------------------------------------------------------------ immutable files

def test_files_are_never_overwritten_and_tampering_is_detected(tmp_path):
    s = snap(public_home())
    NT.write(str(tmp_path), "need_nfl_x.json.gz", [s])
    with pytest.raises(NT.TrackerIntegrityError):
        NT.write(str(tmp_path), "need_nfl_x.json.gz", [s])
    assert NT.load(str(tmp_path), "need_*.json.gz") == [s]
    doc = json.load(gzip.open(tmp_path / "need_nfl_x.json.gz", "rt"))
    doc["records"][0]["need"]["side"] = "home"                    # edit a stored record
    doc["records_hash"] = __import__("sportsbook_v4.util", fromlist=["json_hash"]).json_hash(doc["records"])
    with gzip.open(tmp_path / "need_nfl_x.json.gz", "wt") as f:
        json.dump(doc, f)
    with pytest.raises(NT.TrackerIntegrityError, match="record hash"):
        NT.load(str(tmp_path), "need_*.json.gz")


def test_snapshot_hash_survives_the_pages_rounding_pass():
    from web_v4.live_site import _clean
    s = snap(public_home(121, 0.77))
    assert _clean(s) == s


# ------------------------------------------------------------------ sealing

def g(**k):
    return dict({"sport": "nfl", "event_id": "g1", "home": "Home Team", "away": "Away Team", "kickoff": KICK}, **k)


def test_seal_uses_the_last_snapshot_before_kickoff():
    a, b = snap(public_home(), taken=K - 7200, built=K - 7100), snap(public_home(130), taken=K - 1800, built=K - 1700)
    late = dict(b, built_at="2026-11-01T18:00:05Z")               # built after kickoff: never used
    assert NT.seal([g()], [a, b, late], [], K - 1) == []           # not kicked off yet
    [d] = NT.seal([g()], [a, b, late], [], K + 60)
    assert d["status"] == "OK" and d["snapshot"] == b
    assert NT.seal([g()], [a, b], [d], K + 3600) == []             # sealed once


def test_seal_records_missing_and_early_decisions():
    [d] = NT.seal([g()], [], [], K + 60)
    assert d["status"] == "NO_PRE_KICKOFF_SNAPSHOT" and d["snapshot"] is None
    [e] = NT.seal([g()], [snap(public_home(), taken=K - 5 * 3600, built=K - 5 * 3600)], [], K + 60)
    assert e["status"] == "EARLY"
    assert NT.seal([g(kickoff="2026-10-20T17:00:00Z")], [], [], K) == []     # before the tracker existed


def test_two_different_decisions_for_one_game_is_an_integrity_error():
    [d] = NT.seal([g()], [snap(public_home())], [], K + 60)
    other = dict(d, status="EARLY")
    other["record_hash"] = "x"
    with pytest.raises(NT.TrackerIntegrityError):
        NT.seal([g()], [], [d, other], K + 60)


# ------------------------------------------------------------------ grading

def sealed(ws, **kw):
    return NT.seal([g()], [snap(ws, **kw)], [], K + 60)[0]


@pytest.mark.parametrize("home,away,ats,units", [(20, 21, "W", 100 / 108), (24, 20, "L", -1.0), (23, 20, "P", 0.0)])
def test_grade_need_side_at_its_recorded_line_and_price(home, away, ats, units):
    r = NT.grade(sealed(public_home()), {"home_score": home, "away_score": away})   # Need: away +3 at -108
    assert r["status"] == "GRADED" and r["ats"] == ats and r["units"] == pytest.approx(units)


def test_grade_without_price_or_result_is_never_filled_in():
    d = sealed(public_home(), offer=None)
    assert NT.grade(d, {"home_score": 1, "away_score": 0})["status"] == "NO_PRICE"
    assert NT.grade(sealed(public_home()), None)["status"] == "AWAITING_RESULT"
    assert NT.grade(NT.seal([g()], [], [], K + 60)[0], {"home_score": 1, "away_score": 0})["status"] == "NO_DECISION"


def test_report_separates_all_need_from_the_research_subset():
    q = sealed(public_home())                                      # qualified + aligned
    nq = dict(sealed(public_home(80)), event_id="g2")              # fails min wagers
    nq["snapshot"] = dict(nq["snapshot"], event_id="g2")
    res = {("nfl", "g1"): {"home_score": 20, "away_score": 21}, ("nfl", "g2"): {"home_score": 30, "away_score": 0}}
    rep = NT.report([q, nq], res)
    assert rep["all_need"]["record"] == "1-1-0" and rep["research_subset"]["record"] == "1-0-0"
    assert rep["research_subset"]["sample_warning"].startswith("INSUFFICIENT SAMPLE")
    assert set(rep["by_largest_bettor_share"]) == {"<10%"}
    assert rep["all_need"]["roi"]["roi_95ci"] is not None


def test_summary_statistics():
    rows = [{"status": "GRADED", "ats": a, "units": u, "price": -110} for a, u in
            [("W", 100 / 110)] * 6 + [("L", -1.0)] * 4 + [("P", 0.0)]]
    s = NT.summarize(rows)
    assert s["record"] == "6-4-1" and s["win_pct_ex_push"] == 0.6
    lo, hi = s["win_pct_95ci"]
    assert lo < 0.6 < hi
    assert s["roi"]["roi"] == pytest.approx((6 * 100 / 110 - 4) / 11, abs=1e-4)
    assert s["roi"]["breakeven_win_pct"] == pytest.approx(110 / 210, abs=1e-4)


# ------------------------------------------------------------------ final scores

NFLVERSE = """game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,home_score,location
2026_09_CAR_GB,2026,REG,9,2026-10-29,Thursday,20:15,CAR,20,GB,23,Home
2026_09_NE_MIA,2026,REG,9,2026-11-01,Sunday,16:25,NE,NA,MIA,NA,Home
2025_09_CAR_GB,2025,REG,9,2025-10-30,Thursday,20:15,CAR,10,GB,13,Home
"""


def test_nflverse_parse_keeps_only_final_games_of_the_season():
    rows = R.parse_nflverse(NFLVERSE, 2026)
    assert rows == [{"source_id": "2026_09_CAR_GB", "home": "Green Bay Packers", "away": "Carolina Panthers",
                     "date_et": "2026-10-29", "home_score": 23, "away_score": 20, "final": True}]


def test_cfbd_parse_requires_completed_and_points():
    rows = R.parse_cfbd([{"id": 1, "completed": True, "homeTeam": "Georgia", "awayTeam": "Florida", "homePoints": 31,
                          "awayPoints": 17, "startDate": "2026-10-31T19:30:00.000Z"},
                         {"id": 2, "completed": False, "homeTeam": "Texas", "awayTeam": "Ole Miss", "homePoints": None,
                          "awayPoints": None, "startDate": "2026-10-31T23:30:00.000Z"}])
    assert [r["source_id"] for r in rows] == ["1"] and rows[0]["home_score"] == 31


def _doc(sport, rows, at):
    return {"format": R.FORMAT, "sport": sport, "season": 2026, "fetched_at": at, "source": "test", "rows": rows,
            "rows_hash": R.json_hash(rows)}


def test_match_nfl_by_teams_and_eastern_date_and_flag_corrections():
    tnf = {"sport": "nfl", "event_id": "e1", "home": "Green Bay Packers", "away": "Carolina Panthers",
           "kickoff": "2026-10-30T00:15:00Z"}                     # 8:15 pm ET on Oct 29
    rows = R.parse_nflverse(NFLVERSE, 2026)
    m = R.match([tnf], [_doc("nfl", rows, "2026-10-30T13:00:00Z")])
    assert m[("nfl", "e1")]["home_score"] == 23 and not m[("nfl", "e1")]["corrected"]
    fixed = [dict(rows[0], home_score=24)]
    m2 = R.match([tnf], [_doc("nfl", rows, "2026-10-30T13:00:00Z"), _doc("nfl", fixed, "2026-10-31T13:00:00Z")])
    assert m2[("nfl", "e1")]["home_score"] == 24 and m2[("nfl", "e1")]["corrected"]
    assert R.match([dict(tnf, kickoff="2026-11-06T00:15:00Z")], [_doc("nfl", rows, "x")]) == {}


def test_match_cfb_through_the_team_matcher_and_kickoff_window():
    from data_v4.rankings import TeamMatcher
    m = TeamMatcher([{"school": "Georgia", "mascot": "Bulldogs"}, {"school": "Florida", "mascot": "Gators"}])
    rows = R.parse_cfbd([{"id": 9, "completed": True, "homeTeam": "Georgia", "awayTeam": "Florida", "homePoints": 31,
                          "awayPoints": 17, "startDate": "2026-10-31T19:30:00.000Z"}])
    game = {"sport": "cfb", "event_id": "c1", "home": "Georgia Bulldogs", "away": "Florida Gators",
            "kickoff": "2026-10-31T19:30:00Z"}
    assert R.match([game], [_doc("cfb", rows, "x")], m)[("cfb", "c1")]["away_score"] == 17
    assert R.match([game], [_doc("cfb", rows, "x")], None) == {}                 # no matcher: never guessed


def test_results_saved_only_when_changed(tmp_path):
    rows = R.parse_nflverse(NFLVERSE, 2026)
    d1 = _doc("nfl", rows, "2026-10-30T13:00:00Z")
    p = R.save_if_changed(d1, [], str(tmp_path))
    assert p and R.load(str(tmp_path))[0]["rows"] == rows
    assert R.save_if_changed(dict(d1, fetched_at="2026-10-31T13:00:00Z"), R.load(str(tmp_path)), str(tmp_path)) is None


def test_fetch_uses_one_cfbd_call_in_season_and_never_leaks_the_key():
    calls = []

    def opener(url):
        calls.append(url)
        return b"[]"
    R.fetch("cfb", to_epoch("2026-10-31T12:00:00Z"), key="SECRET", opener=opener)
    assert len(calls) == 1 and "SECRET" not in calls[0]
    calls.clear()
    R.fetch("cfb", to_epoch("2026-12-20T12:00:00Z"), key="SECRET", opener=opener)
    assert len(calls) == 2                                         # bowl season adds postseason


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "data", "raw", "games.csv")),
                    reason="nflverse games.csv not downloaded")
def test_real_nflverse_abbreviations_all_map():
    text = open(os.path.join(os.path.dirname(__file__), "..", "data", "raw", "games.csv")).read()
    import csv
    import io
    teams = {r["home_team"] for r in csv.DictReader(io.StringIO(text)) if r["season"] == "2025"}
    assert teams <= set(R.NFL_ABBR)


# ------------------------------------------------------------------ game-window classification v2 (new weeks only)

def test_classification_v2_applies_only_to_weeks_opening_after_the_cutoff():
    from sportsbook_v4.live_book import CLASSIFICATION_V2_FROM, classify
    old_week, new_week = to_epoch("2026-10-27T12:00:00Z"), to_epoch(CLASSIFICATION_V2_FROM)
    snf, sat_night, sat_noon = (to_epoch(x) for x in ("2026-11-09T01:20:00Z", "2026-11-08T00:30:00Z",
                                                       "2026-11-07T17:00:00Z"))
    assert classify("nfl", snf, True, old_week) == (True, False)          # the open week is untouched
    assert classify("cfb", sat_night, False, old_week) == (False, False)
    assert classify("nfl", snf, True, new_week) == (True, True)           # exclusive national window
    assert classify("nfl", snf, False, new_week) == (False, False)        # regional Sunday game
    assert classify("cfb", sat_night, False, new_week) == (True, False)   # 7:30 pm ET: primetime
    assert classify("cfb", sat_noon, False, new_week) == (False, False)


def test_historical_sensitivity_tool_runs_and_labels_itself(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("need_history", os.path.join(os.path.dirname(__file__), "..",
                                                                               "tools_v4", "need_history.py"))
    nh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nh)
    csv_text = ("game_id,season,game_type,week,gameday,gametime,away_team,away_score,home_team,home_score,spread_line,"
                "away_spread_odds,home_spread_odds\n"
                "2024_01_A,2024,REG,1,2024-09-08,13:00,DAL,10,PHI,24,6.5,-110,-110\n"
                "2024_01_B,2024,REG,1,2024-09-08,16:25,KC,27,DEN,20,-3,-105,-115\n"
                "2024_01_C,2024,POST,1,2024-09-09,20:15,NYJ,9,SF,32,4,NA,NA\n")
    (tmp_path / "games.csv").write_text(csv_text)
    games = nh.load_games(str(tmp_path / "games.csv"), range(2024, 2025))
    assert [g["event_id"] for g in games] == ["2024_01_A", "2024_01_B"]               # regular season only
    assert games[1]["home_spread"] == 3.0 and games[1]["home_price"] == -115          # KC favoured at Denver
    rows = nh.run_week(games, bettors=400)
    assert rows and all(r["ats"] in "WLP" for r in rows)
    out = tmp_path / "h.json"
    nh.main(["--games", str(tmp_path / "games.csv"), "--seasons", "2024-2024", "--bettors", "400", "--out", str(out)])
    doc = json.load(open(out))
    assert doc["label"].startswith("EXPLORATORY SYNTHETIC BACKTEST")
