import math
import random
import sqlite3

import pytest

from edge_v4 import credits, ev, pricing, staking, validation


# ---------------- pricing ----------------
def test_conversions():
    assert pricing.american_to_decimal(-110) == pytest.approx(1.909090909)
    assert pricing.american_to_decimal(150) == pytest.approx(2.5)
    for a in (-250, -110, 100, 135, 400):
        assert pricing.decimal_to_american(pricing.american_to_decimal(a)) == a
    assert pricing.implied_prob(-110) == pytest.approx(0.5238095)
    with pytest.raises(ValueError):
        pricing.american_to_decimal(50)


@pytest.mark.parametrize("method", list(pricing.DEVIG_METHODS))
def test_devig_symmetric_market_is_fifty_fifty(method):
    a, b = pricing.devig([-110, -110], method)
    assert a == pytest.approx(0.5) and b == pytest.approx(0.5)


@pytest.mark.parametrize("method", list(pricing.DEVIG_METHODS))
def test_devig_sums_to_one_and_preserves_order(method):
    p = pricing.devig([-300, 250], method)
    assert sum(p) == pytest.approx(1.0)
    assert p[0] > p[1]


def test_power_and_shin_shift_vig_toward_longshot():
    mult = pricing.devig([-400, 320], "multiplicative")[0]
    assert pricing.devig([-400, 320], "power")[0] > mult
    assert pricing.devig([-400, 320], "shin")[0] > mult


def test_underround_market_rejected():
    with pytest.raises(ValueError):
        pricing.devig([110, 110])


def test_consensus_and_best_price():
    fair = pricing.consensus_fair_prob({"pinnacle": (-120, 110), "dk": (-130, 110)},
                                       weights={"pinnacle": 3, "dk": 1})
    assert 0.5 < fair < 0.56
    assert pricing.best_price({"dk": -115, "fd": -105, "mgm": -110}) == ("fd", -105)


# ---------------- EV ----------------
def test_ev_basic_and_breakeven():
    assert ev.expected_value(0.5, 0.0, -110) == pytest.approx(-0.0454545, abs=1e-6)
    assert ev.breakeven_win_prob(0.0, -110) == pytest.approx(0.5238095)
    p = ev.breakeven_win_prob(0.08, -110)
    assert ev.expected_value(p, 0.08, -110) == pytest.approx(0.0, abs=1e-12)


def test_ev_push_returns_stake():
    # +100, 50% win, 10% push, 40% loss => 0.5 - 0.4
    assert ev.expected_value(0.5, 0.1, 100) == pytest.approx(0.1)


def test_spread_push_on_key_number():
    dist = {3: 0.15, 7: 0.10, 1: 0.25, -3: 0.20, 10: 0.30}
    cover, push, fail = ev.spread_outcome_probs(dist, -3.0)
    assert push == pytest.approx(0.15)
    assert cover == pytest.approx(0.40)
    assert fail == pytest.approx(0.45)
    with pytest.raises(ValueError):
        ev.spread_outcome_probs({1: 0.4}, -3)


def cand(**kw):
    base = dict(game_id="g1", market="spreads", selection="HOME -3", line=-3.0, book="fd",
                american=-105, price_age_s=60, p_win_model=0.56, p_push_model=0.0, p_win_se=0.005,
                p_win_market=0.51)
    base.update(kw)
    return ev.Candidate(**base)


def test_default_is_no_bet():
    assert ev.evaluate(cand(p_win_model=0.50))["decision"] == ev.NO_BET


def test_unvalidated_edge_capped_at_watch_and_validated_is_bet():
    assert ev.evaluate(cand())["decision"] == ev.WATCH
    assert ev.evaluate(cand(validated_strategy=True))["decision"] == ev.BET


def test_uncertainty_blocks_bet():
    r = ev.evaluate(cand(p_win_se=0.04, validated_strategy=True))
    assert r["decision"] == ev.WATCH
    lo, hi = r["ev_interval"]
    assert lo < 0 < r["ev_per_unit"] < hi


@pytest.mark.parametrize("kw,reason", [
    (dict(price_age_s=3600), "stale"),
    (dict(p_win_market=None), "benchmark"),
    (dict(p_win_model=0.70), "model error"),
])
def test_hard_no_bet_gates(kw, reason):
    r = ev.evaluate(cand(validated_strategy=True, **kw))
    assert r["decision"] == ev.NO_BET and reason in " ".join(r["reasons"])


def test_signal_agreement_never_upgrades_decision():
    a = ev.evaluate(cand(p_win_model=0.50, validated_strategy=True))
    b = ev.evaluate(cand(p_win_model=0.50, validated_strategy=True,
                         supporting=["need", "sharp", "sims"]))
    assert a["decision"] == b["decision"] == ev.NO_BET


