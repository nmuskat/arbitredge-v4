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


def build(timeline, source="odds_api", market="spreads"):
    """timeline: {minutes_after_T0: {book: (home_line, home_price, away_price)}}"""
    conn = store.connect()
    for minute, books in sorted(timeline.items()):
        t = T0 + 60 * minute
        qs = []
        for book, (line, hp, ap) in books.items():
            a, b = ("home", "away") if market == "spreads" else ("over", "under")
            qs.append(Quote(EID, "americanfootball_nfl", KICK, "H", "A", book, market, a, line, hp, iso(t), iso(t), source, "fp"))
            qs.append(Quote(EID, "americanfootball_nfl", KICK, "H", "A", book, market, b,
                            -line if market == "spreads" else line, ap, iso(t), iso(t), source, "fp"))
        store.ingest(conn, qs, [], iso(t), "americanfootball_nfl", "odds", f"fp{minute}")
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
    tl[60] = {b: (-2.5, -135, 115) for b in ("pinnacle", *REC)}   # juice moved toward home, line unchanged
    r = S.analyse(build(tl), EID, "spreads", T0 + 7200)
    assert r["suspected_side"] == "home"


def test_sharp_vs_rec_disagreement():
    tl = steady([0])
    tl[30] = {"pinnacle": (-3.5, -110, -110), "circasports": (-3.5, -110, -110), **{b: FLAT for b in REC}}
    r = S.analyse(build(tl), EID, "spreads", T0 + 3600)
    assert any("stronger than recreational" in e for e in r["supporting"])


def test_totals_direction_is_over_under():
    tl = steady([0, 30], q=(44.5, -110, -110))
    tl[60] = {b: (46.5, -110, -110) for b in ("pinnacle", *REC)}
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
