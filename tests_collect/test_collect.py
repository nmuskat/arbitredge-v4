import json
import os
import sqlite3
import threading
import time

import pytest

import odds_gateway as og
from analysis_v4 import line_shop
from collect_v4 import collector as C
from edge_v4.odds_client import OddsClient
from market_v4 import normalize, store

KEY = "".join(["k1", "k2", "k3", "k4"]) * 8          # fake, built at runtime
NOW = store.iso_to_epoch("2026-10-31T15:00:00Z")      # Saturday


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def odds_payload(t, games, soft_home=-110):
    out = []
    for gid, kick in games:
        bks = []
        for b, hp, ap in (("pinnacle", -108, -102), ("betonlineag", -107, -103), ("lowvig", -108, -102),
                          ("draftkings", soft_home, -110), ("fanduel", -110, -110)):
            bks.append({"key": b, "last_update": iso(t - 30), "markets": [
                {"key": "spreads", "last_update": iso(t - 30), "outcomes": [
                    {"name": "Home", "price": hp, "point": -3.0}, {"name": "Away", "price": ap, "point": 3.0}]},
                {"key": "totals", "last_update": iso(t - 30), "outcomes": [
                    {"name": "Over", "price": -110, "point": 44.5}, {"name": "Under", "price": -110, "point": 44.5}]},
                {"key": "h2h", "last_update": iso(t - 30), "outcomes": [
                    {"name": "Home", "price": -150}, {"name": "Away", "price": 130}]}]})
        out.append({"id": gid, "sport_key": "americanfootball_nfl", "commence_time": iso(kick),
                    "home_team": "Home", "away_team": "Away", "bookmakers": bks})
    return out


class Upstream:
    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.games = [("a" * 32, NOW + 3600), ("b" * 32, NOW + 30 * 3600), ("c" * 32, NOW - 600)]   # c is in play

    def __call__(self, path, q):
        self.calls.append(path)
        if path.endswith("/events"):
            body = [{"id": g, "commence_time": iso(k)} for g, k in self.games]
            return 200, {"x-requests-last": "0", "x-requests-remaining": "15000", "x-requests-used": "5000"}, json.dumps(body).encode()
        return 200, {"x-requests-last": "3", "x-requests-remaining": "14997", "x-requests-used": "5003"}, \
            json.dumps(odds_payload(self.clock(), self.games)).encode()


