"""CF1 production tracker: same frozen rule as research, point in time, write-once, PASS records."""
import hashlib
import json
import sqlite3

from cf1 import tracker as T


def _db(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT,"
              " away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL,"
              " fetched_at REAL)")
    c.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price,"
                  " market_last_update, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return c


def _q(eid, t, ko, book, hp):
    return [(eid, "americanfootball_ncaaf", ko, "H", "A", book, "spreads", "home", hp, -110, t, t),
            (eid, "americanfootball_ncaaf", ko, "H", "A", book, "spreads", "away", -hp, -110, t, t)]


def test_rule_hash_matches_research_definition():
    assert T.RULE_HASH == hashlib.sha256(json.dumps(T.RULE, sort_keys=True).encode()).hexdigest()
    assert T.RULE_HASH == "4723d3200f977daf79add7c727f86c9ae4035b861a7fed5db0701762480f0fc0"


def test_signal_pass_point_in_time_write_once(tmp_path):
    ko = 1_800_000_000.0
    rows = []
    for bk in ("draftkings", "fanduel", "betmgm"):
        rows += _q("mv", ko - 48 * 3600, ko, bk, -3.0) + _q("mv", ko - 2 * 3600, ko, bk, -5.0)
        rows += _q("mv", ko - 600, ko, bk, 7.0)                       # after build time: never visible
        rows += _q("st", ko - 48 * 3600, ko, bk, -3.0) + _q("st", ko - 2 * 3600, ko, bk, -3.5)
    c = _db(rows)
    w = T.record(c, ko - 3000, str(tmp_path))
    recs = {x: json.load(open(tmp_path / x)) for x in w}
    mv, st_ = recs["cf1_mv_spread.json"], recs["cf1_st_spread.json"]
    assert mv["decision"] == "SIGNAL" and mv["side"] == "home" and mv["point"] == -5.0 and mv["move"] > 1
    assert st_["decision"] == "PASS"
    assert T.record(c, ko - 3000, str(tmp_path)) == []                # write-once
    assert T.record(c, ko - 3000, str(tmp_path / "x"), existing=["cf1_mv_spread.json", "cf1_st_spread.json"]) == []
    assert T.record(_db(rows), ko - 2 * 3600 - 1800, str(tmp_path / "early")) == []   # before decision time
