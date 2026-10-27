"""Engine A on real-format collected quotes (sportsbook_v4.live_book): MODELED splits, Need from liability,
CFB Top-25 gating, collection-gap safety, persistence and auditability."""
import sqlite3
import time

import pytest

from market_v4 import normalize, store
from sportsbook_v4 import live_book as LB
from sportsbook_v4.ledger_read import fetch_wagers
from sportsbook_v4.schema import ensure_schema
from sportsbook_v4.settlement import book_need_all
from sportsbook_v4.util import json_hash, to_epoch

T0 = store.iso_to_epoch("2026-10-27T12:00:00Z")
NFL = [("n1", "Kansas City Chiefs", "Denver Broncos", -7.5, 47.5, (-380, 300)),
       ("n2", "Buffalo Bills", "Miami Dolphins", -3.0, 44.5, (-165, 140)),
       ("n3", "Chicago Bears", "Detroit Lions", 2.5, 49.5, (120, -140))]
CFB = [("c1", "Alabama Crimson Tide", "LSU Tigers", -10.5, 55.5, (-450, 340)),        # 1 vs unranked
       ("c2", "Ohio State Buckeyes", "Penn State Nittany Lions", -3.5, 47.5, (-170, 145)),  # 2 vs 9
       ("c3", "Kansas Jayhawks", "Iowa State Cyclones", 1.5, 52.5, (105, -125)),     # unranked vs unranked
       ("c4", "Texas Longhorns", "Florida Gators", -14.0, 50.5, (-700, 480))]        # 4 vs unranked
BOOKS = ("pinnacle", "circasports", "lowvig", "betonlineag", "draftkings", "fanduel", "betmgm")
KICK = {"n1": T0 + 4 * 86400, "n2": T0 + 5 * 86400, "n3": T0 + 5 * 86400 + 3600,
        "c1": T0 + 4 * 86400 + 7200, "c2": T0 + 4 * 86400 + 9000, "c3": T0 + 4 * 86400, "c4": T0 + 4 * 86400 + 5000}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _eid(k):
    return (k * 16)[:32]


def payload(t, games, sport, drift):
    out = []
    for k, home, away, sp, tot, ml in games:
        books = []
        for i, b in enumerate(BOOKS):
            j = (i % 3 - 1) * 5
            line = sp - drift
            books.append({"key": b, "last_update": iso(t), "markets": [
                {"key": "spreads", "outcomes": [{"name": home, "price": -110 + j, "point": line},
                                                {"name": away, "price": -110 - j, "point": -line}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": -110 - j, "point": tot},
                                               {"name": "Under", "price": -110 + j, "point": tot}]},
                {"key": "h2h", "outcomes": [{"name": home, "price": ml[0]}, {"name": away, "price": ml[1]}]}]})
        out.append({"id": _eid(k), "sport_key": sport, "commence_time": iso(KICK[k]), "home_team": home,
                    "away_team": away, "bookmakers": books})
    return out


def build_store(gap=None, start=T0 - 3 * 86400, end=T0, step=3 * 3600):
    """Hourly-ish snapshots of every game; `gap` = (lo, hi) epoch window with NO collection."""
    conn = store.connect()
    t, n = start, 0
    while t <= end:
        if not (gap and gap[0] <= t < gap[1]):
            for sport, games in (("americanfootball_nfl", NFL), ("americanfootball_ncaaf", CFB)):
                q, an = normalize.normalize(payload(t, games, sport, 0.5 * (n % 2)), iso(t))
                store.ingest(conn, q, an, iso(t), sport, "odds", "fp")
        t += step
        n += 1
    return conn


def poll(fetched_at, week=9):
    teams = [("Alabama", "Crimson Tide"), ("Ohio State", "Buckeyes"), ("Penn State", "Nittany Lions"),
             ("Texas", "Longhorns"), ("LSU", "Tigers"), ("Kansas", "Jayhawks"), ("Kansas State", "Wildcats"),
             ("Iowa State", "Cyclones"), ("Florida", "Gators")]
    return {"source": "CollegeFootballData API", "fetched_at": fetched_at, "year": 2026,
            "polls": [{"season": 2026, "season_type": "regular", "week": week,
                       "ranks": [{"rank": 1, "school": "Alabama"}, {"rank": 2, "school": "Ohio State"},
                                 {"rank": 4, "school": "Texas"}, {"rank": 9, "school": "Penn State"}]}],
            "teams": [{"school": s, "mascot": m, "alternate_names": []} for s, m in teams]}


