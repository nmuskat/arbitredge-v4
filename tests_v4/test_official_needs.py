"""Official ArbitrEdge Needs (experimental, rules official_needs.v1): rules, classification, prospective freezing,
sealing, grading, and isolation from the sportsbook, the Need tracker and C1-C4."""

import ast
import pathlib

import pytest

from official_v5 import rules as OFF
from sportsbook_v4.util import to_epoch

ROOT = pathlib.Path(__file__).resolve().parents[1]
KICK = "2026-11-08T18:00:00Z"
K = to_epoch(KICK)
GAME = {"event_id": "g1", "home": "Home FC", "away": "Away FC", "kickoff": KICK}
OFFER = {"home_spread": -3.5, "home_price": -110, "away_price": -110, "price_book": "fanduel"}
_n = [0]


def _w(arch, side, stake, bettor=None):
    _n[0] += 1
    return {"wager_id": f"w{_n[0]}", "archetype": arch, "side": side, "stake": float(stake), "market": "spread",
            "bettor_index": _n[0] if bettor is None else bettor, "accepted_line": -3.5 if side == "home" else 3.5,
            "accepted_home_spread": -3.5, "accepted_price": -110}


def qualifying_wagers():
    """Recreational money heavily on away, sharps on home: the book needs HOME (it loses most if away covers)."""
    ws = [_w("REC_CASUAL", "away", 40) for _ in range(110)] + [_w("REC_CASUAL", "home", 40) for _ in range(40)]
    ws += [_w("SHARP", "home", 300, bettor=900 + i) for i in range(4)] + [_w("SHARP", "away", 100, bettor=990)]
    return ws


def _eval(ws, *, rank=1, swing_rank=1, stable=True, taken=None, built=None, odds=None, offer=OFFER, window=True):
    taken = taken if taken is not None else K - 3600
    built = built if built is not None else taken + 60
    from sportsbook_v4 import need_tracker as NT
    nd = NT.need_of(ws)
    handles = {f"o{i}": 1e9 for i in range(rank - 1)} | {"g1": 1.0} | {f"x{i}": 0.5 for i in range(8 - rank)}
    swings = {f"o{i}": 1e9 for i in range(swing_rank - 1)} | {"g1": nd.get("swing") or 0} | {f"x{i}": -1 for i in range(8 - swing_rank)}
    ns = {"baseline": nd.get("side"), "variants": 4, "agree": 4 if stable else 3, "label": "STABLE" if stable else "FRAGILE"}
    return OFF.evaluate(game=GAME, spread_wagers=ws, week={"handles": handles, "swings": swings}, need_stability=ns,
                        offer=offer, odds_as_of=odds if odds is not None else taken - 600, taken_at=taken, built_at=built,
                        enforce_window=window)


def test_rules_are_versioned_and_pinned():
    from sportsbook_v4.util import json_hash
    assert OFF.RULES_VERSION == "official_needs.v1" and OFF.RULES_HASH == json_hash(OFF.RULES)
    assert OFF.RULES["rec_opposes"] == {"min_rec_handle_share": 0.65, "min_rec_ticket_share_exclusive": 0.50}
    assert OFF.RULES["sharp_supports"]["min_sharp_handle_share"] == 0.60
    assert OFF.RULES["top_handle"]["top_share"] == OFF.RULES["top_swing"]["top_share"] == 0.25
    assert OFF.RULES_HASH == "d121b6b6b942dc4f9a3ad2834b25a93deb1357747a5c97dd4202f2e74917b487"     # any change needs a new RULES_VERSION


def test_all_six_rules_pass_is_official_with_the_need_side_at_the_market_price():
    ev = _eval(qualifying_wagers())
    assert ev["classification"] == "OFFICIAL", ev["failed"]
    assert ev["pick"]["side"] == "home" and ev["pick"]["team"] == "Home FC" and ev["pick"]["line"] == -3.5
    assert ev["pick"]["price"] == -110 and ev["pick"]["price_book"] == "fanduel"
    assert [r["id"] for r in ev["rules"]] == list(OFF.ORDER) and all(r["pass"] for r in ev["rules"])


