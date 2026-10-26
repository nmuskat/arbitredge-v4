import calendar
import sqlite3
import time

import pytest

from market_v4 import asof, normalize, quality, store


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def ts(s):
    return store.iso_to_epoch(s)


EID_A = "a" * 32   # kicks off inside the outage gap
EID_B = "b" * 32   # kicks off after service restored


def event(eid, home, away, commence, books, spread=-3.0, total=44.5):
    """books: {book: (home_ml, away_ml, home_spread_price, away_spread_price, over, under)}"""
    bks = []
    for book, (hml, aml, hsp, asp, ov, un) in books.items():
        bks.append({"key": book, "title": book, "last_update": None, "markets": [
            {"key": "h2h", "last_update": None, "outcomes": [{"name": home, "price": hml}, {"name": away, "price": aml}]},
            {"key": "spreads", "last_update": None, "outcomes": [
                {"name": home, "price": hsp, "point": spread}, {"name": away, "price": asp, "point": -spread}]},
            {"key": "totals", "last_update": None, "outcomes": [
                {"name": "Over", "price": ov, "point": total}, {"name": "Under", "price": un, "point": total}]},
            {"key": "player_pass_tds", "outcomes": [{"name": "X", "price": 100}]},
        ]})
    return {"id": eid, "sport_key": "americanfootball_nfl", "commence_time": commence,
            "home_team": home, "away_team": away, "bookmakers": bks}


def stamp(payload, t):
    for ev in payload:
        for b in ev["bookmakers"]:
            b["last_update"] = iso(t - 60)
            for m in b["markets"]:
                m["last_update"] = iso(t - 60)
    return payload


BOOKS = {"pinnacle": (-150, 130, -105, -105, -110, -110),
         "draftkings": (-155, 130, -110, -110, -110, -110),
         "fanduel": (-160, 134, -108, -112, -112, -108)}

GAP_START, GAP_END = ts("2026-10-25T00:55:00Z"), ts("2026-10-26T17:44:00Z")
KICK_A, KICK_B = "2026-10-25T17:00:00Z", "2026-10-27T00:15:00Z"


@pytest.fixture
def db():
    conn = store.connect()
    t = ts("2026-10-24T12:00:00Z")
    while t <= ts("2026-10-27T00:00:00Z"):
        if not (GAP_START < t < GAP_END):
            evs = []
            if t < ts(KICK_A):
                evs.append(event(EID_A, "Philadelphia Eagles", "Dallas Cowboys", KICK_A, BOOKS))
            evs.append(event(EID_B, "Kansas City Chiefs", "Buffalo Bills", KICK_B, BOOKS, spread=-2.5, total=47.5))
            q, an = normalize.normalize(stamp(evs, t), iso(t), request_fp="fp")
            store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
        t += 15 * 60
    # the restoration fetch exactly at 17:44
    q, an = normalize.normalize(stamp([event(EID_B, "Kansas City Chiefs", "Buffalo Bills", KICK_B, BOOKS, -2.5, 47.5)],
                                      GAP_END), iso(GAP_END))
    store.ingest(conn, q, an, iso(GAP_END), "americanfootball_nfl", "odds", "fp")
    return conn


# ---------------- normalize ----------------
def test_normalize_counts_and_skips_unsupported_markets():
    q, an = normalize.normalize([event(EID_B, "H", "A", KICK_B, BOOKS)], "2026-10-26T18:00:00Z")
    assert len(q) == 3 * 3 * 2 and an == []
    assert {x.market for x in q} == {"h2h", "spreads", "totals"}


def test_normalize_historical_wrapper_marks_source():
    payload = {"timestamp": "2026-10-20T12:00:00Z", "previous_timestamp": None, "next_timestamp": None,
               "data": [event(EID_B, "H", "A", KICK_B, {"pinnacle": BOOKS["pinnacle"]})]}
    q, _ = normalize.normalize(payload, "2026-10-26T18:00:00Z")
    assert q and all(x.source == "odds_api:historical" for x in q)
    assert all(x.market_last_update == "2026-10-20T12:00:00Z" for x in q)   # snapshot time, not fetch time