def test_output_has_required_fields():
    r = ev.evaluate(cand())
    for k in ("game_id", "market", "selection", "book", "american", "p_win_model", "p_win_market",
              "edge_vs_market", "expected_roi", "ev_interval", "supporting", "conflicting",
              "price_age_s", "decision", "reasons", "policy_version"):
        assert k in r


# ---------------- staking ----------------
def test_kelly_formula_with_and_without_push():
    assert staking.kelly_fraction(0.55, 0.0, 100) == pytest.approx(0.10)
    p, push, a = 0.50, 0.10, 100
    f = staking.kelly_fraction(p, push, a)
    b = pricing.net_profit_per_unit(a)
    q = 1 - p - push
    g = lambda x: p * math.log(1 + b * x) + q * math.log(1 - x)
    assert g(f) >= max(g(f - 1e-3), g(f + 1e-3))
    assert staking.kelly_fraction(0.45, 0.0, -110) == 0.0


def bet_row(**kw):
    r = ev.evaluate(cand(validated_strategy=True))
    r.update(kw)
    return r


def test_only_bets_get_stakes():
    r = ev.evaluate(cand())  # WATCH
    assert staking.size_stake(r, 10_000, 10_000, {})[0] == 0.0
    assert staking.size_stake(bet_row(), 10_000, 10_000, {})[0] > 0


def test_caps_apply():
    pol = staking.StakingPolicy(kelly_multiplier=1.0)
    stake, notes = staking.size_stake(bet_row(), 10_000, 10_000, {"game": {"g1": 290.0}}, pol)
    assert stake == pytest.approx(10.0) and any("game" in n for n in notes)
    stake, _ = staking.size_stake(bet_row(), 10_000, 10_000, {"day": 1000.0}, pol)
    assert stake == 0.0
    stake, _ = staking.size_stake(bet_row(corr_group="g1-same-side"), 10_000, 10_000,
                                  {"group": {"g1-same-side": 390.0}}, pol)
    assert stake == pytest.approx(10.0)


def test_no_chasing_stake_fraction_never_rises_in_drawdown():
    pol = staking.StakingPolicy(mode="flat")
    peak = 10_000
    prev_frac = 1.0
    for bank in range(10_000, 6_000, -250):
        s, _ = staking.size_stake(bet_row(), bank, peak, {}, pol)
        frac = s / bank
        assert frac <= prev_frac + 1e-12
        prev_frac = frac
    assert staking.size_stake(bet_row(), 7_400, peak, {}, pol)[0] == 0.0  # past hard stop


# ---------------- credits ----------------
def test_cost_estimates():
    assert credits.estimate_cost("events") == 0
    assert credits.estimate_cost("odds", ["h2h", "spreads", "totals"], ["us"]) == 3
    assert credits.estimate_cost("odds", ["spreads"], bookmakers=[f"b{i}" for i in range(11)]) == 2
    assert credits.estimate_cost("historical_odds", ["h2h", "spreads"], ["us", "eu"]) == 40
    assert credits.estimate_cost("scores", days_from=3) == 2


def make_gate(budget=credits.Budget(), t0=1_790_000_000.0):
    clock = {"t": t0}
    g = credits.CreditGate(sqlite3.connect(":memory:"), budget, clock=lambda: clock["t"])
    return g, clock


def test_gate_refusals():
    g, _ = make_gate(credits.Budget(v4_monthly_cap=10, v4_daily_cap=8, v3_floor_remaining=102, max_single_request=5))
    with pytest.raises(credits.CreditRefused, match="approval"):
        g.authorize("odds", {}, 6)
    g.authorize("odds", {}, 5)
    g.record("odds", {}, 5, 200, {"x-requests-last": "5", "x-requests-remaining": "104", "x-requests-used": "50"})
    with pytest.raises(credits.CreditRefused, match="V3 floor"):
        g.authorize("odds", {}, 3)
    g.record("odds", {"x": 1}, 3, 200, {"x-requests-remaining": "5000"})
    with pytest.raises(credits.CreditRefused, match="24h"):
        g.authorize("odds", {}, 1)


def test_monthly_cap_and_month_rollover():
    g, clock = make_gate(credits.Budget(v4_monthly_cap=10, v4_daily_cap=10))
    g.record("odds", {}, 10, 200, {})
    with pytest.raises(credits.CreditRefused, match="monthly"):
        g.authorize("odds", {}, 1)
    clock["t"] += 40 * 86400
    g.authorize("odds", {}, 1)