@pytest.mark.parametrize("kw,failing", [
    ({"stable": False}, "stable_need"),
    ({"rank": 3}, "top_handle"),          # 8 games -> top 2
    ({"swing_rank": 3}, "top_swing"),
    ({"odds": "old"}, "data_valid"),
    ({"offer": None}, "data_valid"),
])
def test_each_rule_can_fail_on_its_own(kw, failing):
    if kw.get("odds") == "old":
        kw = {"odds": K - 3600 - 4 * 3600}
    ev = _eval(qualifying_wagers(), **kw)
    assert [r["id"] for r in ev["rules"] if not r["pass"]] == [failing]
    assert ev["classification"] != "OFFICIAL"


def test_recreational_ticket_majority_is_required_as_well_as_65pct_handle():
    ws = qualifying_wagers()
    ws = [w for w in ws if not (w["archetype"] == "REC_CASUAL" and w["side"] == "away")]
    ws += [_w("REC_CASUAL", "away", 400) for _ in range(30)]           # 75% of rec handle but only 43% of tickets
    ws += [_w("REC_CASUAL", "home", 10) for _ in range(80)]
    ev = _eval(ws)
    r = next(x for x in ev["rules"] if x["id"] == "rec_opposes")
    assert not r["pass"] and ev["classification"] != "OFFICIAL"


@pytest.mark.parametrize("sharps,why", [
    ([("home", 3000, 900)], "sharp wagers"),                                    # one big sharp ticket is never enough
    ([("home", 1000, 900)] * 3, "sharp customers"),                             # one customer split into 3 tickets
    ([("home", 3000, 900), ("home", 100, 901), ("home", 100, 902)], "one sharp customer"),
])
def test_sharp_sample_safeguards(sharps, why):
    ws = [w for w in qualifying_wagers() if w["archetype"] != "SHARP"] + [_w("SHARP", s, k, b) for s, k, b in sharps]
    r = next(x for x in _eval(ws)["rules"] if x["id"] == "sharp_supports")
    assert not r["pass"] and why in r["value"]


def test_adequate_independent_bettors_and_concentration():
    few = [_w("REC_CASUAL", "away", 40) for _ in range(60)] + [_w("SHARP", "home", 300, 900 + i) for i in range(4)]
    assert "distinct spread bettors" in next(x for x in _eval(few)["rules"] if x["id"] == "data_valid")["value"]
    whale = qualifying_wagers() + [_w("HIGH_STAKES_REC", "away", 5000)]
    assert "largest bettor" in next(x for x in _eval(whale)["rules"] if x["id"] == "data_valid")["value"]


def test_watchlist_and_pass():
    ev = _eval(qualifying_wagers(), rank=3)            # five of six: stable + valid + 3 of the other 4
    assert ev["classification"] == "WATCHLIST"
    ev = _eval(qualifying_wagers(), rank=3, swing_rank=3)
    assert ev["classification"] == "PASS"               # only 2 of the other 4
    ev = _eval(qualifying_wagers(), stable=False, rank=3)
    assert ev["classification"] == "PASS"               # watchlist needs a stable Need
    no_need = [_w("REC_CASUAL", "home", 50) for _ in range(60)] + [_w("REC_CASUAL", "away", 50) for _ in range(60)]
    assert _eval(no_need)["classification"] == "PASS"


def test_more_than_24h_out_is_never_official_unless_only_displayed():
    early = K - 30 * 3600
    assert _eval(qualifying_wagers(), taken=early)["classification"] != "OFFICIAL"
    assert _eval(qualifying_wagers(), taken=early, window=False)["classification"] == "OFFICIAL"   # display only


def test_records_are_prospective_only(monkeypatch):
    run = {"run_id": "r", "engine_version": "sportsbook_v5.r2", "params_hash": "p"}
    ev = _eval(qualifying_wagers())
    with pytest.raises(ValueError, match="before kickoff"):
        OFF.snapshot(sport="nfl", run=run, game=GAME, evaluation=ev, taken_at=K + 10, built_at=K + 20)
    with pytest.raises(ValueError, match="before kickoff"):
        OFF.snapshot(sport="nfl", run=run, game=GAME, evaluation=ev, taken_at=K - 100, built_at=K + 5)  # built late
    with pytest.raises(ValueError, match="never retroactive"):
        OFF.snapshot(sport="nfl", run=run, game=GAME, evaluation=ev, taken_at=to_epoch(OFF.REGISTERED_AT) - 60,
                     built_at=K - 60)
    rec = OFF.snapshot(sport="nfl", run=run, game=GAME, evaluation=ev, taken_at=K - 3600, built_at=K - 3500)
    assert rec["format"] == "official_needs.v1" and rec["rules_hash"] == OFF.RULES_HASH and rec["record_hash"]
    assert rec["engine_version"] == "sportsbook_v5.r2" and rec["pick"]["price"] == -110


