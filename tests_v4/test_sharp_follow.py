"""SHARP FOLLOW LIST: simulated sharps' bigger side per spread/total, its tags and graded records."""

from sportsbook_v4 import sharp_follow as SF

_n = [0]


def w(arch, side, stake, market="total", at="2026-11-07T00:00:00Z", bettor=None):
    _n[0] += 1
    return {"wager_id": f"w{_n[0]}", "archetype": arch, "side": side, "stake": float(stake), "market": market,
            "bettor_index": _n[0] if bettor is None else bettor, "accepted_at": at,
            "accepted_line": 40.0, "accepted_price": -110.0}


def rec(side, k, market="total"):
    return [w("REC_CASUAL", side, 100, market) for _ in range(k)]


def test_tags_one_side_split_and_card_labels():
    t = SF.tag(rec("over", 30) + [w("SHARP", "under", s) for s in (900, 800, 700)], "total")
    assert t["side"] == "under" and "ONE_SIDE" in t["tags"] and "SHARP_VS_PUBLIC" in t["tags"]
    t = SF.tag(rec("under", 30) + [w("SHARP", "under", s) for s in (900, 800)] + [w("SHARP", "over", 500)], "total")
    assert t["side"] == "under" and "SPLIT" in t["tags"] and "ALIGNED" in t["tags"]
    assert t["sharp_handle"] == {"over": 500.0, "under": 1700.0} and t["sharp_bettors"] == 3
    assert SF.tag(rec("over", 30), "total") is None                                  # no sharp money
    assert SF.tag([w("SHARP", "over", 100), w("SHARP", "under", 100)], "total") is None   # exact tie


def test_grade_spread_and_total():
    assert SF.grade("spread", "away", 10.0, 24, 17) == "W"        # away +10 loses by 7
    assert SF.grade("spread", "home", -10.0, 27, 17) == "P"
    assert SF.grade("total", "under", 38.5, 24, 10) == "W" and SF.grade("total", "over", 38.5, 24, 10) == "L"
    assert SF.side_line("spread", "away", -3.5) == 3.5 and SF.side_line("total", "under", 44.5) == 44.5


def test_report_splits_before_and_after_registration_and_never_uses_post_kickoff_wagers():
    g1 = {"sport": "cfb", "event_id": "a", "home": "H", "away": "A", "kickoff": "2026-11-01T00:00:00Z"}
    g2 = {"sport": "cfb", "event_id": "b", "home": "H2", "away": "A2", "kickoff": "2026-11-08T00:00:00Z"}
    g3 = {"sport": "cfb", "event_id": "c", "home": "H3", "away": "A3", "kickoff": "2026-11-20T00:00:00Z"}
    early = "2026-10-30T00:00:00Z"
    wg = {("cfb", "a"): [w("SHARP", "under", 500, at=early)] + [w("SHARP", "over", 5000, at="2026-11-02T00:00:00Z")],
          ("cfb", "b"): [w("SHARP", "over", 500, at="2026-11-07T00:00:00Z")],
          ("cfb", "c"): [w("SHARP", "under", 500, at="2026-11-07T00:00:00Z")]}
    res = {("cfb", "a"): {"home_score": 10, "away_score": 7}, ("cfb", "b"): {"home_score": 30, "away_score": 28}}
    out = SF.report({("cfb", k): g for k, g in (("a", g1), ("b", g2), ("c", g3))}, wg, res,
                    lambda s, e, m: 40.0 if e == "a" else 45.0, "2026-11-10T00:00:00Z")
    r = out["records"]["cfb"]["total"]
    assert r["ONE_SIDE"]["before"]["W"] == 1          # game a: under 40 (17 pts) wins; the post-kickoff over bet ignored
    assert r["ONE_SIDE"]["forward"]["W"] == 1         # game b: over 45 (58 pts), after registration
    assert [u["event_id"] for u in out["upcoming"]] == ["c"]
    assert out["registered_at"] == SF.REGISTERED_AT


def test_missing_close_is_counted_not_graded():
    g = {"sport": "nfl", "event_id": "x", "home": "H", "away": "A", "kickoff": "2026-11-08T00:00:00Z"}
    out = SF.report({("nfl", "x"): g}, {("nfl", "x"): [w("SHARP", "over", 100, at="2026-11-07T00:00:00Z")]},
                    {("nfl", "x"): {"home_score": 1, "away_score": 2}}, lambda *a: None, "2026-11-09T00:00:00Z")
    assert out["records"]["nfl"]["total"]["ANY"]["forward"] == {"W": 0, "L": 0, "P": 0, "no_line": 1}