@pytest.mark.parametrize("mutate,kind", [
    (lambda e: e["bookmakers"][0]["markets"][0]["outcomes"][0].update(price=50), "invalid_price"),
    (lambda e: e["bookmakers"][0]["markets"][0]["outcomes"][0].update(name="Philly"), "unmatched_outcome_name"),
    (lambda e: e["bookmakers"][0]["markets"][1]["outcomes"][1].update(point=2.5), "spread_points_not_opposite"),
    (lambda e: e["bookmakers"][0]["markets"][2]["outcomes"][1].update(point=45.0), "total_points_differ"),
    (lambda e: e["bookmakers"][0]["markets"][1]["outcomes"].pop(), "incomplete_market"),
])
def test_normalize_anomalies_reported_and_market_excluded(mutate, kind):
    ev = event(EID_B, "H", "A", KICK_B, {"pinnacle": BOOKS["pinnacle"]})
    mutate(ev)
    q, an = normalize.normalize([ev], "2026-10-26T18:00:00Z")
    assert any(a["kind"] == kind for a in an)
    assert len(q) == 4        # the bad market is excluded entirely, the other two kept


# ---------------- store ----------------
def test_store_is_append_only(db):
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM mkt_quote")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE mkt_quote SET price=100")


def test_utc_parsing():
    assert store.iso_to_epoch("2026-10-26T17:44:00Z") == calendar.timegm((2026, 10, 26, 17, 44, 0, 0, 0, 0))
    assert store.iso_to_epoch("2026-10-26T17:44:00.500Z") == store.iso_to_epoch("2026-10-26T17:44:00Z") + 0.5
    with pytest.raises(ValueError):
        store.iso_to_epoch("2026-10-26T17:44:00")


# ---------------- point in time ----------------
def test_ok_state_consensus_and_best_price(db):
    st = asof.market_state(db, EID_B, "spreads", ts("2026-10-24T18:00:00Z"))
    assert st["status"] == "OK" and st["line"] == -2.5 and st["n_books_on_line"] == 3
    assert 0.49 < st["fair"]["home"] < 0.51
    assert st["best"]["home"] == ("pinnacle", -105)
    assert st["sharp_books_on_line"] == ["pinnacle"]


def test_no_look_ahead(db):
    T = ts("2026-10-24T18:07:00Z")
    st = asof.market_state(db, EID_B, "h2h", T)
    assert all(b["age_s"] >= 0 for b in st["books"].values())
    assert max(T - b["age_s"] for b in st["books"].values()) <= T


def test_outage_gap_closes_market(db):
    st = asof.market_state(db, EID_B, "spreads", ts("2026-10-26T06:00:00Z"))
    assert st["status"] == "STALE" and st["newest_quote_age_s"] > 29 * 3600
    assert "fair" not in st          # nothing to bet against: no price is invented


def test_restoration_reopens_market(db):
    assert asof.market_state(db, EID_B, "spreads", GAP_END + 60)["status"] == "OK"


def test_no_data_and_kickoff(db):
    assert asof.market_state(db, EID_B, "h2h", ts("2026-10-01T00:00:00Z"))["status"] == "NO_DATA"
    assert asof.market_state(db, EID_B, "h2h", ts(KICK_B) + 1)["status"] == "CLOSED_KICKOFF"


def test_main_line_chosen_by_mode():
    conn = store.connect()
    t = ts("2026-10-24T12:00:00Z")
    ev = event(EID_B, "H", "A", KICK_B, BOOKS, spread=-3.0)
    ev["bookmakers"][2]["markets"][1]["outcomes"][0]["point"] = -3.5
    ev["bookmakers"][2]["markets"][1]["outcomes"][1]["point"] = 3.5
    q, an = normalize.normalize(stamp([ev], t), iso(t))
    store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    st = asof.market_state(conn, EID_B, "spreads", t + 60)
    assert st["line"] == -3.0 and st["n_books_on_line"] == 2


# ---------------- observed close ----------------
def test_close_unobserved_for_game_kicking_off_in_gap(db):
    close, why = asof.observed_close(db, EID_A, "spreads")
    assert close is None and "no fresh quote" in why


def test_close_observed_after_restoration(db):
    close, why = asof.observed_close(db, EID_B, "spreads")
    assert why == "observed" and close["status"] == "OK"


# ---------------- quality ----------------
def test_fetch_gap_detected_and_affected_games(db):
    gaps = quality.fetch_gaps(db, "americanfootball_nfl", 15 * 60, ts("2026-10-24T12:00:00Z"), ts("2026-10-27T00:00:00Z"))
    assert len(gaps) == 1 and 40 < gaps[0]["hours"] < 41.5
    hit = quality.games_affected_by_gaps(db, gaps)
    assert [g["event_id"] for g in hit] == [EID_A]


def test_stale_upcoming_detected(db):
    st = quality.stale_upcoming(db, ts("2026-10-26T06:00:00Z"), 45 * 60)
    assert [s["event_id"] for s in st] == [EID_B]
    assert quality.stale_upcoming(db, GAP_END + 60, 45 * 60) == []


