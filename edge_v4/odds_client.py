"""
edge_v4/odds_client.py -- the only way V4 code requests Odds API data.

Talks to the local gateway over its Unix socket. V4 never holds the API
key; the gateway enforces the credit budget. Responses carry provenance
(source=upstream|cache, estimated cost, provider usage headers).
"""

import http.client
import json
import socket

DEFAULT_SOCKET = "/run/arbitredge-v4-gateway/gateway.sock"


class GatewayRefused(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body.get('refused') or body.get('provider_message') or body}")
        self.status, self.body = status, body


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path, timeout=60):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


class OddsClient:
    def __init__(self, socket_path=DEFAULT_SOCKET, timeout=60):
        self.socket_path, self.timeout = socket_path, timeout

    def _call(self, method, path, payload=None):
        c = _UnixConn(self.socket_path, self.timeout)
        try:
            body = None if payload is None else json.dumps(payload)
            c.request(method, path, body=body, headers={"Content-Type": "application/json"})
            r = c.getresponse()
            data = json.loads(r.read() or b"{}")
            return r.status, data
        finally:
            c.close()

    def status(self):
        return self._call("GET", "/status")[1]

    def request(self, endpoint, sport=None, params=None, event_id=None, purpose="", cache_ttl_s=0):
        st, data = self._call("POST", "/request", {
            "endpoint": endpoint, "sport": sport, "event_id": event_id,
            "params": params or {}, "purpose": purpose, "cache_ttl_s": cache_ttl_s})
        if st != 200:
            raise GatewayRefused(st, data)
        return data