def new_book():
    b = sqlite3.connect(":memory:")
    ensure_schema(b)
    return b


@pytest.fixture(scope="module")
def built():
    live = build_store()
    book = new_book()
    out = LB.build(book, live, T0, [poll(T0 - 86400)], bettors=3000)
    return live, book, out


def test_nfl_all_games_cfb_top25_only(built):
    _, _, out = built
    assert out["nfl"]["coverage"]["included"] == 3 and out["nfl"]["status"] == "OK"
    cfb = out["cfb"]
    ids = {g["event_id"] for g in cfb["games"]}
    assert ids == {_eid("c1"), _eid("c2"), _eid("c4")}          # unranked-vs-unranked c3 excluded
    assert cfb["coverage"]["excluded"] == {"unranked": 1, "rank_unavailable": 0}
    g = next(g for g in cfb["games"] if g["event_id"] == _eid("c2"))
    assert (g["rankings"]["home_rank"], g["rankings"]["away_rank"]) == (2, 9)
    assert g["rankings"]["source"] == "CollegeFootballData API" and g["rankings"]["fetched_at"] == T0 - 86400


def test_separate_persistent_populations(built):
    _, _, out = built
    a, b = out["nfl"]["audit"], out["cfb"]["audit"]
    assert a["population_id"].startswith("nfl|") and b["population_id"].startswith("cfb|")
    assert a["population_hash"] != b["population_hash"]
    # a later build (more data, later T) uses the SAME bettors
    live2 = build_store(end=T0 + 6 * 3600)
    out2 = LB.build(new_book(), live2, T0 + 6 * 3600, [poll(T0 - 86400)], bettors=3000, variants={})
    assert out2["nfl"]["audit"]["population_hash"] == a["population_hash"]
    assert out2["cfb"]["audit"]["population_hash"] == b["population_hash"]


def test_splits_are_modeled_and_sum_to_100(built):
    _, _, out = built
    for sport in ("nfl", "cfb"):
        assert out[sport]["label"] == "MODELED"
        for g in out[sport]["games"]:
            assert g["label"] == "MODELED"
            for m, row in g["splits"].items():
                if row["status"] != "OK":
                    continue
                assert row["label"] == "MODELED"
                sides = row["sides"].values()
                assert abs(sum(s["ticket_pct"] for s in sides) - 100) <= 0.2
                assert abs(sum(s["handle_pct"] for s in sides) - 100) <= 0.2


def test_tickets_from_counts_handle_from_stakes(built):
    live, book, out = built
    run_id = out["nfl"]["audit"]["run_id"]
    diverged = 0
    for g in out["nfl"]["games"]:
        ws = fetch_wagers(book, run_id, g["event_id"], market="spread")
        row = g["splits"]["spread"]
        if row["status"] != "OK":
            continue
        home = [w for w in ws if w["side"] == "home"]
        assert row["sides"]["home"]["tickets"] == len(home)
        assert row["sides"]["home"]["handle"] == pytest.approx(sum(w["stake"] for w in home), abs=0.01)
        diverged += abs(row["handle_minus_tickets_pct"]) > 1.0
    assert diverged >= 1          # stakes differ by side, so handle % is not ticket % by construction


def test_splits_unit_tickets_vs_handle():
    ws = [{"market": "spread", "side": "home", "stake": 20.0}] * 8 + [{"market": "spread", "side": "away", "stake": 500.0}] * 2
    s = LB.splits(ws)["spread"]
    assert s["sides"]["home"]["ticket_pct"] == 80.0 and s["sides"]["home"]["handle_pct"] == pytest.approx(13.8, abs=0.1)
    assert s["sample"] == "LOW" and LB.splits([])["spread"]["status"] == "NO_WAGERS"


def test_need_is_liability_not_fade_the_public():
    # 80% of tickets on home, but the away handle is larger: the book's liability is on AWAY,
    # so it needs HOME to cover. Fading the public would say "away".
    w = [{"market": "spread", "side": "home", "accepted_line": -3.0, "accepted_home_spread": -3.0,
          "accepted_price": -110, "stake": 20.0}] * 8 \
        + [{"market": "spread", "side": "away", "accepted_line": 3.0, "accepted_home_spread": -3.0,
             "accepted_price": -110, "stake": 500.0}] * 2
    need = book_need_all(w, ref_home_spread=-3.0, ref_total=None, max_score=55)
    assert need["spread"]["financial_need_side"] == "home"
    summ = LB._need_summary(need)
    assert summ["spread"]["financial_need_side"] == "home"
    assert summ["spread"]["pnl_if_home_covers_all"] > summ["spread"]["pnl_if_away_covers_all"]