def _rec(cls_wagers, taken, built):
    run = {"run_id": "r", "engine_version": "sportsbook_v5.r2", "params_hash": "p"}
    return OFF.snapshot(sport="nfl", run=run, game=GAME, evaluation=_eval(cls_wagers, taken=taken, built=built),
                        taken_at=taken, built_at=built)


def test_seal_freezes_the_last_pre_kickoff_record_once_and_never_without_one(tmp_path):
    from sportsbook_v4 import need_tracker as NT
    a = _rec(qualifying_wagers(), K - 7200, K - 7100)
    b = _rec(qualifying_wagers()[:100], K - 1800, K - 1700)            # later record: the one that counts
    assert OFF.seal([a, b], [], K + 60) == []                           # inside the grace period
    d = OFF.seal([a, b], [], K + OFF.SEAL_GRACE_S + 60)
    assert len(d) == 1 and d[0]["snapshot"]["record_hash"] == b["record_hash"] and d[0]["classification"] == b["classification"]
    assert OFF.seal([a, b], d, K + 99999) == []                        # sealed once
    assert OFF.seal([], [], K + 99999) == []                           # no record -> no selection, ever
    NT.write(str(tmp_path), OFF.decisions_name(K + 2000), d)
    assert NT.load(str(tmp_path), OFF.DEC_PREFIX + "*.json.gz") == d   # hash-verified round trip
    other = dict(d[0], snapshot=a, sealed_at="2099-01-01T00:00:00Z")
    with pytest.raises(NT.TrackerIntegrityError):
        OFF.unique(d + [other])


def test_grading_at_the_frozen_line_and_price():
    d = OFF.seal([_rec(qualifying_wagers(), K - 1800, K - 1700)], [], K + 99999)[0]
    assert d["classification"] == "OFFICIAL"
    win = OFF.grade(d, {"home_score": 27, "away_score": 20})            # home -3.5 covers
    loss = OFF.grade(d, {"home_score": 21, "away_score": 20})
    pending = OFF.grade(d, None)
    assert (win["ats"], win["units"]) == ("W", pytest.approx(0.9091, abs=1e-4)) and (loss["ats"], loss["units"]) == ("L", -1.0)
    assert pending["status"] == "AWAITING_RESULT"
    rep = OFF.report([d], {("nfl", "g1"): {"home_score": 27, "away_score": 20}})
    assert rep["official"]["by_sport"]["nfl"]["wins"] == 1 and rep["official"]["by_sport"]["cfb"]["selections"] == 0
    assert rep["watchlist"]["overall"]["selections"] == 0


def test_zero_qualifying_games_report_zero():
    rep = OFF.report([], {})
    assert rep["official"]["overall"]["selections"] == 0 and rep["official"]["overall"]["roi_pct"] is None


def test_official_code_changes_nothing_it_reads():
    tree = ast.parse((ROOT / "official_v5" / "rules.py").read_text())
    for n in ast.walk(tree):          # no assignment into imported modules (e.g. NT.QUALIFY = ...)
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                assert not (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in ("NT",)), ast.dump(t)
    for f in (ROOT / "sportsbook_v4").glob("*.py"):
        if f.name not in ("live_book.py", "seal_run.py"):      # the build and the collection-job seal step only
            assert "official_v5" not in f.read_text(), f.name     # no engine/Need/cohort code reads it
    from sportsbook_v4 import cohorts as C
    assert "official" not in str(C.COHORTS).lower()


# ------------------------------------------------------------------ through real builds (freeze -> seal), prospective only

def _site(tmp_path, T, registered, monkeypatch, need_in=None):
    from sportsbook_v4 import live_book as LB
    from tests_v4.test_live_book import T0, build_store, new_book, poll
    from web_v4 import live_site
    monkeypatch.setattr(OFF, "REGISTERED_AT", registered)
    live = build_store(end=T)
    out = LB.build(new_book(), live, T, [poll(T0 - 86400)], bettors=500, variants={},
                   book_in=str(tmp_path / "book"), book_out=str(tmp_path / "book"))
    snap = {"sports": {sp: {"book": out[sp]} for sp in ("nfl", "cfb")}}
    need_out = tmp_path / "need"
    live_site.official_step(snap, [poll(T0 - 86400)], T, str(need_in or need_out), None, str(need_out))
    return snap


