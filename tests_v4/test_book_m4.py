"""Engine m4: a side-neutral, coherent offered market, and the book's own line management after
respected (sharp) action. Regression tests for the bugs found in the 2026-10-28 audit."""
import json
import random

import pytest

from sportsbook_v4 import engine as E
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.market import (Quote, implied_probability, representative_ml_offer, representative_offer,
                                  representative_total_offer)
from sportsbook_v4.params import BOOK


def q(book, hs, hp=-110, ap=-110, total=None, op=None, up=None, hml=None, aml=None, ref=None):
    return Quote(book=book, effective_epoch=0, home_spread=hs, home_price=hp, away_price=ap,
                 ref=ref or f"r:{book}", total=total, over_price=op, under_price=up, home_ml=hml, away_ml=aml)


def mirror(x):
    return Quote(book=x.book, effective_epoch=0, home_spread=None if x.home_spread is None else -x.home_spread,
                 home_price=x.away_price, away_price=x.home_price, ref=x.ref, total=x.total,
                 over_price=x.under_price, under_price=x.over_price, home_ml=x.away_ml, away_ml=x.home_ml)


MARKETS = [
    # the real Indiana case: retail -20.5, market makers -19.5, Pinnacle -21
    [q("pinnacle", -21.0, -102, -108), q("lowvig", -19.5, -122, -102), q("betonlineag", -19.5, -125, +100),
     q("draftkings", -20.5, -112, -108), q("fanduel", -20.5, -110, -110), q("betmgm", -20.5, -110, -110)],
    [q("a", -3.0, -115, -105), q("b", -3.5, -105, -115), q("c", -3.0, -120, +100), q("d", -2.5, -125, +105)],
    [q("a", 7.0), q("b", 6.5, -105, -115), q("c", 7.0, -112, -108)],
]


@pytest.mark.parametrize("quotes", MARKETS)
def test_offer_mirrors_with_the_market(quotes):
    o, m = representative_offer(quotes), representative_offer([mirror(x) for x in quotes])
    assert m["home_spread"] == -o["home_spread"]
    assert (m["home_price"], m["away_price"]) == (o["away_price"], o["home_price"])
    # (the price book itself may differ only between books quoting the identical pair)


@pytest.mark.parametrize("quotes", MARKETS)
def test_both_prices_come_from_one_real_book(quotes):
    o = representative_offer(quotes)
    pairs = {x.book: (x.home_spread, x.home_price, x.away_price) for x in quotes}
    assert pairs[o["price_book"]] == (o["home_spread"], o["home_price"], o["away_price"])


def test_the_old_lower_median_bias_is_gone():
    # two books each side of the market: the old rule always gave home the fewer-points number
    flips = {"home_fewer": 0, "home_more": 0}
    rng = random.Random(7)
    for i in range(400):
        base = rng.choice([-7.0, -3.0, -1.5, 2.5, 6.0])
        qs = [q(f"b{i}x", base - 0.5, ref=f"x{i}"), q(f"b{i}y", base, ref=f"y{i}")]
        o = representative_offer(qs)
        flips["home_fewer" if o["home_spread"] == base - 0.5 else "home_more"] += 1
    assert 160 <= flips["home_fewer"] <= 240, flips       # ~50/50 coin flips on exact ties, not 100/0


def test_totals_side_neutral():
    qs = [q("a", None, total=47.5, op=-110, up=-110), q("b", None, total=48.0, op=-105, up=-115),
          q("c", None, total=48.0, op=-110, up=-110), q("d", None, total=48.5, op=-110, up=-110)]
    o = representative_total_offer(qs)
    assert o["total"] == 48.0 and o["price_book"] in ("b", "c")
    # mirror over/under around 48: totals 48.5/48/48/47.5 -> still 48, prices swapped
    m = representative_total_offer([q(x.book, None, total=96.0 - x.total, op=x.under_price, up=x.over_price)
                                    for x in qs])
    assert m["total"] == 48.0 and (m["over_price"], m["under_price"]) == (o["under_price"], o["over_price"])


def test_moneyline_pair_from_one_book():
    qs = [q("a", None, hml=-150, aml=130), q("b", None, hml=-140, aml=120), q("c", None, hml=-160, aml=135)]
    o = representative_ml_offer(qs)
    assert (o["home_price"], o["away_price"]) in {(-150, 130), (-140, 120), (-160, 135)}
    assert o["price_book"] == "a"                     # median no-vig home probability


# ---------------------------------------------------------------- line management