def test_need_matches_book_pnl(built):
    _, _, out = built
    for g in out["nfl"]["games"] + out["cfb"]["games"]:
        sp = (g["need"] or {}).get("spread")
        if not sp or sp["status"] != "DIRECTIONAL":
            continue
        better = "home" if sp["pnl_if_home_covers_all"] > sp["pnl_if_away_covers_all"] else "away"
        assert sp["financial_need_side"] == better


def test_cfb_unavailable_without_rankings():
    out = LB.build(new_book(), build_store(), T0, None, bettors=500, variants={})
    assert out["cfb"]["status"] == "UNAVAILABLE" and out["cfb"]["games"] == [] and out["cfb"]["audit"] is None
    assert "not connected" in out["cfb"]["coverage"]["reason"]
    assert out["nfl"]["status"] == "OK"


def test_poll_fetched_after_now_is_not_used():
    # the only poll was fetched after every kickoff: rank unavailable, no CFB game is guessed in
    out = LB.build(new_book(), build_store(), T0, [poll(T0 + 30 * 86400)], bettors=500, variants={})
    assert out["cfb"]["games"] == []
    assert out["cfb"]["coverage"]["excluded"]["rank_unavailable"] == 4


def test_collection_gap_means_no_wagers(built):
    gap = (T0 - 2 * 86400, T0 - 86400)                 # 24h without any collection
    live = build_store(gap=gap)
    book = new_book()
    out = LB.build(book, live, T0, [poll(T0 - 86400)], bettors=3000, variants={})
    for sport in ("nfl", "cfb"):
        ws = fetch_wagers(book, out[sport]["audit"]["run_id"], market=None)
        assert ws
        for w in ws:
            t, offer = to_epoch(w["accepted_at"]), to_epoch(w["offer_as_of"])
            assert offer <= t
            # a price is only ever offered from a quote no older than 6 h (policy pinned here, not read
            # from the module, so loosening the module constant fails this test)
            assert not (gap[0] + 6 * 3600 + 600 < t < gap[1]), w


def test_quote_age_policy_is_six_hours():
    assert LB.LIVE_MAX_AGE == 6 * 3600


def test_no_wagers_before_first_quote_or_after_now(built):
    live, book, out = built
    first = live.execute("SELECT MIN(fetched_at) FROM mkt_quote").fetchone()[0]
    for sport in ("nfl", "cfb"):
        for w in fetch_wagers(book, out[sport]["audit"]["run_id"], market=None):
            assert first <= to_epoch(w["accepted_at"]) <= T0


def test_audit_ledger_hash_and_determinism(built):
    live, book, out = built
    a = out["nfl"]["audit"]
    ws = fetch_wagers(book, a["run_id"], market=None)
    assert a["wagers"] == len(ws)
    assert a["ledger_hash"] == json_hash([[w["wager_id"], w["market"], w["side"], w["accepted_line"],
                                           w["accepted_price"], w["stake"], w["accepted_at"]] for w in ws])
    again = LB.build(new_book(), live, T0, [poll(T0 - 86400)], bettors=3000, variants={})
    assert again["nfl"]["audit"]["ledger_hash"] == a["ledger_hash"]
    assert again["cfb"]["audit"]["ledger_hash"] == out["cfb"]["audit"]["ledger_hash"]


def test_sensitivity_bands_contain_baseline(built):
    _, _, out = built
    assert out["nfl"]["sensitivity"]["variants"] == sorted(LB.SENSITIVITY_VARIANTS)
    n = 0
    for g in out["nfl"]["games"]:
        for m, sides in g["bands"].items():
            for s, b in sides.items():
                base = g["splits"][m]["sides"][s]
                assert b["ticket_pct"][0] <= base["ticket_pct"] <= b["ticket_pct"][1]
                assert b["handle_pct"][0] <= base["handle_pct"] <= b["handle_pct"][1]
                n += b["n_variants"] > 0
    assert n > 0


def test_variants_do_not_touch_the_baseline_book(built):
    _, book, out = built
    pops = {r[0] for r in book.execute("SELECT population_id FROM v4_population")}
    assert pops == {out["nfl"]["audit"]["population_id"], out["cfb"]["audit"]["population_id"]}


def test_no_games_before_any_collection():
    out = LB.build(new_book(), build_store(), T0 - 10 * 86400, [poll(T0 - 86400)], bettors=500, variants={})
    assert out["nfl"]["status"] == "NO_GAMES" and out["nfl"]["audit"] is None
