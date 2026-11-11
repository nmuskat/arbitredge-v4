"""
need_v6/customers.py -- what one synthetic customer does at one arrival, against the DESK's prices.

Reused unchanged from the live engine (sportsbook_v4.engine): game choice (popularity, primetime, national TV,
loyalty, kickoff proximity), market choice (individual mix), side preference (favourite/home/popularity/recency/
loyalty utility; over preference), conviction threshold, bankroll-based stake with lognormal noise, weekly budget.

New in v6:
  * PRICE SENSITIVITY (recreational): perceived value = fair win prob at the offered number + bias_weight x
    (own preference - 0.5) - implied prob of the offered price. Acceptance falls as the price worsens
    (CONFIG price_tolerance, price_beta per archetype). All else equal, a worse price -> fewer bets on that side.
  * SHARPS bet only when the desk's price is +EV against the market makers' no-vig fair at the SAME observation the
    desk has (no fetch-cadence artefact; moves between our fetches are unknown and never invented).
  * LIMITS: per-wager cap by sport/market x kickoff ramp; respected customers capped lower; closed sides refused.
"""

import math
import random

from need_v6.config import CONFIG, kickoff_factor
from sportsbook_v4 import engine as E
from sportsbook_v4.market import profit_on_win
from sportsbook_v4.params import BOOK
from sportsbook_v4.util import seed_int, to_epoch


def implied(price):
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


def _offer_dict(market, offers):
    """Desk offers -> the shape the live engine's preference code expects."""
    a, b = ("home", "away") if market == "spread" else ("over", "under")
    if a not in offers and b not in offers:
        return None
    pa = offers.get(a) or (None, None)
    pb = offers.get(b) or (None, None)
    if market == "spread":
        line = pa[0] if pa[0] is not None else -pb[0]
        return {"home_spread": line, "home_price": pa[1], "away_price": pb[1]}
    line = pa[0] if pa[0] is not None else pb[0]
    return {"total": line, "over_price": pa[1], "under_price": pb[1]}


def stake_cap(sport, market, hours_to_kick, respected):
    cap = CONFIG["wager_limit"][sport][market] * kickoff_factor(hours_to_kick)
    return cap * (CONFIG["respected_limit_factor"] if respected else 1.0)


def decide(rng_key, bettor, arch, seq, t, sport, open_games, facts_source, staked):
    """open_games: [(GameSpec, {market: {"desk": Desk, "gm": GameMarket, "ms": state, "offers": {side: (pt, price)}}})].
    Returns (wager dict | None, reason)."""
    rng = random.Random(seed_int("v6_decide", rng_key, bettor["bettor_uid"], seq))
    tr = bettor["traits"]
    if not open_games:
        return None, "no_open_market"
    if arch["is_sharp"]:
        best = None
        for g, mkts in open_games:
            for m, v in mkts.items():
                mu = v["ms"].get("fair_mu")
                if mu is None:
                    continue
                for side, (pt, price) in v["offers"].items():
                    pw, pp = v["gm"].fair_prob(mu, side, pt)
                    win = profit_on_win(1.0, price)
                    ev = pw * win - (1 - pw - pp)
                    if ev >= CONFIG["sharp_min_ev"] and (best is None or (ev, g.canonical_game_id, m, side) > best[0]):
                        best = ((ev, g.canonical_game_id, m, side), g, m, side, pt, price, ev)
        if best is None:
            return None, "no_edge"
        _, game, market, side, point, price, ev = best
        stake_mult = 1.0 + arch["edge_stake_multiplier"] * max(0.0, ev / CONFIG["sharp_min_ev"] - 1.0)
        detail = {"ev_vs_fair": round(ev, 5)}
    else:
        avail = [(g, {m: _offer_dict(m, v["offers"]) for m, v in mkts.items() if _offer_dict(m, v["offers"])})
                 for g, mkts in open_games]
        avail = [(g, o) for g, o in avail if o]
        if not avail:
            return None, "no_open_market"
        game, offers = E._choose_game(rng, bettor, arch, avail, t)
        market = E._choose_market(rng, arch, offers)
        if market is None:
            return None, "no_open_market"
        offer = offers[market]
        facts = facts_source.facts(game.canonical_game_id, t - (t % 3600))
        side, p_side, detail = E._recreational_pick(rng, bettor, arch, game, market, offer, facts)
        if p_side < arch["conviction_threshold"]:
            return None, "low_conviction"
        v = dict(open_games)[game][market]
        if side not in v["offers"]:
            return None, "limit_side_closed"
        point, price = v["offers"][side]
        mu = v["ms"].get("fair_mu")
        if mu is None:
            return None, "no_fair_price"
        pw, pp = v["gm"].fair_prob(mu, side, point)
        perceived = pw / max(1e-9, 1 - pp) + CONFIG["bias_weight"] * (p_side - 0.5) - implied(price)
        beta = CONFIG["price_beta"].get(bettor["archetype"], 25.0)
        p_acc = 1.0 if perceived >= -CONFIG["price_tolerance"] else math.exp(beta * (perceived + CONFIG["price_tolerance"]))
        if rng.random() >= p_acc:
            return None, "price_not_attractive"
        if implied(price) > E._max_prob(arch, market):
            return None, "price_too_expensive"
        stake_mult = 1.0
        detail.update({"perceived_value": round(perceived, 5), "p_accept": round(p_acc, 5)})
    hours = (to_epoch(game.commence_time) - t) / 3600.0
    raw = bettor["bankroll"] * tr["unit_fraction"] * math.exp(arch["stake_log_sd"] * rng.gauss(0.0, 1.0)) * stake_mult
    cap = stake_cap(sport, market, hours, arch["is_sharp"])
    budget = bettor["bankroll"] * arch["weekly_risk_fraction"] - staked.get(bettor["bettor_index"], 0.0)
    stake = float(math.floor(min(raw, cap, budget)))
    if stake < BOOK["min_wager"]:
        return None, "budget_or_minimum"
    detail.update({"cap": round(cap, 2), "raw_stake": round(raw, 2)})
    return {"game": game, "market": market, "side": side, "point": point, "price": price, "stake": stake,
            "respected": bool(arch["is_sharp"]), "detail": detail}, "accepted"