def test_builds_freeze_records_in_the_window_and_seal_the_last_one_after_kickoff(tmp_path, monkeypatch):
    from sportsbook_v4 import need_tracker as NT
    from tests_v4.test_live_book import KICK, T0
    t1 = KICK["n1"] - 3 * 3600
    s1 = _site(tmp_path, t1, "2026-10-01T00:00:00Z", monkeypatch)
    g = {x["event_id"]: x for x in s1["sports"]["nfl"]["book"]["official"]["games"]}
    n1 = next(v for k, v in g.items() if v["home"] == "Kansas City Chiefs")
    assert n1["frozen"] and n1["status"] in ("OFFICIAL", "WATCHLIST", "PASS")
    assert all(x["status"] == "EARLY" and not x["frozen"] for x in g.values() if x is not n1)   # > 24 h away
    assert s1["official_needs"]["records_this_build"] >= 1 and s1["official_needs"]["sealed_this_build"] == 0
    recs = NT.load(str(tmp_path / "need"), OFF.SNAP_PREFIX + "*.json.gz")
    assert recs and all(r["format"] == "official_needs.v1" and r["engine_version"].startswith("sportsbook_v5") for r in recs)
    t2 = KICK["n1"] + OFF.SEAL_GRACE_S + 600
    s2 = _site(tmp_path, t2, "2026-10-01T00:00:00Z", monkeypatch)
    dec = NT.load(str(tmp_path / "need"), OFF.DEC_PREFIX + "*.json.gz")
    n1rec = [r for r in recs if r["home"] == "Kansas City Chiefs"]
    assert len(dec) == 1 and dec[0]["snapshot"]["record_hash"] == max(n1rec, key=lambda r: r["built_at"])["record_hash"]
    assert s2["official_needs"]["sealed_total"] == 1
    assert not NT.load(str(tmp_path / "need"), "decisions_*.json.gz")      # the Need tracker's own files are untouched


def test_builds_before_registration_freeze_nothing(tmp_path, monkeypatch):
    from sportsbook_v4 import need_tracker as NT
    from tests_v4.test_live_book import KICK
    t1 = KICK["n1"] - 3 * 3600
    s = _site(tmp_path, t1, "2030-01-01T00:00:00Z", monkeypatch)
    assert s["official_needs"]["records_this_build"] == 0
    assert not any(x["frozen"] for x in s["sports"]["nfl"]["book"]["official"]["games"])
    assert NT.load(str(tmp_path / "need"), OFF.SNAP_PREFIX + "*.json.gz") == []


# ------------------------------------------------------------------ owner's pre-deployment sealing checklist (2026-10-31)

def _manual(rec, **kw):
    """A record as if written by a build (re-hashed), for situations a real build refuses to create."""
    from sportsbook_v4.util import json_hash
    r = {k: v for k, v in rec.items() if k != "record_hash"}
    r.update(kw)
    r["record_hash"] = json_hash(r)
    return r


def test_1_records_are_immutable_timestamped_and_tamper_evident(tmp_path):
    from sportsbook_v4 import need_tracker as NT
    rec = _rec(qualifying_wagers(), K - 1800, K - 1700)
    assert rec["taken_at"] == "2026-11-08T17:30:00Z" and rec["built_at"] == "2026-11-08T17:31:40Z" and rec["record_hash"]
    NT.write(str(tmp_path), OFF.snapshot_name("nfl", K - 1700), [rec])
    with pytest.raises(NT.TrackerIntegrityError, match="never overwritten"):
        NT.write(str(tmp_path), OFF.snapshot_name("nfl", K - 1700), [rec])
    import gzip
    import json
    p = next(tmp_path.glob("official_snap_*"))
    doc = json.load(gzip.open(p, "rt"))
    doc["records"][0]["pick"]["line"] = -1.5                       # edited after the fact
    json.dump(doc, gzip.open(p, "wt"))
    with pytest.raises(NT.TrackerIntegrityError):
        NT.load(str(tmp_path), OFF.SNAP_PREFIX + "*.json.gz")


