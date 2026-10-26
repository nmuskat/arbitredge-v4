#!/usr/bin/env python3
"""
ArbitrEdge V4 Odds API gateway.

The ONLY process on the dev server that can read the Odds API key. V4 code
(and the Claude Code session) talk to it over a local Unix socket and never
see the key. Every request passes the credit gate (V4 monthly cap, 24 h cap,
V3 floor on the shared balance, per-request ceiling) BEFORE anything is
sent, and is written to an append-only ledger afterwards.

Owned by root, runs as the unprivileged `arbitredge-gw` user under systemd.
The dev user cannot edit this file, its config, its ledger or the key.

Safe by default: live upstream calls stay DISABLED until the owner sets
"live_enabled": true in the root-owned config (cache reads still work).

Stdlib only.
"""

import http.client
import json
import os
import re
import socket
import socketserver
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import credits  # noqa: E402  (vendored copy of edge_v4/credits.py)

DEFAULT_CONFIG = {
    "base_url": "https://api.the-odds-api.com",
    "socket_path": "/run/arbitredge-v4-gateway/gateway.sock",
    "socket_group": "arbitredge-gwsock",
    "state_dir": "/var/lib/arbitredge-v4-gateway",
    "live_enabled": False,
    "allowed_sports": ["americanfootball_nfl", "americanfootball_ncaaf"],
    "v4_monthly_cap": 6000,
    "v4_daily_cap": 400,
    "v3_floor_remaining": 2000,
    "max_single_request": 200,
    "max_cache_ttl_s": 86400,
    "rate_limit_cooldown_s": 60,
    "upstream_timeout_s": 20,
}

PATHS = {
    "sports": "/v4/sports",
    "events": "/v4/sports/{sport}/events",
    "odds": "/v4/sports/{sport}/odds",
    "scores": "/v4/sports/{sport}/scores",
    "event_odds": "/v4/sports/{sport}/events/{event_id}/odds",
    "event_markets": "/v4/sports/{sport}/events/{event_id}/markets",
    "historical_odds": "/v4/historical/sports/{sport}/odds",
    "historical_events": "/v4/historical/sports/{sport}/events",
    "historical_event_odds": "/v4/historical/sports/{sport}/events/{event_id}/odds",
}
NEEDS_EVENT = {"event_odds", "event_markets", "historical_event_odds"}
NEEDS_MARKETS = {"odds", "event_odds", "historical_odds", "historical_event_odds"}
ALLOWED_PARAMS = {"regions", "markets", "bookmakers", "oddsFormat", "dateFormat", "date",
                  "eventIds", "commenceTimeFrom", "commenceTimeTo", "daysFrom"}
VALUE_RE = re.compile(r"^[A-Za-z0-9_,:.\-]{1,400}$")
EVENT_RE = re.compile(r"^[a-f0-9]{32}$")
HARD_TRIP_CODES = {"INVALID_KEY", "DEACTIVATED_KEY", "OUT_OF_USAGE_CREDITS", "MISSING_KEY"}


