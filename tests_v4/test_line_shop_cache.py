"""Incremental line-shopping cache (analysis_v4/line_shop_cache.py): the report must be IDENTICAL to the
full calculation in every scenario -- cold, warm, new snapshots, corrected/deleted snapshots, moved
kickoffs, interrupted or corrupt cache files, calculation-version changes and tampered entries."""
import gzip
import json
import os
import subprocess

import pytest

from analysis_v4 import line_shop, line_shop_cache as C
from edge_v4 import ev
from market_v4 import normalize, store

SPORT = "americanfootball_nfl"
T0 = store.iso_to_epoch("2026-10-31T15:00:00Z")
STEP = 1800
GAMES = [("a" * 32, T0 + 20 * 3600), ("b" * 32, T0 + 30 * 3600), ("c" * 32, T0 + 40 * 3600)]


def _iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def payload(i, t, fix=None, moved_kickoff=False):
    out = []
    for g, (gid, kick) in enumerate(GAMES):
        if moved_kickoff and gid == GAMES[2][0] and i >= 30:
            kick -= 6 * 3600                    # the feed later moves game c's kickoff earlier
        if t >= kick:
            continue
        soft = (115, 105, -110, 120, 100)[(i + g) % 5]
        if fix is not None and fix == (i, g):
            soft = -105
        sharp_home = -108 - (i % 3)
        bks = []
        for b, hp, ap in (("pinnacle", sharp_home, -102), ("betonlineag", -107, -103), ("lowvig", -108, -102),
                          ("draftkings", soft, -110), ("fanduel", -110, 100 if i % 4 == 0 else -110)):
            bks.append({"key": b, "last_update": _iso(t - 30), "markets": [
                {"key": "spreads", "last_update": _iso(t - 30), "outcomes": [
                    {"name": "Home", "price": hp, "point": -3.0}, {"name": "Away", "price": ap, "point": 3.0}]},
                {"key": "totals", "last_update": _iso(t - 30), "outcomes": [
                    {"name": "Over", "price": -110 if b != "fanduel" else 102, "point": 44.5},
                    {"name": "Under", "price": -110, "point": 44.5}]},
                {"key": "h2h", "last_update": _iso(t - 30), "outcomes": [
                    {"name": "Home", "price": -150}, {"name": "Away", "price": 130 if b != "draftkings" else 145}]}]})
        out.append({"id": gid, "sport_key": SPORT, "commence_time": _iso(kick), "home_team": "Home",
                    "away_team": "Away", "bookmakers": bks})
    return out


def history(n=60, skip=(), **kw):
    conn = store.connect()
    for i in range(n):
        if i in skip:
            continue
        t = T0 + i * STEP
        q, an = normalize.normalize(payload(i, t, **kw), _iso(t))
        store.ingest(conn, q, an, _iso(t), SPORT, "odds", "fp")
    return conn


def dumps(r):
    return json.dumps(r, sort_keys=True, default=repr)


def cached(conn, path, **kw):
    c = C.LineShopCache(path, seed=3, **kw)
    r = line_shop.report(conn, SPORT, cache=c)
    c.save(path)
    return r, c.stats[SPORT]


@pytest.fixture
def cpath(tmp_path):
    return str(tmp_path / "line_shop_cache.json.gz")


def test_cold_and_warm_identical_to_full(cpath):
    conn = history()
    full = line_shop.report(conn, SPORT)
    assert full["opportunities"] >= 5 and full["close_observed"] >= 1        # the fixture exercises real rows
    r1, s1 = cached(conn, cpath)
    assert dumps(r1) == dumps(full) and s1["status"].startswith("cold") and s1["reused"] == 0
    r2, s2 = cached(conn, cpath)
    assert dumps(r2) == dumps(full)
    assert s2["status"] == "warm" and s2["computed"] == 0 and s2["reused"] == s1["computed"] > 0
    assert s2["spot_checked"] == min(C.SPOT_CHECK, s2["reused"])


def test_new_snapshots_computed_only(cpath):
    cached(history(40), cpath)
    conn = history(60)
    r, st = cached(conn, cpath)
    assert dumps(r) == dumps(line_shop.report(conn, SPORT))
    assert st["reused"] > 0 and st["computed"] > 0 and st["invalidated"] == 0
    live_new = sum(1 for i in range(40, 60) for _, k in GAMES if T0 + i * STEP < k)
    assert st["computed"] == live_new                # only the new snapshots were scanned


def test_corrected_snapshot_invalidates_only_affected_game_from_that_time(cpath):
    cached(history(), cpath)
    conn = history(fix=(20, 1))                      # one book's price for game b at snapshot 20 corrected
    r, st = cached(conn, cpath)
    assert dumps(r) == dumps(line_shop.report(conn, SPORT))
    affected = sum(1 for i in range(20, 60) if T0 + i * STEP < GAMES[1][1])
    assert st["invalidated"] == affected and st["computed"] == affected


def test_deleted_snapshot(cpath):
    cached(history(), cpath)
    conn = history(skip={15})
    r, st = cached(conn, cpath)
    assert dumps(r) == dumps(line_shop.report(conn, SPORT)) and st["invalidated"] > 0


def test_kickoff_moved_later_in_feed(cpath):
    cached(history(30), cpath)
    conn = history(60, moved_kickoff=True)           # latest kickoff differs from the one seen when cached
    r, _ = cached(conn, cpath)
    assert dumps(r) == dumps(line_shop.report(conn, SPORT))