@pytest.fixture
def rig(tmp_path):
    clock = {"t": NOW}
    up = Upstream(lambda: clock["t"])
    cfg = dict(og.DEFAULT_CONFIG, state_dir=str(tmp_path / "gw"), live_enabled=True, socket_group=None,
               socket_path=str(tmp_path / "gw.sock"), v4_monthly_cap=500, v4_daily_cap=150, max_single_request=30)
    gw = og.Gateway(cfg, KEY, clock=lambda: clock["t"], opener=up)
    srv = og.UnixHTTPServer(cfg["socket_path"], og.make_handler(gw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = C.Collector(OddsClient(cfg["socket_path"]), str(tmp_path / "data"), clock=lambda: clock["t"],
                    sports=("americanfootball_nfl",))
    yield c, up, clock, gw, tmp_path
    srv.shutdown()


@pytest.mark.parametrize("mins,every", [(None, 720), (30, 10), (90, 10), (91, 30), (300, 30), (1000, 60), (5000, 180), (20000, 720)])
def test_cadence(mins, every):
    assert C.cadence_minutes(mins) == every


def test_collects_through_gateway_and_skips_when_not_due(rig):
    c, up, clock, gw, tmp = rig
    r = c.run_once()[0]
    assert r["quotes"] > 0 and r["usage"]["x-requests-last"] == "3"
    assert set(r["books"]) == {"pinnacle", "betonlineag", "lowvig", "draftkings", "fanduel"}
    rows = c.conn.execute("SELECT DISTINCT event_id FROM mkt_quote").fetchall()
    assert ("c" * 32,) not in rows                       # in-play game excluded
    assert gw.status()["usage"]["v4"] == 3               # events call was free
    clock["t"] += 5 * 60
    assert "skipped" in c.run_once()[0]                  # next kickoff in < 90 min -> every 10 min
    clock["t"] += 6 * 60
    assert "quotes" in c.run_once()[0]
    assert sum(1 for p in up.calls if p.endswith("/odds")) == 2


def test_budget_exhaustion_is_logged_not_fatal(rig):
    c, up, clock, gw, tmp = rig
    gw.gate.budget = og.credits.Budget(v4_monthly_cap=2, v4_daily_cap=150, v3_floor_remaining=2000, max_single_request=30)
    r = c.run_once()[0]
    assert r["status"] == 402 and "monthly cap" in r["refused"]
    log = open(os.path.join(tmp, "data", "collector_log.jsonl")).read()
    assert '"event": "refused"' in log and KEY not in log


def test_key_never_written_to_data_dir(rig):
    c, up, clock, gw, tmp = rig
    c.run_once()
    for root, _, files in os.walk(tmp):
        for f in files:
            p = os.path.join(root, f)
            if os.path.isfile(p) and not p.endswith(".sock"):
                assert KEY.encode() not in open(p, "rb").read(), p


# ---------------------------------------------------------------- line-shop analysis

def build_history(close_home_fair_price):
    """Soft book offers home -3 at +115 two days out; sharps at -108. At close the
    sharp consensus moves to `close_home_fair_price` on the home side."""
    conn = store.connect()
    kick = NOW + 48 * 3600
    gid = "d" * 32
    t = NOW
    while t < kick:
        hp = -108 if t < kick - 3 * 3600 else close_home_fair_price
        ap = -102 if t < kick - 3 * 3600 else (-110 if close_home_fair_price < -115 else -102)
        payload = odds_payload(t, [(gid, kick)], soft_home=115 if t < NOW + 3600 else -110)
        for bk in payload[0]["bookmakers"]:
            if bk["key"] in ("pinnacle", "betonlineag", "lowvig"):
                bk["markets"][0]["outcomes"][0]["price"] = hp
                bk["markets"][0]["outcomes"][1]["price"] = ap
        q, an = normalize.normalize(payload, iso(t))
        store.ingest(conn, q, an, iso(t), "americanfootball_nfl", "odds", "fp")
        t += 1800
    return conn


def test_line_shop_finds_once_and_scores_against_close():
    conn = build_history(-125)                       # close moves toward the home side: the +115 was a real edge
    r = line_shop.report(conn, "americanfootball_nfl")
    rows = [o for o in r["opportunity_rows"] if o["book"] == "draftkings" and o["market"] == "spreads"]
    assert len(rows) == 1                            # seen in two snapshots, counted once
    assert rows[0]["american"] == 115 and rows[0]["closing_ev"] > 0.05
    assert r["close_observed"] >= 1 and r["closing_ev"]["games"] == 1


def test_close_against_us_gives_negative_closing_ev():
    conn = build_history(110)                        # consensus later moves away from home
    r = line_shop.report(conn, "americanfootball_nfl")
    row = [o for o in r["opportunity_rows"] if o["book"] == "draftkings" and o["market"] == "spreads"][0]
    assert row["closing_ev"] < row["ev_per_unit"]


def test_unobserved_close_excluded_not_imputed():
    conn = store.connect()
    kick = NOW + 10 * 3600
    gid = "e" * 32
    q, an = normalize.normalize(odds_payload(NOW, [(gid, kick)], soft_home=115), iso(NOW))
    store.ingest(conn, q, an, iso(NOW), "americanfootball_nfl", "odds", "fp")    # one early snapshot, no close
    r = line_shop.report(conn, "americanfootball_nfl")
    assert r["opportunities"] >= 1 and r["close_observed"] == 0 and r["close_unobserved"] == r["opportunities"]
    assert r["verdict"] == "INSUFFICIENT DATA"
