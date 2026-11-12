"""
collect_v4/collector.py -- decide WHEN to fetch, fetch through the gateway,
store with provenance. Designed to be invoked every few minutes
(`python -m collect_v4.run`); each invocation does at most one odds fetch
per sport, only if one is due.

Cost model (The Odds API v4, confirmed against x-requests-last on every
call): one /odds request returns EVERY upcoming game of a sport for the
requested books and markets. bookmakers= with <= 10 books counts as one
region, so 3 markets cost 3 credits per sport per fetch. /events is free and
supplies kickoff times.

Cadence (minutes between odds fetches, by time to the next kickoff):
    <= 90 min   : 10     (captures an OBSERVED closing line for every game)
    <= 6 h      : 30
    <= 48 h     : 60
    <= 7 days   : 180
    otherwise   : 720
Monthly cost at this cadence is roughly 2,000 credits per sport in season;
the gateway's caps (initial test budget, then 6,000/month) are the hard stop.
"""

import json
import os
import time

from edge_v4.odds_client import GatewayRefused
from market_v4 import normalize, store

SPORTS = ("americanfootball_nfl", "americanfootball_ncaaf")
# 10 books = one region unit. Market makers first (consensus), then the US
# recreational books where line-shopping prices would be found.
# 2026-11-12 (owner-approved): circasports replaces lowvig (a BetOnline mirror) so Circa's line moves can be
# tracked (Pinnacle & Circa tab). Same 10 books, same credits per fetch.
BOOKS = ("pinnacle", "betonlineag", "circasports", "draftkings", "fanduel", "betmgm",
         "williamhill_us", "betrivers", "espnbet", "fanatics")
MARKETS = ("h2h", "spreads", "totals")
CADENCE = ((90, 10), (360, 30), (2880, 60), (10080, 180))
EVENTS_REFRESH_S = 3600


def cadence_minutes(minutes_to_kickoff):
    if minutes_to_kickoff is None:
        return 720
    for limit, every in CADENCE:
        if minutes_to_kickoff <= limit:
            return every
    return 720


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class Collector:
    def __init__(self, client, data_dir, clock=time.time, sports=SPORTS, books=BOOKS, markets=MARKETS):
        self.client, self.clock = client, clock
        self.sports, self.books, self.markets = sports, books, markets
        os.makedirs(data_dir, exist_ok=True)
        self.conn = store.connect(os.path.join(data_dir, "market_live.db"))
        self.state_path = os.path.join(data_dir, "collector_state.json")
        self.log_path = os.path.join(data_dir, "collector_log.jsonl")
        self.state = json.load(open(self.state_path)) if os.path.exists(self.state_path) else {}

    # ---------------------------------------------------------------- helpers
    def _save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2, sort_keys=True)
        os.replace(tmp, self.state_path)

    def _log(self, **rec):
        rec["at"] = _iso(self.clock())
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def refresh_events(self, sport):
        st = self.state.setdefault(sport, {})
        if self.clock() - st.get("events_at", 0) < EVENTS_REFRESH_S and "kickoffs" in st:
            return st["kickoffs"]
        out = self.client.request("events", sport, {"dateFormat": "iso"}, purpose="schedule (free)",
                                  cache_ttl_s=600)
        kicks = sorted(store.iso_to_epoch(e["commence_time"]) for e in (out.get("data") or []))
        st.update(events_at=self.clock(), kickoffs=kicks)
        self._save()
        return kicks

    def next_kickoff_minutes(self, kickoffs):
        now = self.clock()
        future = [k for k in kickoffs if k > now]
        return None if not future else (future[0] - now) / 60.0

    def due(self, sport):
        kicks = self.refresh_events(sport)
        mins = self.next_kickoff_minutes(kicks)
        every = cadence_minutes(mins)
        last = self.state.get(sport, {}).get("odds_at", 0)
        return (self.clock() - last) >= every * 60 - 30, mins, every

    # ---------------------------------------------------------------- main
    def fetch_odds(self, sport):
        params = {"bookmakers": ",".join(self.books), "markets": ",".join(self.markets),
                  "oddsFormat": "american", "dateFormat": "iso"}
        out = self.client.request("odds", sport, params, purpose="line-shop collection")
        fetched = self.clock()
        quotes, anomalies = normalize.normalize(out.get("data") or [], _iso(fetched), request_fp=f"odds:{sport}")
        # pre-game only: in-play prices are a different market
        quotes = [q for q in quotes if store.iso_to_epoch(q.commence_time) > fetched]
        n = store.ingest(self.conn, quotes, anomalies, _iso(fetched), sport, "odds", f"odds:{sport}")
        self.state.setdefault(sport, {})["odds_at"] = fetched
        self._save()
        books_seen = sorted({q.book for q in quotes})
        self._log(event="odds", sport=sport, quotes=n, anomalies=len(anomalies), books=books_seen,
                  usage=out.get("usage"), est_cost=out.get("est_cost"), cost_note=out.get("cost_note"))
        return {"sport": sport, "quotes": n, "books": books_seen, "usage": out.get("usage")}

    def run_once(self):
        results = []
        for sport in self.sports:
            try:
                is_due, mins, every = self.due(sport)
                if not is_due:
                    results.append({"sport": sport, "skipped": f"not due (every {every} min)"})
                    continue
                results.append(self.fetch_odds(sport))
            except GatewayRefused as e:
                self._log(event="refused", sport=sport, status=e.status, detail=str(e))
                results.append({"sport": sport, "refused": str(e), "status": e.status})
        return results
