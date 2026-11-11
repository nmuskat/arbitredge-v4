"""
need_v6/engine.py -- chronological, event-driven simulation of one sport-week with the trading desk.

Events, in time order (desk observations before arrivals at the same second):
  * OBSERVATION: a new collected quote set for a game-market -> desk.on_observation (move/defend/balance/position)
  * ARRIVAL: a synthetic customer arrives (the live engine's deterministic arrival clocks) -> customers.decide
    against the desk's CURRENT prices -> accepted wager recorded at the offered terms -> exposure -> desk.on_wager
Nothing at time t can see quotes fetched after t, wagers after t, or any result (results are not an input).
Deterministic: same inputs + same CONFIG -> identical wagers, decisions and cards (tested).
Freeze: the card at the last desk decision at or before kickoff - FREEZE_BEFORE_S is the final pre-game NEED.
"""

import heapq

from need_v6 import VERSION
from need_v6 import customers as C
from need_v6.book import Exposure
from need_v6.config import CONFIG, CONFIG_HASH
from need_v6.desk import Desk
from need_v6.market import GameMarket
from sportsbook_v4 import engine as E
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.util import from_epoch, sha256_hex, to_epoch

FREEZE_BEFORE_S = 30 * 60


def simulate(conn, sport, games, customers, ws, we, rng_key, until=None, card_fn=None):
    """games: [GameSpec]; customers: [(customer_view, arch)] from customers_v5.effective (active only).
    Returns dict(wagers, decisions, cards_latest, cards_frozen, passes)."""
    until = we if until is None else min(until, we)
    gms, desks, expo = {}, {}, {}
    events = []
    for g in games:
        kick = to_epoch(g.commence_time)
        for m in CONFIG["markets"]:
            gm = GameMarket(conn, sport, g.canonical_game_id, m, kick)
            k = (g.canonical_game_id, m)
            gms[k], desks[k], expo[k] = gm, Desk(sport, m, gm), Exposure(m)
            for t in gm.times:
                if ws <= t <= until and t < kick:
                    events.append((t, 0, k[0], m, None))
    stats_cache = {}
    for cust, arch in customers:
        for seq, t in enumerate(E.bettor_arrivals(rng_key, cust, ws, we, stats_cache, arch)):
            if t <= until:
                events.append((t, 1, cust["bettor_index"], seq, (cust, arch)))
    events.sort(key=lambda e: (e[0], e[1], str(e[2]), e[3] if isinstance(e[3], int) else 0))
    by_game = {g.canonical_game_id: g for g in games}
    facts = NoFacts()
    staked, wagers, passes = {}, [], {}
    by_k = {k: [] for k in desks}
    frozen, latest = {}, {}

    def card(k, t):
        if card_fn is None:
            return None
        return card_fn(k, t, gms[k], desks[k], expo[k], by_k[k], by_game[k[0]])

    for t, kind, a, b, payload in events:
        if kind == 0:
            k = (a, b)
            g = by_game[a]
            kick = to_epoch(g.commence_time)
            desks[k].on_observation(t, gms[k].state(t), expo[k], kick)
            if t <= kick - FREEZE_BEFORE_S:
                frozen[k] = card(k, t)
            continue
        cust, arch = payload
        open_games = []
        for g in games:
            kick = to_epoch(g.commence_time)
            if t >= kick:
                continue
            mk = {}
            for m in CONFIG["markets"]:
                k = (g.canonical_game_id, m)
                ms = gms[k].state(t)
                if ms.get("status") != "OK":
                    continue
                offers = desks[k].offers(ms["fair_mu"])
                if offers:
                    mk[m] = {"desk": desks[k], "gm": gms[k], "ms": ms, "offers": offers}
            if mk:
                open_games.append((g, mk))
        acc, reason = C.decide(rng_key, cust, arch, b, t, sport, open_games, facts, staked)
        if acc is None:
            passes[reason] = passes.get(reason, 0) + 1
            continue
        g = acc["game"]
        k = (g.canonical_game_id, acc["market"])
        staked[cust["bettor_index"]] = staked.get(cust["bettor_index"], 0.0) + acc["stake"]
        expo[k].add(acc["side"], acc["point"], acc["price"], acc["stake"])
        w = {"wager_id": sha256_hex(VERSION, rng_key, cust["bettor_uid"], b)[:40], "bettor_index": cust["bettor_index"],
             "bettor_uid": cust["bettor_uid"], "archetype": cust["archetype"], "event_id": g.canonical_game_id,
             "sport": sport, "market": acc["market"], "side": acc["side"],
             "accepted_line": float(acc["point"]),
             "accepted_home_spread": (float(acc["point"]) if acc["side"] == "home" else -float(acc["point"]))
             if acc["market"] == "spread" else None,
             "accepted_price": float(acc["price"]), "stake": acc["stake"], "accepted_at": from_epoch(t),
             "respected": acc["respected"], "detail": acc["detail"]}
        wagers.append(w)
        by_k[k].append(w)
        desks[k].on_wager(t, acc["side"], acc["respected"], gms[k].state(t), expo[k])
    for k in desks:
        latest[k] = card(k, until)
    return {"version": VERSION, "config_hash": CONFIG_HASH, "wagers": wagers, "passes": passes,
            "decisions": {f"{k[0]}|{k[1]}": d.log for k, d in desks.items()},
            "cards_latest": latest, "cards_frozen": frozen, "until": from_epoch(until)}
