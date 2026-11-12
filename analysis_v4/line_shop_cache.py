"""
analysis_v4/line_shop_cache.py -- incremental cache for the season-to-date line-shopping report.

PERFORMANCE ONLY. The report is identical with or without the cache (tests_v4/test_line_shop_cache.py
compares the two). Approved by the owner 2026-11-12 (dashboard builds had grown past 10 minutes
because line_shop.find_opportunities re-scanned every stored snapshot on every build).

Why the scan of one game at one snapshot can be reused:
  edge_v4.forward.scan(conn, None, [game], t, ...) reads ONLY that game's quotes with fetched_at <= t
  (market_v4.asof.market_state and sharp_v4.signals.analyse are point-in-time), plus fixed code and
  parameters. The game's kickoff and team names (from the LATEST fetch, which can change later) only
  decide whether the game is scanned at t and fill the display fields kickoff/home/away; both are
  re-applied from the current store on every build, never taken from the cache.

Cache entry: key "<t>|<event_id>" -> [fingerprint, candidate rows]
  fingerprint  SHA-256 chain over EVERY quote row of that game with fetched_at <= t (all columns the
               calculation can read). A revised, added or deleted quote at or before t changes it, so
               the entry is recomputed; entries of other games or earlier times stay valid.
  rows         the scan's non-NO_BET rows with a selection, in scan order.
Whole-cache key (VERSION): cache schema + SHA-256 of the calculation's source code and NFL key-number
parameters + decision policy + freshness rule + sport. Any change -> the whole sport is recomputed.

Safety: a missing, unreadable, truncated, malformed or other-version cache is ignored (full
calculation). Every build also recomputes a random sample of reused entries; any difference discards
the cache and recomputes everything. The cache saved by a build contains only entries it verified
or computed itself, written atomically.
"""

import bisect
import gzip
import hashlib
import json
import os
import random

SCHEMA = "line_shop_cache.v1"
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
# the calculation's code: every module the scan, fair-line pricing, as-of state and Engine B read, plus params
CODE_DIRS = ("edge_v4", "market_v4", "sharp_v4", "sims_v4")
CODE_FILES = ("analysis_v4/line_shop.py", "analysis_v4/line_shop_cache.py", "sims_v4/nfl_distribution_2026.json")
SPOT_CHECK = 25                     # reused entries recomputed per sport per build
QUOTE_COLS = ("event_id", "sport", "commence", "home", "away", "book", "market", "side", "point", "price",
              "market_last_update", "fetched_at", "source", "request_fp")


def code_hash():
    h = hashlib.sha256()
    files = list(CODE_FILES)
    for d in CODE_DIRS:
        for name in sorted(os.listdir(os.path.join(_ROOT, d))):
            if name.endswith(".py"):
                files.append(f"{d}/{name}")
    for rel in sorted(set(files)):
        h.update(rel.encode() + b"\0")
        with open(os.path.join(_ROOT, rel), "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


def version(sport, policy, max_age_s):
    pol = json.dumps(policy.__dict__, sort_keys=True, default=repr)
    return hashlib.sha256(f"{SCHEMA}|{code_hash()}|{pol}|{max_age_s!r}|{sport}".encode()).hexdigest()[:32]


def fingerprints(conn, sport):
    """{event_id: ([fetched_at ascending], [chain digest after that fetch])} over every quote of the sport."""
    out, cur, h, last_fa, fas, digs = {}, None, None, None, None, None
    sql = (f"SELECT {', '.join(QUOTE_COLS)} FROM mkt_quote WHERE sport=? "
           "ORDER BY event_id, fetched_at, book, market, side, id")
    for row in conn.execute(sql, (sport,)):
        eid, fa = row[0], row[11]
        if eid != cur:
            if cur is not None:
                digs[-1] = h.hexdigest()[:32]
            cur, h, fas, digs, last_fa = eid, hashlib.sha256(), [], [], None
            out[eid] = (fas, digs)
        if fa != last_fa:
            if last_fa is not None:
                digs[-1] = h.hexdigest()[:32]
            fas.append(fa)
            digs.append(None)
            last_fa = fa
        h.update(repr(row).encode() + b"\n")
    if cur is not None:
        digs[-1] = h.hexdigest()[:32]
    return out


def fingerprint_at(fp, eid, t):
    """Digest of the game's quotes with fetched_at <= t, or None when nothing was fetched by t."""
    fas, digs = fp.get(eid, ((), ()))
    i = bisect.bisect_right(fas, t)
    return digs[i - 1] if i else None


def _valid_entry(v):
    return (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], list)
            and all(isinstance(r, dict) and "decision" in r and "selection" in r and "game_id" in r for r in v[1]))


