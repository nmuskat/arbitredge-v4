"""SHARP VS PUBLIC: simulated sharps vs simulated recreational customers, per game-market, from accepted wagers."""

import pytest

from sportsbook_v4 import sharp_public as SP

_n = [0]


def w(arch, side, stake, bettor=None, market="spread"):
    _n[0] += 1
    return {"wager_id": f"w{_n[0]}", "archetype": arch, "side": side, "stake": float(stake), "market": market,
            "bettor_index": _n[0] if bettor is None else bettor}


def rec(side, k, stake=100, market="spread"):
    return [w("REC_CASUAL", side, stake, market=market) for _ in range(k)]


def sharp(side, stakes, bettors=None, market="spread"):
    return [w("SHARP", side, s, None if bettors is None else bettors[i], market) for i, s in enumerate(stakes)]


def test_percentages_use_each_groups_own_denominator():
    ws = rec("home", 30, 100) + rec("away", 10, 100) + sharp("away", [1000, 1000, 500]) + sharp("home", [500])
    x = SP.market(ws, "spread")
    assert x["recreational"]["sides"]["home"]["handle_pct"] == 75.0         # 3,000 of 4,000 rec handle
    assert x["recreational"]["sides"]["home"]["ticket_pct"] == 75.0
    assert x["sharp"]["sides"]["away"]["handle_pct"] == 83.3                # 2,500 of 3,000 sharp handle
    assert x["sharp"]["sides"]["away"]["ticket_pct"] == 75.0                # 3 of 4 sharp tickets
    assert x["sharp_share_of_handle_pct"] == 42.9                           # 3,000 of 7,000 total
    assert x["sharp"]["sides"]["away"]["handle"] == 2500.0 and x["recreational"]["sides"]["away"]["tickets"] == 10
    assert x["classification"] == "SHARP_VS_PUBLIC" and x["display"] == "SHARP VS PUBLIC"


def test_aligned_when_both_majorities_on_the_same_side():
    ws = rec("home", 30) + rec("away", 10) + sharp("home", [800, 700, 600])
    assert SP.market(ws, "spread")["classification"] == "ALIGNED"


def test_a_single_sharp_wager_never_creates_a_signal():
    ws = rec("home", 40) + sharp("away", [3000])
    x = SP.market(ws, "spread")
    assert x["classification"] == "INSUFFICIENT" and x["display"] == "MIXED / INSUFFICIENT DATA"
    assert any("sharp wagers" in r for r in x["reasons"])


def test_one_sharp_customer_splitting_tickets_is_not_enough():
    ws = rec("home", 40) + sharp("away", [1000, 1000, 1000], bettors=[7, 7, 7])
    x = SP.market(ws, "spread")
    assert x["classification"] == "INSUFFICIENT" and any("sharp customers" in r for r in x["reasons"])


def test_one_dominant_sharp_customer_is_not_enough():
    ws = rec("home", 40) + sharp("away", [3000, 3000, 100, 100], bettors=[1, 1, 2, 3])
    assert any("one sharp customer" in r for r in SP.market(ws, "spread")["reasons"])


def test_tiny_sharp_share_of_handle_is_insufficient():
    ws = rec("home", 200, 100) + sharp("away", [100, 100, 100])                # 300 of 20,300 = 1.5%
    x = SP.market(ws, "spread")
    assert x["classification"] == "INSUFFICIENT" and any("of total" in r for r in x["reasons"])


def test_no_clear_majority_is_mixed():
    ws = rec("home", 21) + rec("away", 19) + sharp("away", [500, 500, 500])     # rec 52.5% home: no majority
    x = SP.market(ws, "spread")
    assert x["classification"] == "MIXED" and x["recreational"]["majority_side"] is None


def test_totals_and_moneylines_are_separate_markets_with_their_own_sides():
    ws = (rec("over", 30, market="total") + sharp("under", [500, 500, 500], market="total")
          + rec("home", 30, market="moneyline") + sharp("home", [500, 500, 500], market="moneyline"))
    g = SP.game(ws)
    assert g["total"]["classification"] == "SHARP_VS_PUBLIC" and g["total"]["sharp"]["majority_side"] == "under"
    assert g["moneyline"]["classification"] == "ALIGNED"
    assert g["spread"]["status"] == "NO_WAGERS"
    assert g["total"]["total_tickets"] == 33                                   # moneyline wagers not counted


def test_classification_is_independent_of_need():
    import ast
    import inspect
    names = {n.module for n in ast.walk(ast.parse(inspect.getsource(SP))) if isinstance(n, ast.ImportFrom)}
    assert names == {"sportsbook_v4.params"}                                   # no Need, settlement or P&L inputs


def test_thresholds_are_fixed():
    assert SP.THRESHOLDS == {"min_sharp_tickets": 3, "min_sharp_bettors": 2, "max_one_sharp_share": 0.7,
                             "min_sharp_share_of_handle": 0.05, "min_rec_tickets": 20, "majority": 0.55}


def test_every_built_game_carries_it_next_to_an_unchanged_need(tmp_path):
    from sportsbook_v4 import live_book as LB
    from tests_v4.test_live_book import NOW, T0, build_store, new_book, poll
    out = LB.build(new_book(), build_store(), NOW, [poll(T0 - 86400)], bettors=500, variants={},
                   book_in=str(tmp_path), book_out=str(tmp_path))
    for g in out["nfl"]["games"]:
        assert set(g["sharp_public"]) == {"spread", "total", "moneyline"}
        for m, x in g["sharp_public"].items():
            if x["status"] == "OK":
                assert x["total_tickets"] == g["splits"][m]["tickets"]
                assert x["total_handle"] == pytest.approx(g["splits"][m]["handle"])


def test_bet_at_reports_the_numbers_each_group_actually_got():
    def bet(side, line, price, stake, at, arch="SHARP"):
        x = w(arch, side, stake)
        x.update(accepted_line=line, accepted_price=price, accepted_at=at)
        return x
    ws = [bet("home", -2.5, -110, 300, "2026-11-03T12:00:00.000000Z"),
          bet("home", -3.0, -105, 100, "2026-11-05T12:00:00.000000Z"),
          bet("away", -0.0, -110, 50, "2026-11-04T12:00:00.000000Z")] + rec("away", 25)
    x = SP.market(ws, "spread")
    b = x["sharp"]["sides"]["home"]["bet_at"]
    assert (b["min_line"], b["max_line"], b["avg_line"]) == (-3.0, -2.5, -2.62)     # stake-weighted
    assert (b["last_line"], b["last_price"], b["last_at"]) == (-3.0, -105, "2026-11-05T12:00:00.000000Z")
    a = x["sharp"]["sides"]["away"]["bet_at"]
    assert str(a["last_line"]) == "0.0" and str(a["min_line"]) == "0.0"              # never "-0.0"
    assert x["recreational"]["sides"]["away"]["bet_at"] is None                      # no prices in these test rows
    assert x["classification"] in ("SHARP_VS_PUBLIC", "ALIGNED", "MIXED", "INSUFFICIENT")
