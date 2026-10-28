"""Bettor behaviour: no unintended home/away bias, stake ordering, ticket vs
handle divergence (tests 1, 2, 3). These test the MODEL'S INTERNAL
PROPERTIES only -- they say nothing about whether the parameters match
real bettors (they are UNVALIDATED)."""

import random
from statistics import mean, median

import pytest

from sportsbook_v4.facts import NoFacts
from sportsbook_v4.engine import GameSpec, _recreational_side, advance, create_run
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.params import ARCHETYPES
from sportsbook_v4.population import ensure_population, load_population
from sportsbook_v4.settlement import exposure
from tests_v4 import fixtures as fx

RECREATIONAL = ("REC_CASUAL", "TEAM_LOYAL", "UNDERDOG_CONTRARIAN", "NARRATIVE_CHASER")


@pytest.fixture(scope="module")
def population():
    conn = fx.new_v4_db()
    pid = ensure_population(conn, "nfl", fx.NFL_TEAMS)
    return load_population(conn, pid)["bettors"]


def test_active_params_assume_no_home_preference():
    assert all(a["home_pref"][0] == 0.0 for a in ARCHETYPES.values())


def test_side_probability_is_exactly_antisymmetric_under_home_away_swap(population):
    facts = {"home_wins_last_3": 3, "away_wins_last_3": 0, "home_current_win_streak": 4}
    mirrored = {"away_wins_last_3": 3, "home_wins_last_3": 0, "away_current_win_streak": 4}
    g = GameSpec("x", "nfl_buf", "nfl_dal", "2026-11-01T18:00:00Z")
    gm = GameSpec("xm", "nfl_dal", "nfl_buf", "2026-11-01T18:00:00Z")
    offer, offer_m = {"home_spread": -3.5}, {"home_spread": 3.5}
    checked = 0
    for b in population[:2000]:
        b0 = dict(b, traits=dict(b["traits"], home_pref=0.0))
        arch = ARCHETYPES[b0["archetype"]]
        _, d1 = _recreational_side(random.Random(1), b0, arch, g, offer, facts)
        _, d2 = _recreational_side(random.Random(1), b0, arch, gm, offer_m, mirrored)
        assert d1["p_home"] == pytest.approx(1.0 - d2["p_home"], abs=1e-6)
        checked += 1
    assert checked == 2000


def test_population_home_pref_centred(population):
    hp = [b["traits"]["home_pref"] for b in population]
    assert abs(mean(hp)) < 0.005      # population mean ~0 (sd 0.1 per bettor)


def test_no_systematic_home_lean_on_symmetric_slate():
    week = fx.symmetric_week()
    conn = fx.new_v4_db()
    pid = ensure_population(conn, "nfl", fx.NFL_TEAMS)
    market, slate = fx.build_quotes(week), fx.build_slate(week, slate_id="symmetric")
    run_id = create_run(conn, pid, slate, market)
    advance(conn, run_id, fx.WINDOW_END, market, NoFacts(), fx.WINDOW_END)
    ws = fetch_wagers(conn, run_id)
    home_share = sum(w["side"] == "home" for w in ws) / len(ws)
    # Binomial sd at n~15k is ~0.4pp; allow 1.5pp.
    assert abs(home_share - 0.5) < 0.015, home_share
    # Mirror pairs: home share of "a" ~ away share of "b" (same matchup, venues swapped).
    for base in ("s1", "s2", "s3", "s4"):
        a = [w for w in ws if w["canonical_game_id"] == base + "a"]
        b = [w for w in ws if w["canonical_game_id"] == base + "b"]
        ha = sum(w["side"] == "home" for w in a) / len(a)
        hb = sum(w["side"] == "home" for w in b) / len(b)
        assert abs(ha - (1 - hb)) < 0.06, (base, ha, hb)


def test_high_stakes_bettors_have_larger_stakes_not_more_bets(population):
    def expected_unit(b):
        return b["bankroll"] * b["traits"]["unit_fraction"]
    by = {}
    for b in population:
        by.setdefault(b["archetype"], []).append(b)
    hs_unit = median(expected_unit(b) for b in by["HIGH_STAKES_REC"])
    for rec in RECREATIONAL:
        assert hs_unit >= 10 * median(expected_unit(b) for b in by[rec]), rec
    hs_freq = mean(b["traits"]["arrivals_per_week"] for b in by["HIGH_STAKES_REC"])
    assert hs_freq <= mean(b["traits"]["arrivals_per_week"] for b in by["REC_CASUAL"])


def test_accepted_stake_ordering_in_run(full_week):
    ws = fetch_wagers(full_week["conn"], full_week["run_id"])
    by = {}
    for w in ws:
        by.setdefault(w["archetype"], []).append(w["stake"])
    hs = median(by["HIGH_STAKES_REC"])
    for rec in RECREATIONAL:
        assert hs >= 10 * median(by[rec]), (rec, hs, median(by[rec]))
    assert median(by["SHARP"]) > median(by["SELECTIVE_PRICE_SENSITIVE"]) > median(by["REC_CASUAL"])


def test_ticket_and_handle_shares_can_diverge(full_week):
    gaps = []
    for g in full_week["slate"].games:
        e = exposure(fetch_wagers(full_week["conn"], full_week["run_id"], g.canonical_game_id))
        gaps.append(abs(e["home_ticket_pct"] - e["home_handle_pct"]))
    # tickets come from counts and handle from stakes, so they are not identical by construction. Since
    # engine m4 the book moves after sharp action, so sharp volume (the largest source of divergence in
    # m1) is bounded and the fixture's largest gap is ~8.5 points.
    assert max(gaps) >= 5.0 and sum(g > 1.0 for g in gaps) >= len(gaps) // 2, gaps


def test_sharps_only_bet_when_book_number_is_off_market(full_week):
    import json
    rows = full_week["conn"].execute(
        "SELECT decision_json FROM v4_wager WHERE run_id=? AND archetype='SHARP'", (full_week["run_id"],)
    ).fetchall()
    assert rows
    for (dj,) in rows:
        d = json.loads(dj)
        assert d["edge_points"] >= 0.5 or d["edge_prob"] >= 0.015
