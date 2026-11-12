"""Pinnacle & Circa line-move tracker (analysis_v4/book_moves.py): the inferred-need rule, grading, and the build."""
import time

from analysis_v4 import book_moves as M
from market_v4 import normalize, store

SP = "americanfootball_nfl"


def ser(market, *quotes):
    return [(i, ln, a, b, M._pe(market, ln, a, b)) for i, (ln, a, b) in enumerate(quotes)]


def test_owner_examples_bears():
    # Bears are the home side at +3 (home spread +3)
    assert M.infer("spreads", ser("spreads", (3.0, -110, -110), (3.0, -115, -105)))["need"] == "home"   # juice
    r = M.infer("spreads", ser("spreads", (3.0, -110, -110), (2.5, -115, -105)))                        # number
    assert r["need"] == "home" and r["strength"] == "LINE"
    assert M.infer("spreads", ser("spreads", (3.0, -110, -110), (3.0, -112, -108)))["status"] == "NO CLEAR MOVE"
    # the other way: Bears made cheaper -> the book needs the opponent
    assert M.infer("spreads", ser("spreads", (3.0, -110, -110), (3.5, -110, -110)))["need"] == "away"


def test_totals_direction_and_strength():
    r = M.infer("totals", ser("totals", (44.5, -110, -110), (45.5, -110, -110)))
    assert r["need"] == "over" and r["strength"] == "LINE"
    r = M.infer("totals", ser("totals", (44.5, -110, -110), (44.5, -105, -115)))
    assert r["need"] == "under" and r["strength"] == "JUICE"
    assert M.infer("totals", [])["status"] == "NO PRICES YET"


def test_grade():
    assert M.grade("spreads", "home", 2.5, (20, 22)) == "W"      # home +2.5 loses by 2: covers
    assert M.grade("spreads", "home", 2.5, (20, 23)) == "L"
    assert M.grade("spreads", "away", -3.0, (24, 21)) == "P"
    assert M.grade("totals", "under", 44.5, (21, 20)) == "W"
    assert M.grade("totals", "over", 41.0, (21, 20)) == "P"
    assert M.grade("spreads", None, 3.0, (1, 0)) is None and M.grade("spreads", "home", 3.0, None) is None


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def test_build_tracks_changes_and_grades_completed_games():
    conn = store.connect()
    t0 = store.iso_to_epoch("2026-11-01T12:00:00Z")
    kick = t0 + 10 * 3600
    for i, (pin_home, circ_home) in enumerate([(-110, -110), (-110, -110), (-120, -110), (-125, -110)]):
        t = t0 + i * 3600
        bks = [{"key": b, "last_update": _iso(t), "markets": [
            {"key": "spreads", "last_update": _iso(t), "outcomes": [
                {"name": "Home", "price": hp, "point": -3.0}, {"name": "Away", "price": -100 if hp < -110 else -110,
                                                               "point": 3.0}]}]}
               for b, hp in (("pinnacle", pin_home), ("circasports", circ_home))]
        q, an = normalize.normalize([{"id": "g1", "sport_key": SP, "commence_time": _iso(kick), "home_team": "Home",
                                      "away_team": "Away", "bookmakers": bks}], _iso(t))
        store.ingest(conn, q, an, _iso(t), SP, "odds", "fp")
    games = [{"sport": "nfl", "event_id": "g1", "home": "Home", "away": "Away", "kickoff": kick}]
    up = M.build(conn, games, {}, t0 + 5 * 3600)
    cell = up["games"][0]["books"]["pinnacle"]["spreads"]
    assert up["games"][0]["status"] == "upcoming" and cell["inference"]["need"] == "home"
    assert cell["inference"]["changes"] == 2 and len(cell["moves"]) == 3            # unchanged repeats collapsed
    assert up["games"][0]["books"]["circasports"]["spreads"]["inference"]["status"] == "NO CLEAR MOVE"
    done = M.build(conn, games, {"g1": (27, 20)}, kick + 4 * 3600)
    g = done["games"][0]
    assert g["status"] == "final" and g["books"]["pinnacle"]["spreads"]["result"] == "W"     # home -3 won by 7
    assert g["books"]["pinnacle"]["spreads"]["moves"] == []                                 # first/last only
    rec = {(r["book"], r["strength"]): r for r in done["records"]}
    assert rec[("pinnacle", "ALL")]["W"] == 1 and ("circasports", "ALL") not in rec
    gone = M.build(conn, games, {"g1": (27, 20)}, kick + 30 * 86400)
    assert gone["games"] == [] and gone["records"][0]["W"] == 1                            # still in the record
