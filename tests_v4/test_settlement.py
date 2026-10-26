"""Settlement at individual accepted numbers: multiple lines, pushes, middles,
opposing liabilities, and independent P&L reconciliation (tests 7, 8, 13)."""

from fractions import Fraction

import pytest

from sportsbook_v4.settlement import (
    book_pnl_total, exposure, margin_grid, reference_scenarios, wager_result,
)


def w(side, home_spread, price, stake, archetype="REC_CASUAL", bettor=0):
    line = home_spread if side == "home" else -home_spread
    return {"side": side, "accepted_home_spread": home_spread, "accepted_line": line,
            "accepted_price": price, "stake": stake, "archetype": archetype, "bettor_index": bettor}


def exact_pnl(wagers, margin):
    total = Fraction(0)
    for x in wagers:
        side_margin = margin if x["side"] == "home" else -margin
        res = side_margin + Fraction(str(x["accepted_line"]))
        stake, price = Fraction(str(x["stake"])), Fraction(str(x["accepted_price"]))
        if res > 0:
            total -= stake * price / 100 if price > 0 else stake * 100 / -price
        elif res < 0:
            total += stake
    return total


def test_wager_result_signs_and_pushes():
    assert wager_result("home", -3.0, 4) == "win"
    assert wager_result("home", -3.0, 3) == "push"
    assert wager_result("home", -3.0, 2) == "loss"
    assert wager_result("away", 3.0, 3) == "push"
    assert wager_result("away", 3.0, 2) == "win"
    assert wager_result("away", 3.5, 3) == "win"
    assert wager_result("away", -2.5, -3) == "win"     # away favourite covers
    assert wager_result("home", 7.0, -6) == "win"      # home dog +7 loses by 6


def test_multiple_accepted_lines_settle_individually():
    book = [w("home", -3.0, -110, 110), w("home", -3.5, -105, 105), w("away", 2.5, +100, 100)]
    # The away bettor took home_spread +2.5, i.e. away -2.5 (away favoured).
    # Home wins by 3: home -3 pushes, home -3.5 loses, away -2.5 loses.
    assert book_pnl_total(book, 3) == pytest.approx(0 + 105 + 100)
    # home wins by 4: both home bets win (profits 100 and 100), away loses 100
    assert book_pnl_total(book, 4) == pytest.approx(-100 - 100 + 100)
    # away wins by 3 (margin -3): home bets lose 215, away -2.5 covers -> pays 100
    assert book_pnl_total(book, -3) == pytest.approx(110 + 105 - 100)


def test_middle_and_opposing_liabilities():
    # Book took home -2.5 early and away +3.5 late (line moved). Margin 3 = both bettors win.
    book = [w("home", -2.5, -110, 1100), w("away", -3.5, -110, 1100)]
    e = exposure(book)
    assert e["pnl_if_home_covers_all"] == pytest.approx(1100 - 1000)
    assert e["pnl_if_away_covers_all"] == pytest.approx(1100 - 1000)
    assert e["status"] == "TIE" and e["financial_need_side"] is None
    assert e["middle_risk_margins"] == [3]
    assert e["worst_case_pnl"] == pytest.approx(-2000) and e["worst_case_margins"] == [3]
    assert e["push_margins"] == []


def test_book_middle_benefit_and_whole_number_push():
    # Book took home -2 and away +3 (home_spread -3). Margin 2: home pushes, away +3 wins;
    # margin 3: home -2 wins, away +3 pushes. Both are worse for the book than either clean cover.
    book = [w("home", -2.0, -110, 110), w("away", -3.0, -110, 110)]
    e = exposure(book)
    assert set(e["push_margins"]) == {2.0, 3.0}
    assert book_pnl_total(book, 2) == pytest.approx(0 - 100)
    assert book_pnl_total(book, 3) == pytest.approx(-100 + 0)
    assert exposure(book)["middle_risk_margins"] == [2, 3]
    # Opposite ordering: home -3, away +2 (home_spread -2): margin 2 -> home loses, away pushes; 3 -> push/ away loses
    book2 = [w("home", -3.0, -110, 110), w("away", -2.0, -110, 110)]
    assert book_pnl_total(book2, 2) == pytest.approx(110)
    assert book_pnl_total(book2, 3) == pytest.approx(110)
    assert exposure(book2)["middle_benefit_margins"] == [2, 3]


def test_reference_narrow_scenarios_and_push():
    book = [w("home", -3.0, -110, 110), w("away", -3.0, -110, 220)]
    r = reference_scenarios(book, -3.0)
    assert r["home_narrow_cover_margin"] == 4 and r["away_narrow_cover_margin"] == 2
    assert r["push_margin"] == 3 and r["pnl_at_reference_push"] == 0.0
    assert r["pnl_home_narrow_cover"] == pytest.approx(220 - 100)
    assert r["pnl_away_narrow_cover"] == pytest.approx(110 - 200)


def test_single_line_matches_v3_definition():
    # With one line the clean-cover Need equals V3's two-outcome definition.
    book = [w("home", -3.5, -108, 1000), w("away", -3.5, -110, 800)]
    e = exposure(book)
    assert e["pnl_if_home_covers_all"] == pytest.approx(800 - 1000 * 100 / 108, abs=0.01)
    assert e["pnl_if_away_covers_all"] == pytest.approx(1000 - 800 * 100 / 110, abs=0.01)
    assert e["financial_need_side"] == "away"


def test_full_week_pnl_reconciles_exactly(full_week):
    from sportsbook_v4.ledger_read import fetch_wagers
    for g in full_week["slate"].games:
        ws = fetch_wagers(full_week["conn"], full_week["run_id"], g.canonical_game_id)
        assert ws, g.canonical_game_id
        e = exposure(ws)
        assert e["tickets"] == len(ws)
        assert e["handle"] == pytest.approx(sum(x["stake"] for x in ws), abs=0.01)
        for m in margin_grid(ws) + [10_000, -10_000]:
            assert book_pnl_total(ws, m) == pytest.approx(float(exact_pnl(ws, m)), abs=0.01)
        assert e["pnl_if_home_covers_all"] == pytest.approx(float(exact_pnl(ws, 10_000)), abs=0.01)
        assert e["pnl_if_away_covers_all"] == pytest.approx(float(exact_pnl(ws, -10_000)), abs=0.01)
        arch = e["by_archetype"]
        assert sum(a["tickets"] for a in arch.values()) == e["tickets"]
        assert sum(a["handle"] for a in arch.values()) == pytest.approx(e["handle"], abs=0.05)
        assert sum(a["pnl_if_home_covers_all"] for a in arch.values()) == pytest.approx(
            e["pnl_if_home_covers_all"], abs=0.05)