def test_2_3_only_the_final_evaluation_decides_and_an_earlier_official_is_dropped():
    early = _rec(qualifying_wagers(), K - 9000, K - 8900)          # OFFICIAL 2.5 h before
    assert early["classification"] == "OFFICIAL"
    final_pass = _rec(qualifying_wagers(), K - 1800, K - 1700)
    final_pass = _manual(final_pass, classification="PASS")        # the final evaluation no longer qualifies
    d = OFF.seal([early, final_pass], [], K + 99999)[0]
    assert d["classification"] == "PASS" and d["snapshot"]["record_hash"] == final_pass["record_hash"]
    assert OFF.report([d], {})["official"]["overall"]["selections"] == 0


def test_4_a_final_official_keeps_the_exact_side_spread_odds_book_model_and_support():
    final = _rec(qualifying_wagers(), K - 1800, K - 1700)
    d = OFF.seal([final], [], K + 99999)[0]
    assert d["classification"] == "OFFICIAL" and d["data_quality"] == "VALID" and d["snapshot"] == final
    pk = d["snapshot"]["pick"]
    assert (pk["side"], pk["line"], pk["price"], pk["price_book"]) == ("home", -3.5, -110, "fanduel")
    assert d["snapshot"]["engine_version"] == "sportsbook_v5.r2" and d["snapshot"]["format"] == "official_needs.v1"
    assert d["snapshot"]["rules_hash"] == OFF.RULES_HASH and d["snapshot"]["supporting"]["pressure"]["bettors"] >= 100
    g = OFF.grade(d, {"home_score": 24, "away_score": 20})
    assert (g["line"], g["price"], g["price_book"], g["ats"]) == (-3.5, -110, "fanduel", "W")


def test_5_stale_or_missing_final_data_never_reuses_an_old_official():
    old = _rec(qualifying_wagers(), K - 4 * 3600, K - 4 * 3600 + 60)   # OFFICIAL, but 4 h before kickoff
    assert old["classification"] == "OFFICIAL"
    d = OFF.seal([old], [], K + 99999)[0]
    assert d["classification"] == "NO_FINAL_EVALUATION" and "is not used" in d["data_quality"]
    stale = _rec(qualifying_wagers(), K - 1800, K - 1700)
    stale_ev = _eval(qualifying_wagers(), taken=K - 1800, built=K - 1700, odds=K - 1800 - 4 * 3600)
    stale = _manual(stale, classification=stale_ev["classification"], rules=stale_ev["rules"])
    d2 = OFF.seal([stale], [], K + 99999)[0]
    assert d2["classification"] == "PASS" and d2["data_quality"].startswith("INVALID: odds older than 3 h")
    rep = OFF.report([d, d2], {})
    assert rep["official"]["overall"]["selections"] == 0 and len(rep["data_quality_issues"]) == 2


def test_6_windows_for_saturday_oct_31_and_sunday_nov_1_including_the_dst_change():
    reg = to_epoch(OFF.REGISTERED_AT)
    assert OFF.REGISTERED_AT == "2026-10-31T04:00:00Z"                     # midnight ET, before every Saturday kickoff
    run = {"run_id": "r", "engine_version": "sportsbook_v5.r2", "params_hash": "p"}
    cfb = dict(GAME, kickoff="2026-10-31T16:00:00Z")                        # first Top-25 kickoffs, 12:00 ET
    nfl = dict(GAME, kickoff="2026-11-01T18:00:00Z")                        # 1:00 pm EST (DST ends 06:00Z Nov 1)
    ev = lambda g, t: OFF.evaluate(game=g, spread_wagers=qualifying_wagers(), week=_week(), need_stability=_ns(),
                                   offer=OFFER, odds_as_of=t - 600, taken_at=t, built_at=t + 60)
    t = reg + 3600                                                          # 05:00Z Saturday
    assert ev(cfb, t)["classification"] == "OFFICIAL"                       # 11 h before the CFB kickoff: in window
    assert OFF.snapshot(sport="cfb", run=run, game=cfb, evaluation=ev(cfb, t), taken_at=t, built_at=t + 60)
    assert "more than 24 h" in [r for r in ev(nfl, t)["rules"] if r["id"] == "data_valid"][0]["value"]
    with pytest.raises(ValueError, match="never retroactive"):
        OFF.snapshot(sport="cfb", run=run, game=cfb, evaluation=ev(cfb, reg - 60), taken_at=reg - 60, built_at=reg)
    t2 = to_epoch("2026-11-01T05:30:00Z")                                   # 30 min before the DST change
    rec = OFF.snapshot(sport="nfl", run=run, game=nfl, evaluation=ev(nfl, t2), taken_at=t2, built_at=t2 + 60)
    assert rec["minutes_before_kickoff"] == 750 and rec["classification"] == "OFFICIAL"   # UTC arithmetic, no DST jump
    t3 = to_epoch("2026-11-01T17:30:00Z")
    late = OFF.snapshot(sport="nfl", run=run, game=nfl, evaluation=ev(nfl, t3), taken_at=t3, built_at=t3 + 60)
    assert OFF.seal([rec, late], [], to_epoch("2026-11-01T18:30:00Z"))[0]["snapshot"]["record_hash"] == late["record_hash"]