def test_duplicate_games_and_unmatched_teams():
    conn = store.connect()
    t = ts("2026-10-24T12:00:00Z")
    evs = [event(EID_A, "Philadelphia Eagles", "Dallas Cowboys", KICK_A, {"pinnacle": BOOKS["pinnacle"]}),
           event("c" * 32, "Dallas Cowboys", "Philadelphia Eagles", "2026-10-25T17:05:00Z", {"pinnacle": BOOKS["pinnacle"]})]
    q, an = normalize.normalize(stamp(evs, t), iso(t))
    store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    d = quality.duplicate_games(conn)
    assert d and d[0]["kind"] == "duplicate_game_swapped_home"
    u = quality.unmatched_teams(conn, ["Philadelphia Eagles"])
    assert [x["name"] for x in u] == ["Dallas Cowboys"]


def test_suspicious_single_book_move():
    conn = store.connect()
    t0 = ts("2026-10-24T12:00:00Z")
    for i, fd_home in enumerate([-110, -160]):         # fanduel alone jumps ~8 points of probability
        t = t0 + i * 900
        books = {"pinnacle": (-150, 130, -110, -110, -110, -110), "draftkings": (-150, 130, -110, -110, -110, -110),
                 "fanduel": (-150, 130, fd_home, -110 if fd_home == -110 else 140, -110, -110)}
        q, an = normalize.normalize(stamp([event(EID_B, "H", "A", KICK_B, books)], t), iso(t))
        store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    s = quality.suspicious_moves(conn, EID_B, "spreads")
    assert [x["book"] for x in s] == ["fanduel"]


def test_market_wide_move_not_flagged():
    conn = store.connect()
    t0 = ts("2026-10-24T12:00:00Z")
    for i, (hp, ap) in enumerate([(-110, -110), (-160, 140)]):
        t = t0 + i * 900
        books = {k: (-150, 130, hp, ap, -110, -110) for k in ("pinnacle", "draftkings", "fanduel")}
        q, an = normalize.normalize(stamp([event(EID_B, "H", "A", KICK_B, books)], t), iso(t))
        store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    assert quality.suspicious_moves(conn, EID_B, "spreads") == []


def test_quote_stamped_after_T_by_the_book_is_excluded():
    """Book clock/labelling skew: a row fetched before T but whose book-reported
    update time is after T must not be visible at T."""
    conn = store.connect()
    t = ts("2026-10-24T12:00:00Z")
    ev = stamp([event(EID_B, "H", "A", KICK_B, {"pinnacle": BOOKS["pinnacle"]})], t)
    for m in ev[0]["bookmakers"][0]["markets"]:
        m["last_update"] = iso(t + 600)
    q, an = normalize.normalize(ev, iso(t))
    store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    assert asof.market_state(conn, EID_B, "h2h", t + 300)["status"] == "NO_DATA"
    assert asof.market_state(conn, EID_B, "h2h", t + 700)["status"] == "OK"


def test_invalid_row_raises_instead_of_vanishing():
    conn = store.connect()
    bad = normalize.Quote(EID_B, "americanfootball_nfl", KICK_B, "H", "A", "pinnacle", "h2h", "home", None, None,
                          "2026-10-24T12:00:00Z", "2026-10-24T12:00:00Z", "odds_api", "fp")
    with pytest.raises(sqlite3.IntegrityError):
        store.ingest(conn, [bad], [], "2026-10-24T12:00:00Z", "americanfootball_nfl", "odds", "fp")
    assert conn.execute("SELECT COUNT(*) FROM mkt_fetch").fetchone()[0] == 0     # whole fetch rolled back


def test_one_inconsistent_soft_book_does_not_poison_consensus():
    conn = store.connect()
    t = ts("2026-10-24T12:00:00Z")
    books = {"pinnacle": (-150, 130, -108, -102, -110, -110), "betonlineag": (-150, 130, -107, -103, -110, -110),
             "draftkings": (-150, 130, 115, -110, -110, -110)}          # under-round spread pair at draftkings
    q, an = normalize.normalize(stamp([event(EID_B, "H", "A", KICK_B, books)], t), iso(t))
    store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
    st = asof.market_state(conn, EID_B, "spreads", t + 60)
    assert st["status"] == "OK" and st["consensus_basis"] == "market_makers"
    assert st["consensus_books"] == ["betonlineag", "pinnacle"] and "draftkings" in st["books"]
    assert st["best"]["home"] == ("draftkings", 115)        # still visible as a price to shop
