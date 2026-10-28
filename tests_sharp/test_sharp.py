import time

import pytest

from market_v4 import store
from market_v4.normalize import Quote
from sharp_v4 import signals as S

EID = "e" * 32
T0 = store.iso_to_epoch("2026-10-20T12:00:00Z")
KICK = "2026-10-25T17:00:00Z"


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def build(timeline, source="odds_api", market="spreads", sport="americanfootball_nfl"):
    """timeline: {minutes_after_T0: {book: (home_line, home_price, away_price)}}"""
    conn = store.connect()
    for minute, books in sorted(timeline.items()):
        t = T0 + 60 * minute
        qs = []
        for book, (line, hp, ap) in books.items():
            a, b = ("home", "away") if market == "spreads" else ("over", "under")
            qs.append(Quote(EID, sport, KICK, "H", "A", book, market, a, line, hp, iso(t), iso(t), source, "fp"))
            qs.append(Quote(EID, sport, KICK, "H", "A", book, market, b,
                            -line if market == "spreads" else line, ap, iso(t), iso(t), source, "fp"))
        store.ingest(conn, qs, [], iso(t), sport, "odds", f"fp{minute}")
    return conn


FLAT = (-2.5, -110, -110)
REC = ["draftkings", "fanduel", "betmgm"]


def steady(minutes, books=("pinnacle", *REC), q=FLAT):
    return {m: {b: q for b in books} for m in minutes}


def test_sharp_book_moves_first_and_others_follow_home():
    tl = steady([0, 30])
    tl[60] = {"pinnacle": (-3.5, -110, -110), **{b: FLAT for b in REC}}
    tl[90] = {"pinnacle": (-3.5, -110, -110), **{b: (-3.5, -110, -110) for b in REC}}
    r = S.analyse(build(tl), EID, "spreads", T0 + 3600 * 2)
    assert r["status"] == "SIGNAL" and r["suspected_side"] == "home"
    assert any("moved first" in e and "pinnacle" in e for e in r["supporting"])
    assert any("key number" in e for e in r["supporting"])        # -2.5 -> -3.5 crosses 3
    assert r["detected_at"] == T0 + 3600
    assert 0 < r["score"] < 100 and r["label"].startswith("EXPERIMENTAL")


def test_steam_detected():
    tl = steady([0, 30])
    tl[40] = {b: (-1.5, -110, -110) for b in ("pinnacle", *REC)}       # everyone moves toward away at once
    r = S.analyse(build(tl), EID, "spreads", T0 + 3600)
    assert r["suspected_side"] == "away" and any(e.startswith("steam") for e in r["supporting"])


def test_no_movement_is_insufficient_evidence():
    r = S.analyse(build(steady([0, 30, 60, 90])), EID, "spreads", T0 + 7200)
    assert r["status"] == "INSUFFICIENT EVIDENCE" and r["suspected_side"] is None and r["score"] is None


def test_thin_market_is_insufficient():
    tl = {0: {"draftkings": FLAT}, 30: {"draftkings": (-3.5, -110, -110)}}
    r = S.analyse(build(tl), EID, "spreads", T0 + 3600)
    assert r["status"] == "INSUFFICIENT EVIDENCE" and "book" in r["reason"]


def test_buyback_is_contradicting():
    tl = steady([0])
    tl[30] = {b: (-3.5, -110, -110) for b in ("pinnacle", *REC)}
    tl[60] = {b: (-2.5, -110, -110) for b in ("pinnacle", *REC)}
    r = S.analyse(build(tl), EID, "spreads", T0 + 7200)
    assert any("buyback" in c for c in r["contradicting"])
    assert r["status"] == "INSUFFICIENT EVIDENCE" or r["suspected_side"] is not None


def test_point_in_time_ignores_later_moves():
    tl = steady([0, 30])
    tl[600] = {b: (-6.5, -110, -110) for b in ("pinnacle", *REC)}
    r = S.analyse(build(tl), EID, "spreads", T0 + 60 * 60)
    assert r["status"] == "INSUFFICIENT EVIDENCE"


def test_synthetic_sportsbook_rows_rejected():
    with pytest.raises(S.SyntheticSourceRejected):
        S.analyse(build(steady([0, 30]), source="synthetic_v4"), EID, "spreads", T0 + 3600)


def test_splits_based_signals_always_marked_unavailable():
    r = S.analyse(build(steady([0, 30])), EID, "spreads", T0 + 3600)
    assert any("reverse line movement" in u for u in r["unavailable"])
    assert any("ticket vs money" in u for u in r["unavailable"])


def test_price_only_move_counts_as_movement():
    tl = steady([0, 30])
    tl[40] = {b: (-2.5, -135, 115) for b in ("pinnacle", *REC)}   # juice moved toward home, line unchanged
    r = S.analyse(build(tl), EID, "spreads", T0 + 7200)
    assert r["suspected_side"] == "home"


def test_sharp_vs_rec_disagreement():
    tl = steady([0])
    tl[30] = {"pinnacle": (-3.5, -110, -110), "circasports": (-3.5, -110, -110), **{b: FLAT for b in REC}}
    r = S.analyse(build(tl), EID, "spreads", T0 + 3600)
    assert any("stronger than recreational" in e for e in r["supporting"])


def test_totals_direction_is_over_under():
    tl = steady([0, 30], q=(44.5, -110, -110))
    tl[40] = {b: (46.5, -110, -110) for b in ("pinnacle", *REC)}
    r = S.analyse(build(tl, market="totals"), EID, "totals", T0 + 7200)
    assert r["suspected_side"] == "over"


