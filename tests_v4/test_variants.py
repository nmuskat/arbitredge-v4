"""Parameter variants (diagnostics) and common random numbers."""

import pytest

import sportsbook_v4.params as P
from sportsbook_v4.engine import RunMismatch, advance, create_run, load_run
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.population import ensure_population
from tests_v4 import fixtures as fx


def test_variant_changes_hash_and_always_restores():
    base = P.params_hash()
    with P.variant("lag0", book_overrides={"offer_lag_seconds": 0}):
        assert P.BOOK["offer_lag_seconds"] == 0 and P.params_hash() != base
    assert P.params_hash() == base and P.BOOK["offer_lag_seconds"] == 600
    with pytest.raises(ZeroDivisionError):
        with P.variant("boom", all_archetypes={"loyalty": 9.0}):
            1 / 0
    assert P.params_hash() == base and P.ARCHETYPES["REC_CASUAL"]["loyalty"] == 0.0


def test_variants_are_validated_and_do_not_nest():
    with pytest.raises(ValueError):
        with P.variant("bad", archetype_overrides={"SHARP": {"share": 0.5}}):
            pass
    assert P.params_hash() == P.params_hash()
    with P.variant("a"):
        with pytest.raises(RuntimeError):
            with P.variant("b"):
                pass


def test_run_created_under_a_variant_cannot_continue_under_baseline():
    conn = fx.new_v4_db()
    market = fx.build_quotes()
    with P.variant("lag0", book_overrides={"offer_lag_seconds": 0}):
        pid = ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=200)
        run_id = create_run(conn, pid, fx.build_slate(), market)
    with pytest.raises(RunMismatch):
        advance(conn, run_id, fx.WINDOW_END, market, NoFacts(), fx.WINDOW_END)


def _arrivals(variant_kwargs, market):
    conn = fx.new_v4_db()
    with P.variant("v", **variant_kwargs):
        pid = ensure_population(conn, "nfl", fx.NFL_TEAMS, bettor_count=1500)
        run_id = create_run(conn, pid, fx.build_slate(), market)
        s = advance(conn, run_id, fx.WINDOW_END, market, NoFacts(), fx.WINDOW_END)
        return s["arrivals"], load_run(conn, run_id)["rng_key"], run_id


def test_common_random_numbers_across_params_and_market_variants():
    a = _arrivals({}, fx.build_quotes(source_id="m1"))
    b = _arrivals({"book_overrides": {"offer_lag_seconds": 3600}}, fx.build_quotes(source_id="m1"))
    c = _arrivals({}, fx.build_quotes(fx.static_week(), source_id="m2"))
    assert a[1] == b[1] == c[1]               # same randomness identity
    assert a[0] == b[0] == c[0]               # identical arrival draws
    assert len({a[2], b[2], c[2]}) == 3       # but distinct, non-interchangeable runs


def test_market_counterfactual_fixtures():
    static = fx.static_week()
    assert all(len(path) == 1 for *_x, path in static)
    rev = {g[0]: g[6] for g in fx.reversed_week()}
    assert [p[1] for p in rev["g_sun1"]] == [-9.5, -8.5]          # -9.5 -> -10.5 mirrored
    assert rev["g_snf"][1][2:] == (100, -120)                      # juice-only move swaps sides