class LineShopCache:
    """Holds the previous build's entries (read) and this build's entries (written)."""

    def __init__(self, path=None, spot_check=SPOT_CHECK, seed=None):
        self.path, self.spot_check = path, spot_check
        self.rng = random.Random(seed)
        self.prev, self.new, self.stats = {}, {}, {}
        self.load_status = "no cache file"
        if path and os.path.exists(path):
            try:
                with gzip.open(path, "rt") as fh:
                    doc = json.load(fh)
                if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("sports"), dict):
                    raise ValueError("unexpected cache schema")
                for sport, sec in doc["sports"].items():
                    if isinstance(sec, dict) and isinstance(sec.get("version"), str) and isinstance(sec.get("entries"), dict):
                        self.prev[sport] = sec
                self.load_status = "loaded"
            except (OSError, EOFError, ValueError, UnicodeDecodeError) as e:      # corrupt/truncated: full calculation
                self.prev, self.load_status = {}, f"ignored ({type(e).__name__})"

    def section(self, sport, ver):
        sec = self.prev.get(sport)
        st = self.stats.setdefault(sport, {"reused": 0, "computed": 0, "invalidated": 0, "spot_checked": 0,
                                           "status": None})
        if sec is None:
            st["status"] = "cold (" + self.load_status + ")"
            return {}
        if sec["version"] != ver:
            st["status"] = "cold (calculation version changed)"
            return {}
        st["status"] = "warm"
        return sec["entries"]

    def store(self, sport, ver, entries):
        self.new[sport] = {"version": ver, "entries": entries}

    def save(self, path=None):
        path = path or self.path
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt") as fh:
            json.dump({"schema": SCHEMA, "sports": self.new}, fh, separators=(",", ":"))
        os.replace(tmp, path)                  # atomic: a reader sees the old file or the complete new one


class SpotCheckFailed(Exception):
    pass


def candidate_rows(rows, no_bet):
    return [r for r in rows if r["decision"] != no_bet and r.get("selection") is not None]


def scan_times(conn, sport, events, policy, max_age_s, cache, scan_one, no_bet, use_prev=True):
    """[(t, candidate rows of that snapshot in scan order)] for every stored snapshot t of the sport.
    scan_one(event, t) -> forward.scan rows of one game. Equivalent to scan(conn, None, live_events, t, ...)
    filtered to candidates: scan's final ordering is a stable sort, so concatenating per-game results in
    event order and re-sorting by the same key (done by the caller) gives the same order.
    Raises SpotCheckFailed when a reused entry differs from a fresh recomputation."""
    ver = version(sport, policy, max_age_s)
    prev = cache.section(sport, ver) if use_prev else {}
    st = cache.stats[sport]
    fp = fingerprints(conn, sport)
    times = [t for (t,) in conn.execute(
        "SELECT DISTINCT fetched_at FROM mkt_fetch WHERE sport=? AND n_quotes>0 ORDER BY 1", (sport,))]
    entries, reused, result = {}, [], []
    for t in times:
        out = []
        for e in events:
            if e["kickoff"] <= t:
                continue
            d = fingerprint_at(fp, e["event_id"], t)
            if d is None:
                continue                        # no quote of this game by t: the scan yields only NO_BET rows
            key = f"{t!r}|{e['event_id']}"
            hit = prev.get(key)
            if hit is not None and _valid_entry(hit) and hit[0] == d:
                stored = hit[1]
                st["reused"] += 1
                reused.append((key, e, t))
            else:
                if hit is not None:
                    st["invalidated"] += 1
                stored = json.loads(json.dumps(candidate_rows(scan_one(e, t), no_bet)))
                st["computed"] += 1
            entries[key] = [d, stored]
            for r in stored:
                r = dict(r)
                r["home"], r["away"], r["kickoff"] = e["home"], e["away"], _iso(e["kickoff"])   # always current
                out.append(r)
        result.append((t, out))
    for key, e, t in cache.rng.sample(reused, min(cache.spot_check, len(reused))):
        st["spot_checked"] += 1
        if _canon(candidate_rows(scan_one(e, t), no_bet), e) != _canon(entries[key][1], e):
            raise SpotCheckFailed(key)
    cache.store(sport, ver, entries)
    return result


def _iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _canon(rows, e=None):
    rows = [dict(r) for r in rows]
    if e is not None:
        for r in rows:
            r["home"], r["away"], r["kickoff"] = e["home"], e["away"], _iso(e["kickoff"])
    return json.dumps(rows, sort_keys=True, default=repr)