def test_mismatch_flag_ledger_immutable_cache_and_key_redaction():
    g, _ = make_gate()
    note = g.record("odds", {"apiKey": "SECRET", "markets": "h2h"}, 1, 200, {"x-requests-last": "3"}, body={"ok": 1})
    assert "EXCEEDS" in note
    assert credits.fingerprint("odds", {"apiKey": "A", "m": 1}) == credits.fingerprint("odds", {"apiKey": "B", "m": 1})
    assert g.cached("odds", {"apiKey": "other", "markets": "h2h"}, ttl_s=60) == {"ok": 1}
    assert "SECRET" not in str(g.conn.execute("SELECT * FROM credit_ledger").fetchall())
    with pytest.raises(sqlite3.DatabaseError):
        g.conn.execute("DELETE FROM credit_ledger")
    with pytest.raises(sqlite3.DatabaseError):
        g.conn.execute("UPDATE credit_ledger SET est_cost=0")


def test_usage_split():
    g, _ = make_gate()
    g.record("odds", {}, 30, 200, {"x-requests-last": "30", "x-requests-used": "1000"})
    assert g.usage_split() == {"v4": 30, "provider_used": 1000, "v3_inferred": 970}


# ---------------- validation ----------------
def bets_from(results, american=-110, t0=0):
    return [{"placed_at": t0 + i, "stake": 1.0, "american": american, "result": r} for i, r in enumerate(results)]


def test_record_drawdown_streak():
    bets = bets_from("WWLLLPW")
    rec = validation.record(bets)
    assert (rec["W"], rec["L"], rec["P"]) == (3, 3, 1)
    assert rec["profit"] == pytest.approx(3 * 100 / 110 - 3)
    assert validation.max_drawdown(bets) == pytest.approx(3.0)
    assert validation.longest_losing_streak(bets) == 3


def test_zero_edge_coinflips_are_not_proven():
    rng = random.Random(1)
    bets = bets_from("".join(rng.choice("WL") for _ in range(1000)))
    s = validation.edge_evidence_summary(bets, n_boot=1000)
    assert s["edge_status"] == "NOT PROVEN"


def test_real_edge_with_clv_can_be_supported_but_multiple_testing_bites():
    rng = random.Random(2)
    bets = bets_from("".join("W" if rng.random() < 0.60 else "L" for _ in range(1500)))
    for b in bets:
        b["close_fair_prob"] = 0.55
    assert validation.edge_evidence_summary(bets, n_boot=1000)["edge_status"] == "SUPPORTED"
    rng = random.Random(5)
    marginal = bets_from("".join("W" if rng.random() < 0.57 else "L" for _ in range(800)))
    for b in marginal:
        b["close_fair_prob"] = 0.55
    one = validation.edge_evidence_summary(marginal, 1, n_boot=2000)
    many = validation.edge_evidence_summary(marginal, 200, n_boot=2000)
    assert one["edge_status"] == "SUPPORTED"
    assert many["edge_status"] == "NOT PROVEN"   # same bets, 200 strategies tried


def test_bootstrap_p_value_never_zero():
    rng = random.Random(7)
    strong = bets_from("".join("W" if rng.random() < 0.75 else "L" for _ in range(500)))
    ci = validation.bootstrap_roi_ci(strong, n_boot=999)
    assert ci["p_roi_le_0"] >= 1 / 1000
    assert validation.edge_evidence_summary(strong, 1000, n_boot=999)["edge_status"] == "NOT PROVEN"


def test_clv_never_imputed():
    assert validation.clv({"american": -110}) is None
    assert validation.clv({"american": -110, "close_fair_prob": 0.55}) == pytest.approx(0.55 - 0.5238095)


def test_scoring_rules_and_calibration():
    assert validation.brier([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    assert validation.log_loss([0.5], [1]) == pytest.approx(math.log(2))
    tbl = validation.calibration_table([0.15, 0.17, 0.85], [0, 1, 1], bins=10)
    assert tbl[0]["n"] == 2 and tbl[0]["observed"] == 0.5


def test_holm():
    assert validation.holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_leakage_detected():
    validation.assert_point_in_time([{"id": 1, "decided_at": 10, "odds_ts": 10, "features_ts": 5}])
    with pytest.raises(validation.LeakageError):
        validation.assert_point_in_time([{"id": 2, "decided_at": 10, "odds_ts": 11}])


def test_walk_forward_is_strictly_chronological():
    items = [{"t": t // 2} for t in range(40)]  # duplicate timestamps
    n = 0
    for train, test in validation.walk_forward_splits(items, key=lambda r: r["t"], train_min=9, test_size=5):
        assert max(r["t"] for r in train) < min(r["t"] for r in test)
        n += 1
    assert n >= 4
