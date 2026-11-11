"""intel_v7: point-in-time history, modules A-E, card contract, freezing, site gate. Quotes are TEST FIXTURES."""
import fnmatch
import gzip
import json
import sqlite3

import pytest

from intel_v7 import classify as E
from intel_v7 import modules as M
from intel_v7 import site as SITE
from intel_v7.config import CONFIG
from intel_v7.convergence import Convergence, pair
from intel_v7.history import History, view
from sportsbook_v4.util import from_epoch

T0 = 1793700000 - (1793700000 % 3600)
KICK = T0 + 2 * 86400
H = 3600
RETAIL = ["draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers"]
SPEC_FIELDS = ["game", "market", "professional_pricing_side", "market_need_signal", "evidence_strength",
               "professional_price", "retail_consensus", "key_number_analysis", "active_resistance",
               "market_convergence", "quote_freshness", "conflicting_evidence", "actual_book_liability",
               "best_observed_executable_price", "validation_status", "signal_or_pass"]


def db(fetches, sport="americanfootball_nfl", market="spreads", event="ev", kick=KICK, conn=None):
    """fetches: [(t, {book: (home_point_or_total, price_a, price_b)})]"""
    c = conn or sqlite3.connect(":memory:")
    if conn is None:
        c.execute("CREATE TABLE mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price, "
                  "market_last_update, fetched_at, source, request_fp)")
        c.execute("CREATE TABLE mkt_fetch (fetched_at, sport, endpoint, request_fp, source, n_quotes, n_anomalies)")
    a, b = ("home", "away") if market == "spreads" else ("over", "under")
    for t, books in fetches:
        for bk, (p, pa, pb) in books.items():
            pb_point = -p if market == "spreads" else p
            c.execute("INSERT INTO mkt_quote VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (event, sport, kick, "Home Team", "Away Team", bk, market, a, p, pa, t - 30, t, "test", "fp"))
            c.execute("INSERT INTO mkt_quote VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (event, sport, kick, "Home Team", "Away Team", bk, market, b, pb_point, pb, t - 30, t, "test", "fp"))
        c.execute("INSERT INTO mkt_fetch VALUES (?,?,?,?,?,?,?)", (t, sport, "odds", "fp", "test", 1, 0))
    return c


def books(retail=-3.0, pin=-3.0, bol=-3.0, rp=(-110, -110), pp=(-105, -105), bp=(-108, -102), drop=()):
    out = {bk: (retail, *rp) for bk in RETAIL}
    out["pinnacle"] = (pin, *pp)
    out["betonlineag"] = (bol, *bp)
    out["lowvig"] = (bol, *bp)
    for d in drop:
        out.pop(d, None)
    return out


def hist(fetches, sport="nfl", market="spread", **kw):
    c = db(fetches, sport={"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}[sport],
           market={"spread": "spreads", "total": "totals"}[market], **kw)
    return History(c, sport, kw.get("event", "ev"), market), c


# ------------------------------------------------------------------------------------------------ history
def test_point_in_time_and_no_postkick_quotes():
    h, _ = hist([(T0, books()), (T0 + H, books(retail=-4.0)), (KICK + 60, books(retail=-20.0))])
    assert h.snap_at(T0 - 1) == (None, {})
    assert h.snap_at(T0 + 10)[1]["draftkings"]["point"] == -3.0
    assert h.snap_at(T0 + H)[1]["draftkings"]["point"] == -4.0
    assert h.snap_at(KICK + 3600)[0] == T0 + H              # the post-kickoff fetch is never used


def test_betonline_group_counts_once():
    h, _ = hist([(T0, books(bol=-4.0))])
    v = view(h.snap_at(T0)[1])
    assert set(v["pro"]) == {"pinnacle", "betonline_group"} and v["retail_n"] == len(RETAIL)


def test_freshness_requires_quote_in_fetch():
    h, _ = hist([(T0, books()), (T0 + H, books(drop=("pinnacle",)))])
    v = view(h.snap_at(T0 + H)[1])
    assert "pinnacle" not in v["pro"] and v["pro_basis"] == "betonline_group"   # last Pinnacle quote is NOT carried
    assert M.freshness(h, T0 + H + 4 * 3600)["status"] == "STALE"


# ------------------------------------------------------------------------------------------------ A divergence
def test_no_divergence_when_prices_agree():
    h, _ = hist([(T0, books(rp=(-105, -105), bp=(-105, -105)))])
    a = M.divergence(h, T0)
    assert a["status"] == "NOT_MATERIAL" and abs(a["gap_pts"]) < 0.05


def test_divergence_direction_spread_and_total():
    h, _ = hist([(T0, books(retail=-3.0, pin=-4.0, bol=-4.0))])        # pros make home a bigger favourite
    a = M.divergence(h, T0)
    assert a["status"] == "MATERIAL" and a["side"] == "home" and a["corroborated"] and a["gap_pts"] > 0
    ht, _ = hist([(T0, books(retail=44.5, pin=43.0, bol=43.0))], market="total")
    at = M.divergence(ht, T0)
    assert at["status"] == "MATERIAL" and at["side"] == "under"


def test_juice_is_normalised_at_the_same_number():
    h, _ = hist([(T0, books(retail=-3.0, pin=-3.0, bol=-3.0, pp=(-135, 115), bp=(-135, 115)))])
    a = M.divergence(h, T0)
    assert a["gap_pts"] > 0                     # same number, home juice heavier at the pros -> pros rate home higher


def test_pros_disagree_and_insufficient():
    h, _ = hist([(T0, books(retail=-3.0, pin=-4.5, bol=-1.5))])
    assert M.divergence(h, T0)["status"] == "PROS_DISAGREE"
    h2, _ = hist([(T0, books(drop=("pinnacle", "betonlineag", "lowvig")))])
    assert M.divergence(h2, T0)["status"] == "INSUFFICIENT"            # retail alone never sets professional pricing
    h3, _ = hist([(T0, books(drop=RETAIL[2:]))])
    assert M.divergence(h3, T0)["status"] == "INSUFFICIENT"


# ------------------------------------------------------------------------------------------------ B resistance
def test_active_resistance_both_operators():
    h, _ = hist([(T0, books()), (T0 + H, books(retail=-3.5)), (T0 + 2 * H, books(retail=-4.5))])
    b = M.resistance(h, T0 + 2 * H)
    assert b["status"] == "ACTIVE_RESISTANCE" and b["side"] == "away" and b["held_by"] == ["betonline_group", "pinnacle"]


def test_resistance_with_missing_pro_quote_is_indeterminate():
    h, _ = hist([(T0, books()), (T0 + H, books(retail=-4.5, drop=("pinnacle", "betonlineag", "lowvig")))])
    assert M.resistance(h, T0 + H)["status"] == "INDETERMINATE"


def test_split_operators_is_indeterminate():
    h, _ = hist([(T0, books()), (T0 + H, books(retail=-4.5, pin=-3.0, bol=-4.5))])
    b = M.resistance(h, T0 + H)
    assert b["status"] == "INDETERMINATE" and "stale feed" in b["reason"]


def test_single_fetch_cannot_show_resistance():
    h, _ = hist([(T0, books(retail=-4.5))])
    assert M.resistance(h, T0)["status"] == "INDETERMINATE"


def test_pro_led_move():
    h, _ = hist([(T0, books()), (T0 + H, books(pin=-4.0, bol=-4.0))])
    b = M.resistance(h, T0 + H)
    assert b["status"] == "PRO_LED_MOVE" and b["side"] == "home"
    assert b["operators"]["pinnacle"]["change_windows"] == [[T0, T0 + H]]   # known only to the fetch window


# ------------------------------------------------------------------------------------------------ C key numbers
def test_key_defense_and_not_applicable():
    f = [(T0, books(pp=(-110, -110), bp=(-110, -110))),
         (T0 + H, books(retail=-3.5, pp=(-120, 100), bp=(-120, 100))),
         (T0 + 2 * H, books(retail=-3.5, pp=(-122, 102), bp=(-122, 102)))]
    small, _ = hist(f)
    assert M.key_numbers(small, T0 + 2 * H)["status"] == "NO_DEFENSE"      # 12 cents < the 0.03 no-vig bar
    f[2] = (T0 + 2 * H, books(retail=-3.5, pp=(-130, 110), bp=(-130, 110)))
    h, _ = hist(f)
    c = M.key_numbers(h, T0 + 2 * H)
    assert c["pro"]["pinnacle"]["on_key"] and c["pro"]["pinnacle"]["held_key_through_window"]
    assert c["pro"]["pinnacle"]["juice_only_changes"] >= 1 and c["pro"]["pinnacle"]["line_changes"] == 0
    assert c["status"] in ("KEY_DEFENSE", "KEY_HELD_JUICE_ONLY")
    hc, _ = hist(f, sport="cfb")
    assert M.key_numbers(hc, T0 + 2 * H)["status"] == "NOT_APPLICABLE"


# ------------------------------------------------------------------------------------------------ D convergence
def _conv_with(rows, kicks):
    cv = Convergence()
    for (g, rm, pm), k in zip(rows, kicks):
        cv.add("nfl", "spread", k, "late", (g, rm, pm))
    return cv


def test_convergence_classifies_and_is_walk_forward():
    rows = [((1 if i % 2 else -1) * 0.6, (1 if i % 2 else -1) * 0.5, 0.0) for i in range(40)]
    cv = _conv_with(rows, [T0 + i * 3600 for i in range(40)])
    assert cv.stats("nfl", "spread", "late", T0 + 10 * 3600)["status"] == "INSUFFICIENT"   # only 10 prior games
    s = cv.stats("nfl", "spread", "late", T0 + 100 * 3600)
    assert s["status"] == "PRO_LEADS" and s["games"] == 40 and s["retail_toward_pro"] == pytest.approx(0.833, abs=0.01)
    rows2 = [((1 if i % 2 else -1) * 0.6, 0.0, -(1 if i % 2 else -1) * 0.5) for i in range(40)]
    assert _conv_with(rows2, [T0 + i for i in range(40)]).stats("nfl", "spread", "late", T0 + 999)["status"] == "RETAIL_LEADS"


def test_convergence_pair_from_history():
    f = [(KICK - 7 * H, books(retail=-3.0, pin=-4.0, bol=-4.0)), (KICK - 20 * 60, books(retail=-4.0, pin=-4.0, bol=-4.0))]
    h, _ = hist(f)
    g, rm, pm = pair(h, "early")
    assert g > 0 and rm > 0 and abs(pm) < 1e-9


# ------------------------------------------------------------------------------------------------ E classifier + card
def test_card_contract_and_liability_unknown():
    h, _ = hist([(T0, books()), (T0 + H, books(retail=-3.0, pin=-4.0, bol=-4.0))])
    c = E.card(h, T0 + H, Convergence())
    for k in SPEC_FIELDS:
        assert k in c
    assert c["actual_book_liability"] == "UNKNOWN"
    assert c["side"] == "home" and c["evidence_strength"] in ("MODERATE", "STRONG")
    assert c["headline_type"] == "PROFESSIONAL PRICING INFERENCE" and c["signal_or_pass"] == "SIGNAL"
    assert "NEED" not in c["market_need_signal"].replace("MARKET NEED", "") or "liability" not in c["market_need_signal"]
    assert c["best_observed_executable_price"]["book"] in RETAIL      # retail gives home cheapest


def test_weak_lean_is_observed_disagreement_and_pass():
    h, _ = hist([(T0, books(retail=-3.0, pin=-4.0, bol=-3.0))])        # only Pinnacle differs, one fetch
    c = E.card(h, T0, Convergence())
    assert c["side"] == "home" and c["evidence_strength"] == "WEAK"
    assert c["headline_type"] == "OBSERVED MARKET DISAGREEMENT" and c["signal_or_pass"] == "PASS"


def test_retail_led_market_downgrades_to_pass():
    h, _ = hist([(T0, books()), (T0 + H, books(retail=-3.0, pin=-4.0, bol=-4.0))])
    rows2 = [((1 if i % 2 else -1) * 0.6, 0.0, -(1 if i % 2 else -1) * 0.5) for i in range(40)]
    cv = Convergence()
    for i, r in enumerate(rows2):
        cv.add("nfl", "spread", T0 - 86400 + i, "early", r)
        cv.add("nfl", "spread", T0 - 86400 + i, "late", r)
    c = E.card(h, T0 + H, cv)
    assert c["market_convergence"]["status"] == "RETAIL_LEADS" and c["signal_or_pass"] == "PASS"
    assert "retail-led" in c["pass_reason"]


def test_conflict_and_no_difference_and_insufficient():
    h, _ = hist([(T0, books(retail=-3.0, pin=-4.5, bol=-1.5))])
    assert E.card(h, T0, Convergence())["classification"] == "CONFLICTING EVIDENCE"
    h2, _ = hist([(T0, books(rp=(-105, -105), bp=(-105, -105)))])
    c2 = E.card(h2, T0, Convergence())
    assert c2["classification"] == "NO MATERIAL DIFFERENCE" and c2["signal_or_pass"] == "PASS"
    h3, _ = hist([(T0, books())])
    c3 = E.card(h3, T0 + 5 * 3600, Convergence())
    assert c3["classification"] == "INSUFFICIENT DATA" and c3["quote_freshness"]["status"] == "STALE"


def test_no_lookahead_and_determinism():
    base = [(T0, books()), (T0 + H, books(retail=-3.5))]
    h1, _ = hist(base)
    h2, _ = hist(base + [(T0 + 2 * H, books(retail=-10.0, pin=-1.0, bol=-1.0))])
    assert E.card(h1, T0 + H, Convergence()) == E.card(h2, T0 + H, Convergence())


# ------------------------------------------------------------------------------------------------ freeze + site
def test_frozen_records_write_once_and_never_match_need_files(tmp_path):
    h, _ = hist([(KICK - 2 * H, books()), (KICK - H, books(retail=-3.0, pin=-4.0, bol=-4.0))])
    c = E.card(h, KICK - 1800, Convergence())
    have = {}
    n = SITE.write_once(c, None, str(tmp_path), KICK - 60, have)
    assert n is None                                            # never before kickoff
    n = SITE.write_once(c, None, str(tmp_path), KICK + 600, have)
    assert n and not fnmatch.fnmatch(n, "need_*.json.gz")
    p = tmp_path / SITE.release_tag(c["kickoff"]) / n
    first = p.read_bytes()
    assert SITE.write_once(dict(c, side="away"), None, str(tmp_path), KICK + 700, SITE.existing(str(tmp_path))) is None
    assert p.read_bytes() == first
    rec = json.load(gzip.open(p, "rt"))
    assert rec["written_before_final"] is True and rec["card"]["actual_book_liability"] == "UNKNOWN"
    late = SITE.write_once(dict(c, event_id="ev2"), None, str(tmp_path), KICK + 5 * H, {})
    assert json.load(gzip.open(tmp_path / SITE.release_tag(c["kickoff"]) / late, "rt"))["written_before_final"] is False


def test_validation_grades_prospective_only(tmp_path):
    f = [(KICK - 2 * H, books()), (KICK - H, books(retail=-3.0, pin=-4.0, bol=-4.0)),
         (KICK - 600, books(retail=-3.5, pin=-4.0, bol=-4.0))]
    h, conn = hist(f)
    c = E.card(h, KICK - 1800, Convergence())
    rec = {"written_before_final": True, "card": c}
    v = SITE.validation(conn, [rec, {"written_before_final": False, "card": c}], {("nfl", "ev"): (30, 20)})
    assert v["excluded_late_written"] == 1
    s = v["by_type"][c["signal_or_pass"]]
    assert s["records"] == 1 and s["record"] == "1-0-0"
    assert s["mean_clv_consensus_close"] is not None


def test_site_flags_gate_v7():
    src = open("web_v4/live_site.py").read()
    assert 'os.environ.get("V7_INTEL") == "on"' in src and "v7panel" in src
    assert "ACTUAL BOOK LIABILITY: UNKNOWN" in src
    assert "v7intel_*.json.gz" in open(".github/scripts/site_inputs.sh").read()


def test_site_block_no_data():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE mkt_fetch (fetched_at REAL, sport TEXT, n_quotes INTEGER)")
    c.execute("CREATE TABLE mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price, "
              "market_last_update, fetched_at, source, request_fp)")
    b = SITE.build_block(c, [], T0)
    assert b["status"] == "OK" and b["sports"]["nfl"]["status"] == "NO_DATA" and b["actual_book_liability"] == "UNKNOWN"


def test_site_block_on_synthetic_week(tmp_path):
    f = [(T0 + i * H, books(retail=-3.0 - (0.5 if i > 3 else 0), pin=-4.0, bol=-4.0)) for i in range(6)]
    conn = db(f, kick=T0 + 30 * H)
    b = SITE.build_block(conn, [], T0 + 6 * H, need_out=str(tmp_path))
    cards = b["sports"]["nfl"]["cards"]
    assert len(cards) == 1 and cards[0]["actual_book_liability"] == "UNKNOWN"
    assert b["frozen_written_this_build"] == 0                  # not kicked off yet


def test_config_values_are_the_documented_a_priori_ones():
    assert CONFIG["gap_material_pts"] == 0.5 and CONFIG["pro_hold_pts"] == 0.25 and CONFIG["retail_move_pts"] == 0.5
    assert CONFIG["operators"]["lowvig"] == CONFIG["operators"]["betonlineag"]


def test_from_epoch_kickoff():
    h, _ = hist([(T0, books())])
    assert E.card(h, T0, Convergence())["kickoff"] == from_epoch(KICK)


def test_prospective_evaluator_follows_the_frozen_spec(tmp_path):
    from intel_v7 import prospective as P
    from intel_v7.config import CONFIG_HASH
    assert P.SPEC_HASH_CONFIG == CONFIG_HASH          # a rule change must create a new spec, never silently re-use this one
    f = [(KICK - 2 * H, books()), (KICK - H, books(retail=-3.0, pin=-4.0, bol=-4.0)),
         (KICK - 600, books(retail=-3.5, pin=-4.0, bol=-4.0))]
    h, conn = hist(f)
    c = E.card(h, KICK - 1800, Convergence())
    rows = P.rows_from_records(conn, [{"written_before_final": True, "card": c},
                                      {"written_before_final": False, "card": c}], {("nfl", "ev"): (30, 20)})
    assert len(rows) == 1
    rep = P.evaluate(rows)
    assert rep["signals"] == (1 if c["signal_or_pass"] == "SIGNAL" else 0)
    assert rep["verdict"].startswith("INCONCLUSIVE")
    assert rep["profitable_claim_allowed"] is False
