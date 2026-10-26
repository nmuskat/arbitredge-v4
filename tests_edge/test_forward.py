import time

import numpy as np
import pytest

from edge_v4 import ev, forward, ledger
from market_v4 import normalize, store
from sims_v4 import distributions as D

EID = "f" * 32
KICK = "2026-11-01T18:00:00Z"


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def payload(t, soft_home_price):
    def bk(key, hp, ap, tot=(-110, -110), ml=(-150, 130)):
        return {"key": key, "last_update": iso(t), "markets": [
            {"key": "spreads", "last_update": iso(t), "outcomes": [{"name": "H", "price": hp, "point": -3.0},
                                                                   {"name": "A", "price": ap, "point": 3.0}]},
            {"key": "totals", "last_update": iso(t), "outcomes": [{"name": "Over", "price": tot[0], "point": 44.5},
                                                                  {"name": "Under", "price": tot[1], "point": 44.5}]},
            {"key": "h2h", "last_update": iso(t), "outcomes": [{"name": "H", "price": ml[0]}, {"name": "A", "price": ml[1]}]}]}
    return [{"id": EID, "sport_key": "americanfootball_nfl", "commence_time": KICK, "home_team": "H", "away_team": "A",
             "bookmakers": [bk("pinnacle", -108, -102), bk("circasports", -108, -102), bk("lowvig", -107, -103),
                            bk("draftkings", soft_home_price, -125)]}]


@pytest.fixture(scope="module")
def models():
    kn = D.KeyNumberModel(D.M_RANGE, True)
    kn.w = {k: (2.5 if k == 3 else 1.5 if k == 7 else 1.0) for k in range(81)}
    tk = D.KeyNumberModel(D.T_RANGE, False)
    tk.w = {int(t): 1.0 for t in D.T_RANGE}
    return kn, tk


def run(models, soft_price, validated=False, age_min=5):
    kn, tk = models
    m = store.connect()
    t = store.iso_to_epoch("2026-10-30T12:00:00Z")
    q, an = normalize.normalize(payload(t, soft_price), iso(t))
    store.ingest(m, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    L = ledger.connect()
    ledger.register(L, forward.STRATEGY, forward.VERSION, forward.DESCRIPTION,
                    {"policy": ev.DecisionPolicy().__dict__, "max_age_s": 45 * 60}, "2026-10-26T00:00:00Z")
    if validated:   # simulate an owner-approved validated strategy in a scratch ledger
        L.execute("DROP TRIGGER v4e_strategy_nu")
        L.execute("UPDATE v4e_strategy SET validated=1")
    rows = forward.scan(m, L, [{"event_id": EID, "kickoff": store.iso_to_epoch(KICK), "home": "H", "away": "A"}],
                        t + 60 * age_min, kn, 13.3, tk, 13.5)
    return rows, L


def test_soft_book_price_beating_consensus_is_flagged_but_capped_at_watch(models):
    rows, L = run(models, +115)                # DraftKings home -3 at +115 vs consensus ~ -108
    top = rows[0]
    assert top["book"] == "draftkings" and top["selection"] == "home" and top["decision"] == ev.WATCH
    assert top["ev_interval"][0] > 0.02
    n = L.execute("SELECT COUNT(*) FROM v4e_decision WHERE mode='forward'").fetchone()[0]
    assert n == len([r for r in rows if r.get("selection")])


def test_validated_strategy_can_reach_bet(models):
    rows, _ = run(models, +115, validated=True)
    assert rows[0]["decision"] == ev.BET


def test_fair_market_produces_no_opportunity(models):
    rows, _ = run(models, -108)
    assert all(r["decision"] == ev.NO_BET for r in rows)


def test_stale_market_is_no_bet(models):
    rows, _ = run(models, +115, age_min=120)
    assert all(r["decision"] == ev.NO_BET for r in rows) and any("STALE" in " ".join(r["reasons"]) for r in rows)
