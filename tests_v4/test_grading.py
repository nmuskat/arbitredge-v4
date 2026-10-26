"""Milestone 2 grader: ATS, ROI at recorded odds, CLV, refusal to invent data."""

import json
import math
import sqlite3

import pytest

from sportsbook_v4.grading import (
    PickRecord, ats, binom_two_sided, closing_line, grade_pick, need_outcome, picks_from_csv,
    picks_from_experimental_mixed_signals, reconstruction_audit, report, select_final_result, summarize, wilson,
)
from sportsbook_v4.market import FixtureMarket, quote

KO = "2026-11-01T18:00:00Z"
FINAL = [{"result_id": 1, "is_final": 1, "final_score_home": 24, "final_score_away": 20,
          "retrieved_at": "2026-11-01T21:30:00Z"}]


def pick(**kw):
    base = dict(pick_id="p1", source="test", source_mutable=False, canonical_game_id="g", sport="nfl",
                side="home", line=-3.5, price=-110.0, price_provenance="RECORDED",
                decided_at="2026-11-01T17:00:00Z")
    base.update(kw)
    return PickRecord(**base)


def market(home_spread=-3.5, hp=-110, ap=-110, ts="2026-11-01T17:30:00Z"):
    return FixtureMarket({"g": [quote(b, ts, home_spread, hp, ap) for b in ("a", "b", "c")]})


def test_ats_math_including_pushes_and_away_sides():
    assert ats("home", -3.5, 24, 20) == "W"
    assert ats("home", -4.0, 24, 20) == "P"
    assert ats("home", -4.5, 24, 20) == "L"
    assert ats("away", 3.5, 24, 20) == "L"
    assert ats("away", 4.0, 24, 20) == "P"
    assert ats("away", 4.5, 24, 20) == "W"
    assert ats("away", -2.5, 17, 20) == "W"


def test_units_at_recorded_price():
    close = closing_line(market(), "g", KO)
    g = grade_pick(pick(price=+120.0), KO, select_final_result(FINAL, KO, "2026-11-02T00:00:00Z")[0], close)
    assert g["ats"] == "W" and g["units"] == pytest.approx(1.2)
    g = grade_pick(pick(line=-4.5, price=-125.0), KO, {"home_score": 24, "away_score": 20}, close)
    assert g["ats"] == "L" and g["units"] == -1.0
    g = grade_pick(pick(line=-4.0), KO, {"home_score": 24, "away_score": 20}, close)
    assert g["ats"] == "P" and g["units"] == 0.0


def test_missing_data_is_never_invented():
    res = {"home_score": 24, "away_score": 20}
    assert grade_pick(pick(line=None), KO, res, None)["status"] == "UNGRADABLE"
    assert grade_pick(pick(side=None), KO, res, None)["status"] == "UNGRADABLE"
    assert grade_pick(pick(decided_at=None), KO, res, None)["status"] == "UNGRADABLE"
    assert grade_pick(pick(), None, res, None)["status"] == "UNGRADABLE"
    g = grade_pick(pick(price=None, price_provenance="MISSING"), KO, res, None)
    assert g["status"] == "GRADED" and "units" not in g and "MISSING_PRICE" in g["issues"]
    s = summarize([g])
    assert s["roi_recorded_odds"] == {"n": 0} and s["excluded_from_roi_missing_price"] == 1


def test_not_prospective_excluded():
    g = grade_pick(pick(decided_at=KO), KO, {"home_score": 24, "away_score": 20}, None)
    assert g["status"] == "NOT_PROSPECTIVE" and "ats" not in g
    g = grade_pick(pick(decided_at="2026-11-01T19:00:00Z"), KO, {"home_score": 24, "away_score": 20}, None)
    assert g["status"] == "NOT_PROSPECTIVE"


