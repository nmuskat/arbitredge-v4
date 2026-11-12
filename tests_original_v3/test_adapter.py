"""original_v3 adapter: original layout, no post-kickoff quotes, results only known after the game, original
pipeline runs unchanged on the adapted data."""
import sqlite3

import original_v3.adapter as A


def _v4(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE mkt_quote (id INTEGER PRIMARY KEY, event_id TEXT, sport TEXT, commence REAL, home TEXT,"
              " away TEXT, book TEXT, market TEXT, side TEXT, point REAL, price INTEGER, market_last_update REAL,"
              " fetched_at REAL)")
    ko = 1_800_000_000.0
    rows = []
    for k, t in enumerate((ko - 7 * 3600, ko - 3 * 3600, ko - 2 * 3600, ko - 1800, ko + 600)):
        for bk in ("draftkings", "fanduel", "betmgm"):
            hp = -3.0 - (0.5 if k >= 2 else 0)
            rows += [("e1", "americanfootball_nfl", ko, "H", "A", bk, "spreads", "home", hp, -110, t - 60, t),
                     ("e1", "americanfootball_nfl", ko, "H", "A", bk, "spreads", "away", -hp, -110, t - 60, t)]
    c.executemany("INSERT INTO mkt_quote (event_id, sport, commence, home, away, book, market, side, point, price,"
                  " market_last_update, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    return ko


def test_layout_point_in_time_and_original_pipeline_runs(tmp_path):
    ko = _v4(str(tmp_path / "v4.db"))
    conn, ids = A.build(str(tmp_path / "v4.db"), {("nfl", "e1"): (24, 20)}, [], str(tmp_path / "v3.db"))
    gid = ids["e1"][0]
    assert conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE observed_at >= ?", (A.iso(ko),)).fetchone()[0] == 0
    assert conn.execute("SELECT retrieved_at FROM game_results").fetchone()[0] > A.iso(ko)
    from book_need.inference import compute_inference
    r = compute_inference(conn, gid, A.iso(ko - 3600))
    assert r["inferred_book_need_side"] in ("home", "away", "NO_CLEAR_NEED", "INSUFFICIENT_EVIDENCE", "NOT_YET_EVALUABLE")
    assert compute_inference(conn, gid, A.iso(ko - 24 * 3600))["inferred_book_need_side"] == "NOT_YET_EVALUABLE"
