"""V5 r1: individual customers, weekly participation, bankroll-driven stakes, frozen week-open bankrolls."""

import gzip
import json
import statistics

import pytest

from sportsbook_v4 import ENGINE_VERSION, customers_v5 as C
from sportsbook_v4.params import ARCHETYPES
from sportsbook_v4.population import generate_bettor

TEAMS = [f"nfl_t{i}" for i in range(32)]
POP = [generate_bettor("nfl", i, TEAMS) for i in range(3000)]


def test_active_engine_is_v5_and_its_parameters_are_pinned():
    assert ENGINE_VERSION == "sportsbook_v5.r1"
    # changing any V5 behaviour parameter requires a new engine release (r2, ...), never an edit of r1
    assert C.V5_PARAMS_HASH == "e5e5280907a705f98b98508ceb9d8c7bee1a618953d4c7de6747448e99a47e5d"


def test_customers_keep_m4_identities_and_get_persistent_individual_traits():
    b = POP[7]
    assert C.individual(b) == C.individual(dict(b))                 # deterministic in the customer's id
    eff, arch, _active, ind = C.effective(b, b["bankroll"], 0)
    assert eff["bettor_uid"] == b["bettor_uid"] and eff["bankroll"] == b["bankroll"]
    assert abs(sum(ind["market_weights"].values()) - 1) < 1e-9


def test_customers_within_one_group_differ():
    rc = [C.individual(b) for b in POP if b["archetype"] == "REC_CASUAL"]
    for key, spread in (("p_active", 0.05), ("max_implied_prob", 0.005), ("primetime_mult", 0.2),
                        ("weekly_risk_fraction", 0.05)):
        vals = [x[key] for x in rc]
        assert statistics.pstdev(vals) > spread, key
    assert statistics.pstdev([x["market_weights"]["spread"] for x in rc]) > 0.05
    assert len({x["steady_blend"] for x in rc}) >= 4                 # several timing blends (quarters)


def test_weekly_participation_varies_by_week_and_customer():
    w1, w2 = 1_793_102_400, 1_793_102_400 + 7 * 86400
    act1 = [C.effective(b, b["bankroll"], w1)[2] for b in POP]
    act2 = [C.effective(b, b["bankroll"], w2)[2] for b in POP]
    share = sum(act1) / len(act1)
    assert 0.35 < share < 0.75                                         # many customers sit a week out
    assert act1 != act2                                                # a different set each week
    sharps = [C.effective(b, b["bankroll"], w1)[2] for b in POP if b["archetype"] == "SHARP"]
    assert sum(sharps) / len(sharps) > share                           # pros show up more often


def test_busted_customers_cannot_bet_and_stakes_scale_with_current_bankroll():
    b = POP[3]
    assert C.effective(b, 4.99, 0)[2] is False and C.effective(b, 4.99, 0)[3]["busted"]
    rich = C.effective(b, 2000.0, 0)[0]["bankroll"]
    poor = C.effective(b, 50.0, 0)[0]["bankroll"]
    assert (rich, poor) == (2000.0, 50.0)                              # the engine stakes from these


def test_group_parameters_are_never_modified():
    before = json.dumps(ARCHETYPES, sort_keys=True)
    for b in POP[:200]:
        C.effective(b, b["bankroll"], 0)
    assert json.dumps(ARCHETYPES, sort_keys=True) == before


# ------------------------------------------------------------------ frozen week-open bankrolls

def _week(tmp_path):
    from sportsbook_v4 import live_book as LB
    from tests_v4.test_live_book import NOW, T0, build_store, new_book, poll
    live = build_store()
    d = tmp_path / "ledger"
    book = new_book()
    out = LB.build(book, live, NOW, [poll(T0 - 86400)], bettors=500, variants={}, book_in=str(d), book_out=str(d))
    return live, book, d, out


def test_v5_run_stores_its_snapshot_and_reloads_with_identity_check(tmp_path):
    from sportsbook_v4 import persist
    from tests_v4.test_live_book import new_book
    live, book, d, out = _week(tmp_path)
    rid = out["nfl"]["audit"]["run_id"]
    assert out["nfl"]["audit"]["engine_version"] == "sportsbook_v5.r1"
    snap = persist.snapshot_rows(book, rid)
    assert len(snap) == 500 and all(b >= 0 for _i, b in snap)
    fresh = new_book()
    persist.load_dir(fresh, str(d))
    assert persist.snapshot_rows(fresh, rid) == snap
    first = sorted(p for p in d.iterdir() if rid[:12] in p.name)[0]
    doc = json.load(gzip.open(first, "rt"))
    doc["bankroll_snapshot"][0][1] += 1000.0                            # tamper with one bankroll
    from sportsbook_v4.util import json_hash
    keys = ("format", "population", "run", "interval", "wagers", "bankroll_snapshot", "v5_params_hash")
    doc["rows_sha256"] = json_hash({k: doc[k] for k in keys})
    with gzip.open(first, "wt") as f:
        json.dump(doc, f)
    with pytest.raises(persist.LedgerIntegrityError, match="bankroll snapshot does not match"):
        persist.load_dir(new_book(), str(d))


def test_snapshot_table_is_append_only(tmp_path):
    import sqlite3
    _live, book, _d, out = _week(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        book.execute("UPDATE v5_bankroll_snapshot SET bankroll=0")


def test_week_open_bankrolls_carry_forward_settled_results_of_earlier_weeks_only(tmp_path):
    from sportsbook_v4 import bettor_settlement as BS, live_book as LB
    from sportsbook_v4.ledger_read import fetch_wagers
    from sportsbook_v4.util import to_epoch
    live, book, _d, out = _week(tmp_path)
    rid = out["nfl"]["audit"]["run_id"]
    run_end = to_epoch(out["nfl"]["audit"]["window"][1])
    ws = [dict(w, run_id=rid, population_id=out["nfl"]["audit"]["population_id"], sport="nfl")
          for w in fetch_wagers(book, rid, market=None)]
    res = {("nfl", w["canonical_game_id"]): {"home_score": 30, "away_score": 0, "source": "test", "source_id": "x",
                                             "fetched_at": "t"} for w in ws}
    sets = BS.new_settlements(ws, res, [], "t")
    pid = out["nfl"]["audit"]["population_id"]
    nxt = LB.week_open_bankrolls(book, pid, "nfl", run_end, sets)            # the following week
    same = LB.week_open_bankrolls(book, pid, "nfl", run_end - 86400, sets)  # a week that began earlier
    opening = dict(LB.week_open_bankrolls(book, pid, "nfl", run_end, []))
    pnl = {}
    for s in sets:
        pnl[s["bettor_index"]] = pnl.get(s["bettor_index"], 0) + s["pnl_cents"]
    for i, b in nxt:
        assert b == max(0.0, round(opening[i] + pnl.get(i, 0) / 100.0, 2))
    assert dict(same) == opening                                        # this week's own results don't count
    assert any(dict(nxt)[i] != opening[i] for i in pnl)