def test_result_selection_guards_lookahead_and_revisions():
    obs = [
        {"result_id": 1, "is_final": 0, "final_score_home": 7, "final_score_away": 3, "retrieved_at": "2026-11-01T19:00:00Z"},
        {"result_id": 2, "is_final": 1, "final_score_home": 24, "final_score_away": 20, "retrieved_at": "2026-11-01T21:30:00Z"},
        {"result_id": 3, "is_final": 1, "final_score_home": 24, "final_score_away": 21, "retrieved_at": "2026-11-02T10:00:00Z"},
        {"result_id": 4, "is_final": 1, "final_score_home": 99, "final_score_away": 0, "retrieved_at": "2026-11-01T12:00:00Z"},
    ]
    r, flags = select_final_result(obs, KO, "2026-11-01T23:00:00Z")
    assert (r["home_score"], r["away_score"]) == (24, 20)
    assert "FINAL_AFTER_GRADING_CUTOFF_IGNORED" in flags and "FINAL_BEFORE_KICKOFF_IGNORED" in flags
    r, flags = select_final_result(obs, KO, "2026-11-03T00:00:00Z")
    assert (r["home_score"], r["away_score"]) == (24, 21) and "SCORE_REVISED" in flags
    assert select_final_result(obs[:1], KO, "2026-11-03T00:00:00Z")[0] is None


def test_clv_points_and_same_line_price():
    res = {"home_score": 24, "away_score": 20}
    # Took home -3 -110; market closed -4 -> +1 point of CLV, no price CLV (different line).
    g = grade_pick(pick(line=-3.0), KO, res, closing_line(market(-4.0), "g", KO))
    assert g["clv_points"] == pytest.approx(1.0) and "clv_price_prob" not in g
    # Took away +4 at -110; closed away +3 -> +1 point.
    g = grade_pick(pick(side="away", line=4.0), KO, res, closing_line(market(-3.0), "g", KO))
    assert g["clv_points"] == pytest.approx(1.0)
    # Same line, close -125/+105 vs our -110 -> positive price CLV.
    g = grade_pick(pick(line=-3.5), KO, res, closing_line(market(-3.5, -125, +105), "g", KO))
    assert g["clv_points"] == 0.0
    assert g["clv_price_prob"] == pytest.approx(125 / 225 - 110 / 210)
    fair = (125 / 225) / (125 / 225 + 100 / 205)
    assert g["clv_novig_prob"] == pytest.approx(fair - 110 / 210)


def test_closing_line_never_uses_post_kickoff_quotes():
    m = FixtureMarket({"g": [quote("a", "2026-11-01T17:00:00Z", -3.0), quote("a", "2026-11-01T18:00:00Z", -7.0)]})
    assert closing_line(m, "g", KO)["home_spread"] == -3.0


def test_summary_statistics_match_hand_calculation():
    res_w, res_l = {"home_score": 24, "away_score": 20}, {"home_score": 20, "away_score": 24}
    graded = ([grade_pick(pick(pick_id=f"w{i}"), KO, res_w, None) for i in range(37)]
              + [grade_pick(pick(pick_id=f"l{i}"), KO, res_l, None) for i in range(34)]
              + [grade_pick(pick(pick_id=f"p{i}", line=-4.0), KO, res_w, None) for i in range(5)])
    s = summarize(graded)
    assert s["record"] == "37-34-5"
    assert s["win_pct_ex_push"] == pytest.approx(37 / 71)
    assert s["p_value_vs_50pct"] == pytest.approx(0.8122, abs=1e-3)
    assert s["win_pct_95ci"] == pytest.approx(wilson(37, 71))
    roi = s["roi_recorded_odds"]
    assert roi["n"] == 76 and roi["units"] == pytest.approx(37 * 100 / 110 - 34)
    assert "INSUFFICIENT_SAMPLE" in s["sample_warning"]
    assert binom_two_sided(5, 10) == pytest.approx(1.0)


def test_report_never_mixes_recorded_and_derived_prices():
    res = {"home_score": 24, "away_score": 20}
    graded = [grade_pick(pick(pick_id="r"), KO, res, None),
              grade_pick(pick(pick_id="d", price=+150.0, price_provenance="DERIVED_FROM_RECORDED_QUOTES"), KO, res, None)]
    s = summarize(graded)
    assert s["roi_recorded_odds"]["n"] == 1 and s["roi_recorded_odds"]["units"] == pytest.approx(100 / 110)
    assert s["roi_derived_odds"]["n"] == 1 and s["roi_derived_odds"]["units"] == pytest.approx(1.5)
    r = report(graded)
    assert set(r["by"]["price_provenance"]) == {"RECORDED", "DERIVED_FROM_RECORDED_QUOTES"}