def _week():
    from sportsbook_v4 import need_tracker as NT
    sw = NT.need_of(qualifying_wagers())["swing"]
    return {"handles": {"g1": 1e9, "a": 1.0, "b": 1.0, "c": 1.0}, "swings": {"g1": sw, "a": -1, "b": -1, "c": -1}}


def _ns():
    return {"baseline": "home", "variants": 4, "agree": 4, "label": "STABLE"}


def test_7_nothing_is_promoted_or_altered_after_kickoff():
    run = {"run_id": "r", "engine_version": "sportsbook_v5.r2", "params_hash": "p"}
    ev = _eval(qualifying_wagers())
    with pytest.raises(ValueError):
        OFF.snapshot(sport="nfl", run=run, game=GAME, evaluation=ev, taken_at=K - 60, built_at=K + 60)
    final_pass = _manual(_rec(qualifying_wagers(), K - 1800, K - 1700), classification="PASS")
    after = _manual(_rec(qualifying_wagers(), K - 1700, K - 1600), built_at="2026-11-08T18:05:00Z")   # built after kickoff
    d = OFF.seal([final_pass, after], [], K + 99999)
    assert d[0]["classification"] == "PASS" and d[0]["snapshot"]["record_hash"] == final_pass["record_hash"]
    better = _rec(qualifying_wagers(), K - 600, K - 500)                 # appears later (e.g. a delayed upload)
    assert OFF.seal([final_pass, better], d, K + 999999) == []           # already sealed: never re-sealed


def test_8_a_selection_is_never_recorded_or_graded_twice(tmp_path):
    from sportsbook_v4 import need_tracker as NT
    d = OFF.seal([_rec(qualifying_wagers(), K - 1800, K - 1700)], [], K + 99999)
    NT.write(str(tmp_path), "official_dec_20261108T183000Z.json.gz", d)          # sealed by the collection job
    second = {k: v for k, v in d[0].items() if k != "record_hash"}            # the same seal again, by a later build
    second["sealed_at"] = "2026-11-08T19:00:00Z"
    from sportsbook_v4.util import json_hash
    second["record_hash"] = json_hash(second)
    NT.write(str(tmp_path), "official_dec_20261108T190000Z.json.gz", [second])
    loaded = OFF.unique(NT.load(str(tmp_path), OFF.DEC_PREFIX + "*.json.gz"))
    assert len(loaded) == 1
    rep = OFF.report(loaded, {("nfl", "g1"): {"home_score": 24, "away_score": 20}})
    assert rep["official"]["overall"]["selections"] == 1 and rep["official"]["overall"]["graded"] == 1


def test_9_the_collection_job_seals_without_a_dashboard_build_and_builds_then_add_nothing(tmp_path):
    from sportsbook_v4 import need_tracker as NT, seal_run
    need = tmp_path / "need" / "v4-need-2026-W45"
    NT.write(str(need), OFF.snapshot_name("nfl", K - 1700), [_rec(qualifying_wagers(), K - 1800, K - 1700)])
    new, path = seal_run.seal_official(str(tmp_path / "need"), str(tmp_path / "out"), K + OFF.SEAL_GRACE_S + 60)
    assert len(new) == 1 and path.endswith(".json.gz") and "official_dec_" in path
    import shutil
    shutil.copy(path, tmp_path / "need")
    again, _ = seal_run.seal_official(str(tmp_path / "need"), str(tmp_path / "out3"), K + 99999)
    assert again == []                                                   # the build/collect job never seals it twice
    assert OFF.seal(NT.load(str(tmp_path / "need"), OFF.SNAP_PREFIX + "*.json.gz"),
                    OFF.unique(NT.load(str(tmp_path / "need"), OFF.DEC_PREFIX + "*.json.gz")), K + 99999) == []
    script = open(ROOT / ".github" / "scripts" / "seal_step.sh").read()
    assert "official_dec_*.json.gz" in script and "--clobber" not in script
