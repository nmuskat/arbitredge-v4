"""Backfill study: grading at the pick-time line, discovery/test split, fixed situation list."""
from tools_v4 import backfill_study as BS


def test_grade_spreads_and_totals():
    f = {"home": 24, "away": 20}
    assert BS.grade("spread", "home", -3.5, None, f) == "W" and BS.grade("spread", "away", -3.5, None, f) == "L"
    assert BS.grade("spread", "home", -4.0, None, f) == "P"
    assert BS.grade("total", "over", None, 43.5, f) == "W" and BS.grade("total", "under", None, 44.0, f) == "P"


def test_split_is_fixed_by_date():
    assert BS.period("nfl", "2026-09-29T12:00:00Z") == "discovery" and BS.period("nfl", "2026-10-06T12:00:00Z") == "test"
    assert BS.period("cfb", "2026-09-22T12:00:00Z") == "discovery" and BS.period("cfb", "2026-09-29T12:00:00Z") == "test"


def test_record_units_and_holds_rule():
    rows = [{"result": {"home": r, "away": {"W": "L", "L": "W", "P": "P"}[r]}} for r in "WWWLP"]
    rec = BS.record(rows, lambda r: "home")
    assert (rec["w"], rec["l"], rec["p"]) == (3, 1, 1) and rec["units"] == 1.9
    replay = {"label": "x", "weeks": [], "settlements": []}
    out, rs = BS.study(replay)
    assert out["rows"] == 0 and rs == []