def test_experimental_mixed_signals_adapter_flags_mutability_and_derives_price_only_from_recorded_quotes():
    conn = sqlite3.connect(":memory:")
    # Column names as written by operations/run_collection.py (real .schema still to be confirmed).
    conn.execute("CREATE TABLE experimental_mixed_signals (canonical_game_id INTEGER, sport TEXT, signal_version TEXT, "
                 "as_of TEXT, written_at TEXT, public_side TEXT, arbitrage_side TEXT, side_preference_percentile REAL, "
                 "aggregate_regime TEXT, resistance_count INT, divergence_count INT, partial_divergence_count INT, "
                 "confirmation_count INT, no_movement_count INT, book_side_count INT, signal_line REAL, "
                 "signal_line_method TEXT, data_quality TEXT, full_record_json TEXT)")
    frj = {"signal_line": {"spread": 3.5, "lines": [
        {"book": "a", "spread": 3.5, "price": -105}, {"book": "b", "spread": 3.5, "price": -115},
        {"book": "c", "spread": 3.0, "price": -120}]}}
    conn.executemany("INSERT INTO experimental_mixed_signals (canonical_game_id, sport, signal_version, as_of, written_at, "
                     "arbitrage_side, signal_line, data_quality, aggregate_regime, full_record_json) VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (7, "nfl", "mixed_book_side_dominant_v1", "2026-11-01T17:50:00Z", "2026-11-01T17:50:01Z", "away", 3.5,
         "HIGH", "MIXED", json.dumps(frj)),
        (8, "nfl", "mixed_book_side_dominant_v1", "2026-11-01T17:50:00Z", "2026-11-01T17:50:01Z", "home", None,
         "LOW", "MIXED", json.dumps({"signal_line": None})),
    ])
    picks = picks_from_experimental_mixed_signals(conn)
    p7, p8 = picks
    assert p7.source_mutable and p7.price_provenance == "DERIVED_FROM_RECORDED_QUOTES"
    assert p7.price == -105 and p7.line == 3.5          # lower-median implied of the matching books only
    assert p8.line is None and p8.price is None and p8.price_provenance == "MISSING"
    audit = reconstruction_audit(picks, {"7": KO, "8": KO})
    assert audit["missing_line"] == 1 and audit["from_mutable_source"] == 2
    assert audit["price_provenance"]["RECORDED"] == 0
    assert audit["verdict"] == "PARTIALLY_RECONSTRUCTABLE"
    assert reconstruction_audit([], {})["verdict"] == "NO_PICKS_FOUND"


def test_csv_ledger_keeps_blanks_blank(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text("pick_id,canonical_game_id,sport,side,line,price,decided_at,tier\n"
                    "a,g,nfl,home,-3.5,-110,2026-11-01T17:00:00Z,A\n"
                    "b,g,nfl,away,,,,B\n")
    a, b = picks_from_csv(path)
    assert a.price == -110 and a.price_provenance == "RECORDED"
    assert b.line is None and b.price is None and b.decided_at is None and b.price_provenance == "MISSING"
    bad = tmp_path / "bad.csv"
    bad.write_text("pick_id,side\nx,home\n")
    with pytest.raises(ValueError, match="missing required columns"):
        picks_from_csv(bad)


def test_need_outcome_uses_each_wager_number():
    wagers = [
        {"side": "home", "accepted_line": -3.0, "accepted_home_spread": -3.0, "accepted_price": -110, "stake": 1100,
         "archetype": "X", "bettor_index": 1},
        {"side": "away", "accepted_line": 3.5, "accepted_home_spread": -3.5, "accepted_price": -110, "stake": 110,
         "archetype": "X", "bettor_index": 2},
    ]
    close = closing_line(market(-3.5), "g", KO)
    out = need_outcome("away", wagers, {"home_score": 24, "away_score": 21}, close)
    # margin 3: home -3 pushes (0), away +3.5 wins (-100)
    assert out["realized_book_pnl"] == pytest.approx(-100.0)
    assert out["need_side_vs_close"] == "W"
    assert need_outcome(None, wagers, None, close)["status"] == "NOT_EVALUABLE"
    assert math.isclose(out["pnl_if_need_side_covers_all"], 1100 - 100, abs_tol=0.01)