class Refused(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def read_key(path):
    with open(path) as f:
        return parse_key(f.read())


def parse_key(text):
    """Accepts a bare key or a `THE_ODDS_API_KEY=...` line (optionally quoted)."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, val = line.partition("=")
            if name.strip().removeprefix("export ").strip() != "THE_ODDS_API_KEY":
                continue
            line = val.strip()
        key = line.strip("'\"")
        if not re.fullmatch(r"[A-Za-z0-9]{16,128}", key):
            raise ValueError("Odds API key file present but value has an unexpected format")
        return key
    raise ValueError("no THE_ODDS_API_KEY in key file")


class Scrubber:
    def __init__(self, secret):
        self.needles = [secret, urllib.parse.quote(secret, safe="")] if secret else []

    def __call__(self, text):
        text = str(text)
        for n in self.needles:
            text = text.replace(n, "[REDACTED]")
        return text


BREAKER_SCHEMA = """
CREATE TABLE IF NOT EXISTS breaker (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  tripped INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  tripped_at REAL,
  cooldown_until REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO breaker (id, tripped, cooldown_until) VALUES (1, 0, 0);
"""


class Gateway:
    def __init__(self, cfg, key=None, clock=time.time, opener=None):
        self.cfg, self.key, self.clock = cfg, key, clock
        self.scrub = Scrubber(key)
        os.makedirs(cfg["state_dir"], exist_ok=True)
        self.conn = sqlite3.connect(os.path.join(cfg["state_dir"], "ledger.db"), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(BREAKER_SCHEMA)
        self.gate = credits.CreditGate(self.conn, credits.Budget(
            monthly_total=20000,
            v4_monthly_cap=int(cfg["v4_monthly_cap"]),
            v4_daily_cap=int(cfg["v4_daily_cap"]),
            v3_floor_remaining=int(cfg["v3_floor_remaining"]),
            max_single_request=int(cfg["max_single_request"]),
        ), clock=clock)
        self.lock = threading.Lock()
        self.opener = opener or self._https_get

    # ---------------- validation ----------------
    def build(self, req):
        ep = req.get("endpoint")
        if ep not in PATHS:
            raise Refused(400, f"endpoint not allowed: {ep!r}")
        sport = req.get("sport")
        if ep != "sports" and sport not in self.cfg["allowed_sports"]:
            raise Refused(400, f"sport not allowed: {sport!r}")
        event_id = req.get("event_id")
        if ep in NEEDS_EVENT and not (isinstance(event_id, str) and EVENT_RE.match(event_id)):
            raise Refused(400, "event_id must be 32 lowercase hex characters")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise Refused(400, "params must be an object")
        for k, v in params.items():
            if k not in ALLOWED_PARAMS:
                raise Refused(400, f"parameter not allowed: {k!r}")
            if not isinstance(v, (str, int)) or not VALUE_RE.match(str(v)):
                raise Refused(400, f"invalid value for {k!r}")
        if params.get("oddsFormat", "american") != "american":
            raise Refused(400, "oddsFormat must be american")
        if ep in NEEDS_MARKETS and "markets" not in params:
            raise Refused(400, "markets must be explicit (cost depends on it)")
        if ep in NEEDS_MARKETS and not (params.get("regions") or params.get("bookmakers")):
            raise Refused(400, "regions or bookmakers must be explicit (cost depends on it)")
        if ep.startswith("historical") and "date" not in params:
            raise Refused(400, "historical endpoints require date")
        path = PATHS[ep].format(sport=sport, event_id=event_id)
        split = lambda s: [x for x in str(s).split(",") if x] if s else []
        est = credits.estimate_cost(
            ep, split(params.get("markets")), split(params.get("regions")) or None,
            split(params.get("bookmakers")) or None, params.get("daysFrom"))
        return ep, path, {k: str(v) for k, v in params.items()}, est

    # ---------------- breaker ----------------
    def breaker(self):
        t, reason, at, cool = self.conn.execute(
            "SELECT tripped, reason, tripped_at, cooldown_until FROM breaker WHERE id=1").fetchone()
        return {"tripped": bool(t), "reason": reason, "tripped_at": at, "cooldown_until": cool}

    def _trip(self, reason):
        self.conn.execute("UPDATE breaker SET tripped=1, reason=?, tripped_at=? WHERE id=1", (reason, self.clock()))
        self.conn.commit()

    def _cooldown(self, seconds):
        self.conn.execute("UPDATE breaker SET cooldown_until=? WHERE id=1", (self.clock() + seconds,))
        self.conn.commit()

    def reset_breaker(self):
        self.conn.execute("UPDATE breaker SET tripped=0, reason=NULL, tripped_at=NULL, cooldown_until=0 WHERE id=1")
        self.conn.commit()

    # ---------------- upstream ----------------
    def _https_get(self, path, query):
        u = urllib.parse.urlsplit(self.cfg["base_url"])
        conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        kw = {"context": ssl.create_default_context()} if u.scheme == "https" else {}
        c = conn_cls(u.hostname, u.port, timeout=self.cfg["upstream_timeout_s"], **kw)
        try:
            c.request("GET", path + "?" + urllib.parse.urlencode(query), headers={"Accept": "application/json"})
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    # ---------------- main entry ----------------
    def handle(self, req):
        ep, path, params, est = self.build(req)
        purpose = str(req.get("purpose", ""))[:200]
        ttl = min(int(req.get("cache_ttl_s", 0) or 0), int(self.cfg["max_cache_ttl_s"]))
        fp_params = {"path": path, **params}
        with self.lock:
            if ttl > 0:
                hit = self.gate.cached(ep, fp_params, ttl)
                if hit is not None:
                    return 200, {"source": "cache", "est_cost": 0, "data": hit}
            if not self.cfg["live_enabled"]:
                raise Refused(503, "live Odds API calls are disabled by the owner (gateway config live_enabled=false)")
            if self.key is None:
                raise Refused(503, "no Odds API key installed")
            b = self.breaker()
            if b["tripped"]:
                raise Refused(503, f"circuit breaker tripped: {b['reason']} (owner reset required)")
            if b["cooldown_until"] > self.clock():
                raise Refused(429, "provider rate-limit cooldown in effect")
            try:
                self.gate.authorize(ep, fp_params, est)
            except credits.CreditRefused as e:
                raise Refused(402, f"credit gate: {e}")
            try:
                status, headers, raw = self.opener(path, {**params, "apiKey": self.key})
            except Exception as e:  # network failure: no headers, charge the estimate conservatively
                self.gate.record(ep, fp_params, est, 0, {})
                raise Refused(502, "upstream failure: " + self.scrub(type(e).__name__ + ": " + str(e)))
            text = self.scrub(raw.decode("utf-8", "replace"))
            body = None
            try:
                body = json.loads(text)
            except ValueError:
                pass
            note = self.gate.record(ep, fp_params, est, status, headers, body if status == 200 else None)
            if status != 200:
                code = body.get("error_code") if isinstance(body, dict) else None
                if status in (401, 403) or code in HARD_TRIP_CODES:
                    self._trip(f"HTTP {status} {code or ''}".strip())
                elif status == 429:
                    self._cooldown(self.cfg["rate_limit_cooldown_s"])
                return status, {"source": "upstream", "error": True, "provider_status": status,
                                "provider_error_code": code,
                                "provider_message": self.scrub(body.get("message") if isinstance(body, dict) else text[:300])}
            usage = {k: headers.get(k) for k in ("x-requests-used", "x-requests-remaining", "x-requests-last")}
            return 200, {"source": "upstream", "est_cost": est, "usage": usage, "cost_note": note, "data": body}

    def probe(self):
        """Owner-run key check: one /v4/sports call (documented cost 0). Bypasses
        live_enabled and the breaker on purpose so a restored key can be confirmed."""
        if self.key is None:
            raise Refused(503, "no Odds API key installed")
        with self.lock:
            self.gate.authorize("sports", {"path": PATHS["sports"]}, 0)
            try:
                status, headers, raw = self.opener(PATHS["sports"], {"apiKey": self.key})
            except Exception as e:
                raise Refused(502, "upstream failure: " + self.scrub(type(e).__name__ + ": " + str(e)))
            note = self.gate.record("sports", {"path": PATHS["sports"]}, 0, status, headers)
            out = {"provider_status": status,
                   "usage": {k: headers.get(k) for k in ("x-requests-used", "x-requests-remaining", "x-requests-last")},
                   "cost_note": note}
            if status != 200:
                try:
                    out["provider_error_code"] = json.loads(raw).get("error_code")
                except Exception:
                    pass
            return out

    def status(self):
        return {"live_enabled": self.cfg["live_enabled"], "key_loaded_in_this_process": self.key is not None,
                "breaker": self.breaker(), "usage": self.gate.usage_split(),
                "caps": {k: self.cfg[k] for k in ("v4_monthly_cap", "v4_daily_cap", "v3_floor_remaining",
                                                    "max_single_request")},
                "cost_table": credits.COST_TABLE_STATUS}


def make_handler(gw):
    class H(BaseHTTPRequestHandler):
        server_version = "arbitredge-v4-gateway"

        def address_string(self):
            return "unix"

        def log_message(self, fmt, *args):
            sys.stderr.write(gw.scrub("gateway: " + fmt % args) + "\n")

        def _send(self, status, obj):
            data = gw.scrub(json.dumps(obj)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/status":
                return self._send(200, gw.status())
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/request":
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n > 65536:
                    raise Refused(413, "request too large")
                req = json.loads(self.rfile.read(n) or b"{}")
                status, obj = gw.handle(req)
                sys.stderr.write(gw.scrub(
                    f"gateway: {req.get('endpoint')} {req.get('sport')} -> {status} "
                    f"source={obj.get('source')} est={obj.get('est_cost')} purpose={req.get('purpose', '')!r}") + "\n")
                self._send(status, obj)
            except Refused as e:
                sys.stderr.write(gw.scrub(f"gateway: refused {e.status}: {e}") + "\n")
                self._send(e.status, {"error": True, "refused": str(e)})
            except Exception as e:
                self._send(500, {"error": True, "refused": gw.scrub(f"internal error: {type(e).__name__}")})
    return H


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(cfg, key):
    gw = Gateway(cfg, key)
    sock = cfg["socket_path"]
    if os.path.exists(sock):
        os.unlink(sock)
    srv = UnixHTTPServer(sock, make_handler(gw))
    if cfg.get("socket_group"):
        import grp
        os.chown(sock, -1, grp.getgrnam(cfg["socket_group"]).gr_gid)
    os.chmod(sock, 0o660)
    sys.stderr.write(f"gateway: listening on {sock} live_enabled={cfg['live_enabled']}\n")
    srv.serve_forever()


def key_from_keychain(service, account=None):
    """macOS: read the key from the login Keychain (never printed or logged).
    Returns None if the item does not exist."""
    import subprocess
    cmd = ["/usr/bin/security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd[3:3] = ["-a", account]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return parse_key(r.stdout)


def load_key():
    """Key sources, in order: systemd credential (Linux server), macOS Keychain
    (ODDS_KEY_KEYCHAIN_SERVICE), GitHub Actions secret exposed as an env var
    whose NAME is in ODDS_KEY_ENV. The value never reaches stdout or a log."""
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir and os.path.exists(os.path.join(cred_dir, "odds_key")):
        return read_key(os.path.join(cred_dir, "odds_key"))
    svc = os.environ.get("ODDS_KEY_KEYCHAIN_SERVICE")
    if svc:
        return key_from_keychain(svc)
    env_name = os.environ.get("ODDS_KEY_ENV")          # GitHub Actions: name of the env var holding the secret
    if env_name and os.environ.get(env_name):
        return parse_key(os.environ[env_name])
    return None


def main(argv):
    cfg = load_config(os.environ.get("GATEWAY_CONFIG", "/etc/arbitredge-v4/gateway.json"))
    key = load_key()
    if argv[1:] == ["probe-local"]:    # Mac: key from the Keychain, same zero-credit /v4/sports check
        if key is None:
            print(json.dumps({"error": "no Odds API key found in the Keychain"}))
            return 1
        print(json.dumps(Gateway(cfg, key).probe(), indent=2))
        return 0
    if argv[1:] == ["reset-breaker"]:
        Gateway(cfg, key).reset_breaker()
        print("breaker reset")
        return 0
    if argv[1:] == ["probe"]:          # key arrives on stdin from the root-only admin tool
        key = parse_key(sys.stdin.read())
        print(json.dumps(Gateway(cfg, key).probe(), indent=2))
        return 0
    if argv[1:] == ["status"]:
        print(json.dumps(Gateway(cfg, key).status(), indent=2))
        return 0
    serve(cfg, key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