class _View:
    def __init__(self, retail, makers):
        self.retail, self.makers = retail, makers

    def offers(self, gid, t, books=None):
        return {"spread": self.makers if books else self.retail}


def test_book_copies_makers_after_respected_action_and_rebases_when_market_moves():
    retail = {"home_spread": -3.0, "home_price": -110, "away_price": -110}
    makers = {"home_spread": -3.5, "home_price": -105, "away_price": -115}
    fresh, base = E._book_offers(_View(retail, makers), "g", 0, {}, {"spread"})
    assert fresh["spread"] is retail and base["spread"][1] == "market"
    state = {("g", "spread"): E._book_key("spread", retail)}            # a sharp hit this retail quote
    hit, base = E._book_offers(_View(retail, makers), "g", 0, state, {"spread"})
    assert hit["spread"] is makers and base["spread"][1] == "reference"
    moved = dict(retail, home_spread=-3.5)                               # retail market moved: re-base
    after, base = E._book_offers(_View(moved, makers), "g", 0, state, {"spread"})
    assert after["spread"] is moved and base["spread"][1] == "market"


def test_sharp_volume_is_bounded_by_line_management(full_week):
    conn, run_id = full_week["conn"], full_week["run_id"]
    rows = conn.execute("SELECT canonical_game_id, market, decision_json FROM v4_wager WHERE run_id=? AND "
                        "archetype='SHARP' ORDER BY accepted_at, bettor_index, arrival_seq", (run_id,)).fetchall()
    assert rows
    per_base = {}
    for gid, m, dj in rows:
        d = json.loads(dj)
        per_base.setdefault((gid, m, json.dumps(d["book_base"])), []).append(d["book_mode"])
    # on one retail quote at most ONE sharp bet hits the book's own number; any further sharp bets on that
    # quote are against the copied market-maker number (only possible inside the re-quote lag)
    for k, modes in per_base.items():
        assert modes.count("market") <= 1, (k, modes)
    ws = fetch_wagers(conn, run_id, market=None)
    sharp = sum(w["stake"] for w in ws if w["archetype"] == "SHARP")
    assert sharp / sum(w["stake"] for w in ws) < 0.6    # sharps no longer swamp the book (m1: ~0.95 on real data)


def test_state_rebuilt_from_ledger_matches_live_state(full_week):
    conn, run_id = full_week["conn"], full_week["run_id"]
    rows = conn.execute("SELECT canonical_game_id, market, decision_json FROM v4_wager WHERE run_id=? AND "
                        "archetype='SHARP' ORDER BY accepted_at, bettor_index, arrival_seq", (run_id,)).fetchall()
    last = {}
    for gid, m, dj in rows:
        last[(gid, m)] = json.loads(dj)["book_base"]
    assert E.book_state_from_wagers(rows) == last


def test_old_model_runs_are_kept_but_superseded(tmp_path, monkeypatch):
    from sportsbook_v4 import live_book as LB, params as P
    from tests_v4.test_live_book import NOW, T0, build_store, new_book, poll
    live = build_store()
    d = tmp_path / "ledger"
    LB.build(new_book(), live, T0 + 9 * 3600, [poll(T0 - 86400)], bettors=500, variants={}, book_in=str(d),
             book_out=str(d))
    old_files = sorted(p.name for p in d.iterdir())
    monkeypatch.setitem(P.BOOK, "min_wager", 6.0)          # a model change -> new params hash
    out = LB.build(new_book(), live, NOW, [poll(T0 - 86400)], bettors=500, variants={}, book_in=str(d),
                   book_out=str(d))
    sup = out["nfl"]["superseded_runs"]
    assert len(sup) == 1 and sup[0]["slate_id"].startswith("week_nfl_20261027_")
    assert out["nfl"]["audit"]["slate_id"] != sup[0]["slate_id"] and out["nfl"]["status"] == "OK"
    assert set(old_files) <= {p.name for p in d.iterdir()}  # old files untouched; new run added alongside


def test_line_offered_by_more_books_wins_over_the_mean():
    # middle values -3.0 (3 books) and -3.5 (2 books); the mean (-3.42) is nearer -3.5, but most books deal -3
    qs = [q("a", -3.0), q("b", -3.0), q("c", -3.0), q("d", -3.5), q("e", -3.5), q("f", -4.5)]
    assert representative_offer(qs)["home_spread"] == -3.0
    assert representative_offer([mirror(x) for x in qs])["home_spread"] == 3.0
