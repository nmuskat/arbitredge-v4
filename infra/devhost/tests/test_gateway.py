"""
Gateway tests. Run with the V4 repo on PYTHONPATH (for edge_v4.odds_client):
  PYTHONPATH=<v4 repo>:gateway pytest tests/test_gateway.py
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import odds_gateway as og
from edge_v4.odds_client import GatewayRefused, OddsClient

KEY = "".join(["ab12", "cd34", "ef56", "ab78", "cd90", "ef12", "ab34", "cd56"])  # 32 chars, built at runtime
EID = "0123456789abcdef0123456789abcdef"


def cfg(tmp_path, **kw):
    c = dict(og.DEFAULT_CONFIG, state_dir=str(tmp_path / "state"), live_enabled=True, socket_group=None)
    c.update(kw)
    return c


class FakeUpstream:
    """Records every call; returns scripted responses."""
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, path, query):
        self.calls.append((path, dict(query)))
        if self.responses:
            return self.responses.pop(0)
        return 200, {"x-requests-last": "3", "x-requests-used": "100", "x-requests-remaining": "19900"}, b'[{"id": "x"}]'


def odds_req(**kw):
    r = {"endpoint": "odds", "sport": "americanfootball_nfl",
         "params": {"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}, "purpose": "test"}
    r.update(kw)
    return r


def test_live_disabled_by_default_and_no_upstream_call(tmp_path):
    up = FakeUpstream()
    gw = og.Gateway(cfg(tmp_path, live_enabled=False), KEY, opener=up)
    with pytest.raises(og.Refused, match="disabled by the owner"):
        gw.handle(odds_req())
    assert up.calls == []
    assert og.DEFAULT_CONFIG["live_enabled"] is False
    with open(os.path.join(os.path.dirname(og.__file__), "gateway.json")) as f:
        assert json.load(f)["live_enabled"] is False


def test_success_records_usage_and_key_never_returned(tmp_path):
    up = FakeUpstream()
    gw = og.Gateway(cfg(tmp_path), KEY, opener=up)
    st, out = gw.handle(odds_req())
    assert st == 200 and out["est_cost"] == 3 and out["usage"]["x-requests-remaining"] == "19900"
    assert up.calls[0][0] == "/v4/sports/americanfootball_nfl/odds"
    assert up.calls[0][1]["apiKey"] == KEY
    assert KEY not in json.dumps(out)
    dump = "\n".join(gw.conn.iterdump())
    assert KEY not in dump


def test_upstream_echo_of_key_is_scrubbed(tmp_path):
    body = json.dumps({"message": f"bad key {KEY}", "error_code": "INVALID_KEY"}).encode()
    gw = og.Gateway(cfg(tmp_path), KEY, opener=FakeUpstream([(401, {}, body)]))
    st, out = gw.handle(odds_req())
    assert st == 401 and KEY not in json.dumps(out) and "[REDACTED]" in out["provider_message"]


def test_exception_text_scrubbed(tmp_path):
    def boom(path, q):
        raise OSError(f"connect failed for apiKey={q['apiKey']}")
    gw = og.Gateway(cfg(tmp_path), KEY, opener=boom)
    with pytest.raises(og.Refused) as e:
        gw.handle(odds_req())
    assert KEY not in str(e.value) and e.value.status == 502


@pytest.mark.parametrize("bad,msg", [
    (dict(endpoint="../admin"), "endpoint"),
    (dict(sport="basketball_nba"), "sport"),
    (dict(sport="americanfootball_nfl/../../x"), "sport"),
    (dict(params={"regions": "us", "markets": "h2h", "apiKey": "x"}), "apiKey"),
    (dict(params={"regions": "us", "markets": "h2h&apiKey=x"}), "invalid value"),
    (dict(params={"regions": "us"}), "markets must be explicit"),
    (dict(params={"markets": "h2h"}), "regions or bookmakers"),
    (dict(params={"regions": "us", "markets": "h2h", "oddsFormat": "decimal"}), "american"),
    (dict(endpoint="event_odds", event_id="../../x"), "event_id"),
    (dict(endpoint="historical_odds"), "require date"),
])
def test_request_validation(tmp_path, bad, msg):
    up = FakeUpstream()
    gw = og.Gateway(cfg(tmp_path), KEY, opener=up)
    with pytest.raises(og.Refused, match=msg):
        gw.handle(odds_req(**bad))
    assert up.calls == []


def test_historical_cost_and_per_request_ceiling(tmp_path):
    up = FakeUpstream()
    gw = og.Gateway(cfg(tmp_path), KEY, opener=up)
    req = odds_req(endpoint="historical_odds",
                   params={"regions": "us,eu", "markets": "h2h,spreads,totals", "date": "2025-10-01T12:00:00Z"})
    with pytest.raises(og.Refused, match="approval"):   # 10 x 3 x 2 = 60 ... raise ceiling test below
        og.Gateway(cfg(tmp_path, max_single_request=50), KEY, opener=up).handle(req)
    assert up.calls == []
    up.responses = [(200, {"x-requests-last": "60"}, b"{}")]
    st, out = gw.handle(req)
    assert out["est_cost"] == 60


def test_monthly_cap_protects_budget(tmp_path):
    up = FakeUpstream()
    gw = og.Gateway(cfg(tmp_path, v4_monthly_cap=6, v4_daily_cap=6), KEY, opener=up)
    gw.handle(odds_req())
    gw.handle(odds_req())
    with pytest.raises(og.Refused, match="monthly cap") as e:
        gw.handle(odds_req())
    assert e.value.status == 402 and len(up.calls) == 2


def test_v3_floor_uses_provider_remaining(tmp_path):
    up = FakeUpstream([(200, {"x-requests-last": "3", "x-requests-remaining": "2002", "x-requests-used": "17998"}, b"[]")])
    gw = og.Gateway(cfg(tmp_path), KEY, opener=up)
    gw.handle(odds_req())
    with pytest.raises(og.Refused, match="V3 floor"):
        gw.handle(odds_req())
    assert len(up.calls) == 1
    assert gw.status()["usage"] == {"v4": 3, "provider_used": 17998, "v3_inferred": 17995}


@pytest.mark.parametrize("status,code", [(401, "INVALID_KEY"), (401, "OUT_OF_USAGE_CREDITS"), (403, None)])
def test_breaker_trips_and_requires_reset(tmp_path, status, code):
    body = json.dumps({"message": "x", "error_code": code}).encode()
    up = FakeUpstream([(status, {}, body)])
    gw = og.Gateway(cfg(tmp_path), KEY, opener=up)
    gw.handle(odds_req())
    with pytest.raises(og.Refused, match="breaker"):
        gw.handle(odds_req())
    assert len(up.calls) == 1
    # persists across restart
    gw2 = og.Gateway(cfg(tmp_path), KEY, opener=up)
    assert gw2.breaker()["tripped"]
    gw2.reset_breaker()
    gw2.handle(odds_req())
    assert len(up.calls) == 2


def test_rate_limit_cooldown(tmp_path):
    clock = {"t": 1_790_000_000.0}
    up = FakeUpstream([(429, {}, b'{"message":"slow down","error_code":"EXCEEDED_FREQ_LIMIT"}')])
    gw = og.Gateway(cfg(tmp_path), KEY, clock=lambda: clock["t"], opener=up)
    gw.handle(odds_req())
    with pytest.raises(og.Refused, match="cooldown"):
        gw.handle(odds_req())
    clock["t"] += 61
    assert gw.handle(odds_req())[0] == 200
    assert not gw.breaker()["tripped"]


def test_cache_serves_without_credits_even_when_live_disabled(tmp_path):
    up = FakeUpstream()
    gw = og.Gateway(cfg(tmp_path), KEY, opener=up)
    gw.handle(odds_req(cache_ttl_s=600))
    gw.cfg["live_enabled"] = False
    st, out = gw.handle(odds_req(cache_ttl_s=600))
    assert out["source"] == "cache" and len(up.calls) == 1


def test_ledger_rows_immutable(tmp_path):
    import sqlite3
    gw = og.Gateway(cfg(tmp_path), KEY, opener=FakeUpstream())
    gw.handle(odds_req())
    with pytest.raises(sqlite3.DatabaseError):
        gw.conn.execute("DELETE FROM credit_ledger")


@pytest.mark.parametrize("content", [
    "THE_ODDS_API_KEY={k}\n", "export THE_ODDS_API_KEY=\"{k}\"\n", "OTHER=1\nTHE_ODDS_API_KEY='{k}'\n", "{k}\n"])
def test_read_key_formats(tmp_path, content):
    p = tmp_path / "k"
    p.write_text(content.format(k=KEY))
    assert og.read_key(str(p)) == KEY


def test_read_key_rejects_garbage(tmp_path):
    p = tmp_path / "k"
    p.write_text("THE_ODDS_API_KEY=has spaces; rm -rf\n")
    with pytest.raises(ValueError) as e:
        og.read_key(str(p))
    assert "rm -rf" not in str(e.value)


def test_end_to_end_unix_socket_and_real_http_client(tmp_path):
    """Real socket server + real HTTP upstream (plain http stand-in for the provider)."""
    seen = []

    class Up(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(self.path)
            data = b'[{"id": "game1"}]'
            self.send_response(200)
            for k, v in {"x-requests-last": "3", "x-requests-used": "10", "x-requests-remaining": "19990"}.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    sock = str(tmp_path / "gw.sock")
    c = cfg(tmp_path, base_url=f"http://127.0.0.1:{up.server_address[1]}", socket_path=sock)
    t = threading.Thread(target=og.serve, args=(c, KEY), daemon=True)
    t.start()
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.02)
    assert oct(os.stat(sock).st_mode & 0o777) == "0o660"
    client = OddsClient(sock)
    out = client.request("odds", "americanfootball_nfl", {"regions": "us", "markets": "h2h,spreads,totals"})
    assert out["data"] == [{"id": "game1"}]
    assert "apiKey=" + KEY in seen[0]
    with pytest.raises(GatewayRefused) as e:
        client.request("odds", "basketball_nba", {"regions": "us", "markets": "h2h"})
    assert e.value.status == 400
    st = client.status()
    assert st["usage"]["v4"] == 3 and KEY not in json.dumps(st)
    up.shutdown()


def test_probe_bypasses_live_flag_costs_zero_and_hides_key(tmp_path):
    up = FakeUpstream([(200, {"x-requests-last": "0", "x-requests-used": "5", "x-requests-remaining": "19995"}, b"[]")])
    gw = og.Gateway(cfg(tmp_path, live_enabled=False), KEY, opener=up)
    out = gw.probe()
    assert out["provider_status"] == 200 and out["usage"]["x-requests-last"] == "0"
    assert up.calls[0][0] == "/v4/sports" and KEY not in json.dumps(out)
    assert gw.status()["usage"]["v4"] == 0


def test_key_echoed_in_successful_body_is_scrubbed(tmp_path):
    body = json.dumps([{"id": "g", "link": f"https://x/odds?apiKey={KEY}"}]).encode()
    gw = og.Gateway(cfg(tmp_path), KEY, opener=FakeUpstream([(200, {"x-requests-last": "3"}, body)]))
    st, out = gw.handle(odds_req(cache_ttl_s=60))
    assert st == 200 and KEY not in json.dumps(out)
    assert KEY not in "\n".join(gw.conn.iterdump())          # nor in the cache table
