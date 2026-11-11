"""V4b2 (research): point-in-time facts, price-acceptance rules, deterministic individual traits, and the
production path untouched (configuration A unchanged is covered by test_lab.py)."""
from lab_v4 import behavior_v2 as BV2
from lab_v4.config import CONFIGS


def test_facts_never_include_the_game_itself_or_recent_unfinished_games():
    games = [("nfl", "g1", 1000, "A", "B"), ("nfl", "g2", 200000, "A", "C"), ("nfl", "g3", 400000, "B", "A")]
    finals = {("nfl", "g1"): (30, 10), ("nfl", "g2"): (7, 21), ("nfl", "g3"): (50, 0)}
    f = BV2.RealFacts("nfl", games, finals)
    x = f.facts("g3", 400000 - 3600)                         # decision 1 h before g3: g3's own result unknown
    assert x["away_current_win_streak"] == 0                   # A lost g2 (its latest known game)
    assert x["away_point_margin_avg_last_3"] == (20 - 14) / 2  # g1 +20, g2 -14; never g3's +-50
    y = f.facts("g2", 1000 + 3600)                             # g1 kicked off only 1 h earlier: not yet known
    assert "home_point_margin_avg_last_3" not in y


def test_price_acceptance_rules():
    ind = {"tolerance": 0.04, "sensitivity": 20.0, "ev_floor": -0.02}
    assert BV2.price_accept("SELECTIVE_PRICE_SENSITIVE", ind, -0.025, False, 0.0)[0] is False
    assert BV2.price_accept("SELECTIVE_PRICE_SENSITIVE", ind, -0.015, False, 0.99)[0] is True
    assert BV2.price_accept("TEAM_LOYAL", ind, -0.20, True, 0.99)[0] is True        # own team: no price check
    assert BV2.price_accept("TEAM_LOYAL", ind, -0.20, False, 0.5)[0] is False
    assert BV2.price_accept("REC_CASUAL", ind, -0.03, False, 0.999)[0] is True       # within tolerance
    assert BV2.price_accept("REC_CASUAL", ind, None, False, 0.999)[0] is True        # no fair known: unchanged


def test_individual_traits_deterministic_and_distinct():
    a = BV2.ind2({"archetype": "REC_CASUAL", "bettor_uid": "u1"})
    b = BV2.ind2({"archetype": "REC_CASUAL", "bettor_uid": "u1"})
    c = BV2.ind2({"archetype": "REC_CASUAL", "bettor_uid": "u2"})
    assert a == b and a != c


def test_dog_participation_rises_with_size():
    assert BV2.dog_participation("spread", {"line": 3.0, "price": -110}) < \
        BV2.dog_participation("spread", {"line": 10.0, "price": -110})


def test_configs_flagged_research_only():
    for k in ("B2_20K", "B2_10K", "B2_5K"):
        assert CONFIGS[k]["v2"] and CONFIGS[k]["sharp_ev"] and CONFIGS[k]["max_win"]
    assert not CONFIGS["A"].get("v2")