@pytest.mark.parametrize("damage", ["truncated", "garbage", "not_gzip", "wrong_schema", "bad_entries", "empty"])
def test_interrupted_or_corrupt_cache_falls_back_to_full(cpath, damage):
    conn = history()
    full = dumps(line_shop.report(conn, SPORT))
    cached(conn, cpath)
    raw = open(cpath, "rb").read()
    if damage == "truncated":                        # e.g. an interrupted upload/download
        open(cpath, "wb").write(raw[: len(raw) // 2])
    elif damage == "garbage":
        open(cpath, "wb").write(gzip.compress(b"{not json"))
    elif damage == "not_gzip":
        open(cpath, "wb").write(b"plain text")
    elif damage == "wrong_schema":
        open(cpath, "wb").write(gzip.compress(json.dumps({"schema": "other", "sports": {}}).encode()))
    elif damage == "empty":
        open(cpath, "wb").write(b"")
    else:
        doc = json.loads(gzip.decompress(raw))
        for k in list(doc["sports"][SPORT]["entries"])[:10]:
            doc["sports"][SPORT]["entries"][k] = ["x", "not a list"]
        open(cpath, "wb").write(gzip.compress(json.dumps(doc).encode()))
    r, _ = cached(conn, cpath)
    assert dumps(r) == full


def test_leftover_temp_file_from_interrupted_save_is_ignored(cpath):
    conn = history()
    cached(conn, cpath)
    open(cpath + ".tmp", "wb").write(b"half-written")
    r, st = cached(conn, cpath)
    assert dumps(r) == dumps(line_shop.report(conn, SPORT)) and st["status"] == "warm"


def test_version_change_recomputes_everything(cpath, monkeypatch):
    conn = history()
    cached(conn, cpath)
    monkeypatch.setattr(C, "code_hash", lambda: "different-code")
    r, st = cached(conn, cpath)
    assert st["status"] == "cold (calculation version changed)" and st["reused"] == 0
    assert dumps(r) == dumps(line_shop.report(conn, SPORT))


def test_policy_change_recomputes_everything(cpath):
    conn = history()
    cached(conn, cpath)
    pol = ev.DecisionPolicy(version="other", max_plausible_ev=0.10, max_american=1000)
    c = C.LineShopCache(cpath)
    r = line_shop.report(conn, SPORT, pol, cache=c)
    assert c.stats[SPORT]["status"] == "cold (calculation version changed)"
    assert dumps(r) == dumps(line_shop.report(conn, SPORT, pol))


def test_tampered_entry_caught_by_spot_check(cpath):
    conn = history()
    full = dumps(line_shop.report(conn, SPORT))
    cached(conn, cpath)
    doc = json.loads(gzip.decompress(open(cpath, "rb").read()))
    ent = doc["sports"][SPORT]["entries"]
    for k, v in ent.items():                         # fingerprint still matches, rows wrong
        for r in v[1]:
            r["american"] += 1
    open(cpath, "wb").write(gzip.compress(json.dumps(doc).encode()))
    c = C.LineShopCache(cpath, spot_check=10_000)
    r = line_shop.report(conn, SPORT, cache=c)
    assert "spot check failed" in c.stats[SPORT]["status"] and dumps(r) == full


def test_code_hash_covers_calculation_sources():
    h = C.code_hash()
    assert len(h) == 64 and h == C.code_hash()


def test_site_cli_writes_cache_and_reuses_it(tmp_path):
    from web_v4 import live_site
    conn = history(30)
    shard = tmp_path / "shards" / "odds-data-2026-W44"
    shard.mkdir(parents=True)
    with gzip.open(shard / "quotes_1.jsonl.gz", "wt") as fh:
        for t, cols in (("mkt_fetch", "fetched_at, sport, endpoint, request_fp, source, n_quotes, n_anomalies"),
                        ("mkt_quote", ", ".join(C.QUOTE_COLS))):
            names = [c.strip() for c in cols.split(",")]
            for row in conn.execute(f"SELECT {cols} FROM {t}"):
                fh.write(json.dumps({"t": t, **dict(zip(names, row))}) + "\n")
    perf = []
    for run in ("cold", "warm"):
        out = tmp_path / run
        cache = tmp_path / "cold" / "line_shop_cache.json.gz"
        live_site.main(["--shards", str(tmp_path / "shards"), "--out", str(out), "--bettors", "300",
                        "--now", _iso(T0 + 31 * STEP), "--line-shop-cache", str(cache)])
        assert (out / "line_shop_cache.json.gz").exists()
        perf.append(json.load(open(out / "site_snapshot.json"))["sports"]["nfl"]["performance"])
    assert perf[0] == perf[1] and perf[0]["opportunities"] > 0


def test_workflow_passes_cache_and_guards_overwrite():
    root = os.path.join(os.path.dirname(__file__), "..")
    wf = open(os.path.join(root, ".github", "workflows", "site-build.yml")).read()
    sh = open(os.path.join(root, ".github", "scripts", "site_inputs.sh")).read()
    assert "--line-shop-cache prev/line_shop_cache.json.gz" in wf
    assert "line_shop_cache.stamp" in wf and "line_shop_cache.stamp" in sh
    assert "site/line_shop_cache.json.gz --clobber" in wf
    r = subprocess.run(["bash", "-n", os.path.join(root, ".github", "scripts", "site_inputs.sh")])
    assert r.returncode == 0
