"""CF1 tracker: frozen rule, point-in-time decision, side toward the move, write-once records."""
import hashlib
import json
import sqlite3

from lab_v4 import cfb_follow as CF


def _db(path, rows):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT,"
              " away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL,"
              " fetched_at REAL)")
    c.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price,"
                  " market_last_update, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return c


def _q(t, ko, book, hp):
    return [("e1", "americanfootball_ncaaf", ko, "H", "A", book, "spreads", "home", hp, -110, t, t),
            ("e1", "americanfootball_ncaaf", ko, "H", "A", book, "spreads", "away", -hp, -110, t, t)]


def test_rule_frozen():
    assert CF.RULE_HASH == hashlib.sha256(json.dumps(CF.RULE, sort_keys=True).encode()).hexdigest()
    assert CF.RULE["move_pts"] == 1.0 and CF.RULE["decision_offset_s"] == 3600


def test_follow_side_point_in_time_and_write_once(tmp_path):
    ko = 1_800_000_000.0
    rows = []
    for bk in ("draftkings", "fanduel", "betmgm"):
        rows += _q(ko - 48 * 3600, ko, bk, -3.0)          # open: home -3
        rows += _q(ko - 2 * 3600, ko, bk, -5.0)           # decision: home -5 (moved toward home)
        rows += _q(ko - 600, ko, bk, +1.0)                # after the decision: must never be seen
    c = _db(str(tmp_path / "m.db"), rows)
    from intel_v7.history import History
    d = CF.decide(History(c, "cfb", "e1", "spread"))
    assert d["decision"] == "SIGNAL" and d["side"] == "home" and d["move"] > 1.0 and d["point"] == -5.0
    out = str(tmp_path / "rec")
    assert CF.record(c, ko - 3000, out) == 1
    assert CF.record(c, ko - 3000, out) == 0                    # write-once