def test_effectiveness_harness_excludes_unobserved_closes():
    recs = [{"score": 60, "side_beat_close": True, "side_covered": False},
            {"score": 62, "side_beat_close": None, "side_covered": True},
            {"score": 10, "side_beat_close": False, "side_covered": None}]
    t = {r["score_bin"]: r for r in S.effectiveness(recs)}
    assert t["50-74"]["n"] == 2 and t["50-74"]["clv_n"] == 1 and t["50-74"]["unobserved_close"] == 1
    assert t["0-24"]["clv_hit_rate"] == 0.0


def test_recreational_first_mover_is_not_called_sharp():
    tl = steady([0, 30])
    tl[60] = {"draftkings": (-3.5, -110, -110), "pinnacle": FLAT, "fanduel": FLAT, "betmgm": FLAT}
    tl[90] = {b: (-3.5, -110, -110) for b in ("pinnacle", *REC)}
    r = S.analyse(build(tl), EID, "spreads", T0 + 7200)
    texts = r["supporting"] + r["contradicting"]
    assert not any("market-making book(s)" in e and "moved first" in e for e in texts)
    assert any("recreational book(s) draftkings moved first" in e for e in texts)


def test_demo_rows_need_explicit_opt_in_and_are_labelled():
    conn = build(steady([0, 30]), source="demo_sim")
    with pytest.raises(S.SyntheticSourceRejected):
        S.analyse(conn, EID, "spreads", T0 + 3600)
    r = S.analyse(conn, EID, "spreads", T0 + 3600, allow_demo=True)
    assert r["label"].startswith("DEMO DATA")
    with pytest.raises(S.SyntheticSourceRejected):          # the synthetic sportsbook is never evidence
        S.analyse(build(steady([0, 30]), source="synthetic_v4"), EID, "spreads", T0 + 3600, allow_demo=True)


# ---- timing resolution: sparse snapshots (the real collection cadence far from kickoff)

ALL = ("pinnacle", "lowvig", "betonlineag", *REC)


def test_sparse_snapshots_never_claim_steam_or_first_mover():
    # three snapshots 12 h apart; every book moved between them (as in the real 2026-10-28 data)
    tl = {0: {b: FLAT for b in ALL}, 720: {b: (-3.5, -110, -110) for b in ALL},
          1440: {b: (-3.5, -110, -110) for b in ALL}}
    tl[720]["draftkings"] = FLAT                 # one rec book lags a snapshot
    r = S.analyse(build(tl), EID, "spreads", T0 + 1441 * 60)
    texts = r["supporting"] + r["contradicting"]
    assert not any(e.startswith("steam") for e in texts)
    assert not any("moved first" in e for e in texts)
    assert any(u.startswith("steam") for u in r["timing_unresolved"])
    assert any(u.startswith("first mover") for u in r["timing_unresolved"])


def test_dense_snapshots_still_detect_steam():
    tl = {0: {b: FLAT for b in ALL}, 600: {b: FLAT for b in ALL}, 605: {b: (-1.5, -110, -110) for b in ALL}}
    r = S.analyse(build(tl), EID, "spreads", T0 + 610 * 60)
    assert any(e.startswith("steam") for e in r["supporting"]) and r["timing_unresolved"] == []


@pytest.mark.parametrize("sport,expect_move", [("americanfootball_nfl", True), ("americanfootball_ncaaf", False)])
def test_sport_specific_minimum_move(sport, expect_move):
    tl = {0: {b: (-20.5, -110, -110) for b in ALL}, 10: {b: (-21.25, -110, -110) for b in ALL}}   # 0.75 pt
    r = S.analyse(build(tl, sport=sport), EID, "spreads", T0 + 20 * 60)
    assert r["sport"] == sport and r["min_move"] == S.SPORT_THRESHOLDS[sport]["min_move"]
    assert any(e.startswith("consensus moved") for e in r["supporting"] + [r.get("reason", "")]) or not expect_move
    assert (r["status"] == "SIGNAL") == expect_move



def _ml(timeline, sport="americanfootball_nfl"):
    """timeline: {minute: {book: (home_ml, away_ml)}} as moneyline quotes."""
    conn = store.connect()
    for minute, books in sorted(timeline.items()):
        t = T0 + 60 * minute
        qs = []
        for book, (hp, ap) in books.items():
            qs.append(Quote(EID, sport, KICK, "H", "A", book, "h2h", "home", None, hp, iso(t), iso(t), "odds_api", "fp"))
            qs.append(Quote(EID, sport, KICK, "H", "A", book, "h2h", "away", None, ap, iso(t), iso(t), "odds_api", "fp"))
        store.ingest(conn, qs, [], iso(t), sport, "odds", f"fp{minute}")
    return conn


@pytest.mark.parametrize("sport,new_home,expect", [
    ("americanfootball_nfl", (-170, 145), "home"),        # ~ +3 pp toward home: above the NFL 1.5 pp threshold
    ("americanfootball_ncaaf", (-155, 132), None),        # ~ +1.5 pp: below the CFB 2.0 pp threshold
])
def test_moneyline_sharp_evidence_uses_sport_thresholds(sport, new_home, expect):
    tl = {0: {b: (-150, 130) for b in ALL}, 600: {b: (-150, 130) for b in ALL}, 605: {b: new_home for b in ALL}}
    r = S.analyse(_ml(tl, sport), EID, "h2h", T0 + 610 * 60)
    assert r["min_move"] == S.SPORT_THRESHOLDS[sport]["h2h_min_move"]
    assert r["suspected_side"] == expect
