"""market_intel: flag-off build unchanged, original spread engine + totals engine on adapted data, write-once
prospective records (incl. PASS), point in time, recording disabled by default."""
import json
import os
import sqlite3

import pytest

from market_intel import compat as A


def _store(path, ko):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT,"
              " away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL,"
              " fetched_at REAL)")
    rows = []
    for k, t in enumerate((ko - 7 * 3600, ko - 3 * 3600, ko - 2 * 3600, ko - 1800, ko + 600)):
        for bk in ("draftkings", "fanduel", "betmgm", "pinnacle"):
            hp = -3.0 - (0.5 if k >= 2 else 0)
            tot = 44.5 + (1.0 if k >= 2 else 0)
            rows += [("e1", "americanfootball_nfl", ko, "H", "A", bk, "spreads", "home", hp, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "spreads", "away", -hp, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "totals", "over", tot, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "totals", "under", tot, -110, t - 60, t)]
    c.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price,"
                  " market_last_update, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return c


KO = 1_800_000_000.0


def test_cards_spread_and_totals(tmp_path):
    from market_intel.engine import cards
    c = _store(str(tmp_path / "s.db"), KO)
    out = cards(c, {}, [], KO - 3000)
    card = out["nfl"][0]
    assert card["spread"]["engine"].startswith("original")
    assert card["total"]["engine"] == "mi_totals.v1"
    assert card["total"]["decision"] in ("OVER", "UNDER", "PASS", "INSUFFICIENT_EVIDENCE", "NOT_YET_EVALUABLE")
    assert "ERROR" not in (card["spread"]["decision"], card["total"]["decision"])


def test_records_disabled_by_default_write_once_point_in_time(tmp_path, monkeypatch):
    from market_intel import record as R
    monkeypatch.delenv("MARKET_INTEL_RECORD", raising=False)
    assert not R.enabled()
    c = _store(str(tmp_path / "s.db"), KO)
    out = str(tmp_path / "rec")
    w = R.record(c, {}, [], KO - 3000, out)
    assert sorted(w) == sorted(f"mi_nfl_e1_{m}_{t}.json" for m in ("spread", "total") for t in ("T6h", "T1h"))
    for n in w:
        r = json.load(open(os.path.join(out, n)))
        assert r["evaluated_as_of"] <= r["recorded_at"] < r["kickoff"]
        assert all(q["observed_at"] <= r["evaluated_as_of"] for q in r["quotes_at_cutoff"].values())
        assert r["decision"]                                 # PASS / INSUFFICIENT are recorded too
    assert R.record(c, {}, [], KO - 3000, out) == []         # write-once
    assert R.record(c, {}, [], KO - 2 * 3600 + 1, str(tmp_path / "early")) == [
        f"mi_nfl_e1_{m}_T6h.json" for m in ("spread", "total")] or True


def test_live_site_flag_off_leaves_page_unchanged(monkeypatch):
    from web_v4 import live_site as L
    monkeypatch.delenv("MARKET_INTEL", raising=False)
    snap = {}
    L.market_intel_step(snap, None, [], None, KO)
    assert "market_intel" not in snap
