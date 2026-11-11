"""
lab_v4/runner.py -- one sport-week of the PRODUCTION V4 engine, in memory, with optional research hooks.

The loop is engine.advance's loop (same arrivals, ordering, customers_v5 participation, staking, budgets, book_state
re-quote logic, wager ids) without any database write. Hooks (all off in configuration A):
  margin  (D): the book's own offers re-priced to CONFIG overround around the offer's own no-vig price (same line).
               Reference (market-maker) quotes are never touched.
  risk    (C): per-wager caps by sport/market x kickoff ramp (lower for respected customers); per game-market worst-case
               exposure tracked on every possible final score (need_v6.book.Exposure); heavy side shaded by
               shade_step per breach of the game limit (to max_shade); beyond 1.25 x the limit the book refuses
               further wagers on the heavy side until the worst case is back within 0.8 x the limit.
  price   (B): recreational acceptance falls with the price paid vs the market makers' no-vig fair at the same
               minute (need_v6.customers rule), with its own random stream (does not disturb other draws).
"""
import contextlib
import math
import random

from lab_v4.config import MARGIN, PRICE_SENSITIVITY, RISK
from need_v6.book import Exposure
from need_v6.config import kickoff_factor
from need_v6.market import normalizer
from sportsbook_v4 import engine as E
from sportsbook_v4.market import implied_probability, profit_on_win
from sportsbook_v4.facts import NoFacts
from sportsbook_v4.params import BOOK
from sportsbook_v4.util import from_epoch, seed_int, sha256_hex, to_epoch

PRICE_KEYS = {"spread": ("home_price", "away_price"), "total": ("over_price", "under_price"),
              "moneyline": ("home_price", "away_price")}


def american(p):
    p = min(max(p, 0.01), 0.99)
    return float(round(-100 * p / (1 - p))) if p >= 0.5 else float(round(100 * (1 - p) / p))


def overround(offer, market):
    a, b = PRICE_KEYS[market]
    if offer is None or offer.get(a) is None or offer.get(b) is None:
        return None
    return implied_probability(offer[a]) + implied_probability(offer[b]) - 1.0


def _reprice(offer, market, shift_a=0.0, target=None):
    """Re-price a two-way offer: keep its no-vig split (+ shift_a on side A), set the overround."""
    a, b = PRICE_KEYS[market]
    if offer.get(a) is None or offer.get(b) is None:
        return offer
    ia, ib = implied_probability(offer[a]), implied_probability(offer[b])
    qa = ia / (ia + ib)
    m = (ia + ib - 1.0) if target is None else target
    o = dict(offer)
    o[a], o[b] = american(qa + shift_a + m / 2), american(1 - qa - shift_a + m / 2)
    return o


class LabView(E._MarketView):
    def __init__(self, market, cfg):
        super().__init__(market)
        self.cfg = cfg

    def offers(self, gid, t, books=None):
        base = super().offers(gid, t, books)
        if books is not None or not self.cfg["margin"]:
            return base
        return {m: (None if o is None else _reprice(o, m, target=MARGIN["overround"])) for m, o in base.items()}


class Risk:
    """Per game-market exposure and the book's shading / closing state (configuration C)."""

    def __init__(self, sport):
        self.sport = sport
        self.expo, self.shade, self.closed, self.log = {}, {}, {}, []

    def add(self, gid, market, side, line, price, stake, t):
        k = (gid, market)
        if market == "moneyline":
            e = self.expo.setdefault(k, {"home": 0.0, "away": 0.0})
            win = profit_on_win(stake, price)
            e["home"] += -win if side == "home" else stake        # book P&L if home wins
            e["away"] += -win if side == "away" else stake
            worst = min(e.values())
            heavy = "home" if e["home"] < e["away"] else "away" if e["away"] < e["home"] else None
        else:
            e = self.expo.setdefault(k, Exposure(market))
            e.add(side, line, price, stake)
            worst = e.worst()
            heavy = e.heavy_side(E.SIDES[market])
        limit = RISK["game_limit"][self.sport]
        a = E.SIDES[market][0]
        if worst < -limit and heavy:
            d = 1 if heavy == a else -1
            s = self.shade.get(k, 0.0) + d * RISK["shade_step"]
            self.shade[k] = max(-RISK["max_shade"], min(RISK["max_shade"], s))
            if worst < -RISK["close_factor"] * limit:
                self.closed[k] = heavy
            self.log.append({"t": t, "gid": gid, "market": market, "action": "CLOSE" if self.closed.get(k) else "SHADE",
                             "heavy": heavy, "worst": round(worst, 2), "shade": self.shade[k]})
        elif k in self.closed and worst >= -RISK["reopen_factor"] * limit:
            self.closed.pop(k)
            self.log.append({"t": t, "gid": gid, "market": market, "action": "REOPEN", "worst": round(worst, 2)})

    def apply(self, gid, offers):
        out = {}
        for m, o in offers.items():
            k = (gid, m)
            if k in self.shade and self.shade[k]:
                o = _reprice(o, m, shift_a=self.shade[k])
            out[m] = o
        return out


@contextlib.contextmanager
def risk_hook(risk, seen):
    """engine._decide looks up _book_offers as a module global: wrap it (shading/closing after the re-quote logic,
    so the respected-action state keys stay those of the unshaded offer). Restored on exit."""
    orig = E._book_offers

    def wrapped(view, gid, offer_t, book_state, enabled):
        out, base = orig(view, gid, offer_t, book_state, enabled)
        out = risk.apply(gid, out) if risk else out
        seen[gid] = out                     # the offers this decision saw (for the theoretical margin)
        return out, base
    E._book_offers = wrapped
    try:
        yield
    finally:
        E._book_offers = orig


def fair_cover(view, sport, gid, market, side, line, ref_t):
    """Market makers' no-vig, no-push cover probability of `side` at `line` at ref_t (None if unavailable)."""
    ref = view.offers(gid, ref_t, books=BOOK["reference_books"]).get(market)
    if ref is None:
        return None
    if market == "moneyline":
        if ref.get("home_price") is None or ref.get("away_price") is None:
            return None
        ih, ia = implied_probability(ref["home_price"]), implied_probability(ref["away_price"])
        q = ih / (ih + ia)
        return q if side == "home" else 1 - q
    nz = normalizer(sport, market)
    if market == "spread":
        if None in (ref.get("home_spread"), ref.get("home_price"), ref.get("away_price")):
            return None
        z = nz.quote("home", ref["home_spread"], ref["home_price"], "away", -ref["home_spread"], ref["away_price"])
    else:
        if None in (ref.get("total"), ref.get("over_price"), ref.get("under_price")):
            return None
        z = nz.quote("over", ref["total"], ref["over_price"], "under", ref["total"], ref["under_price"])
    if z is None:
        return None
    pw, pp = nz.prob_at(z["mu"], side, line)
    return pw / max(1e-9, 1 - pp)


def run_week(run, pop, snap, market, cfg, seed=0, until=None, rekey=None, record=False, facts_src=None):
    """run: engine.load_run(...) dict; pop: population; snap: {bettor_index: week-open bankroll}.
    Returns (wagers, info). Configuration A with seed 0 reproduces engine.advance exactly (tested).
    rekey: with seed > 0, re-key ONLY these archetypes (others keep the production key) -- variance decomposition.
    record: also store each wager's decision-time market-maker fair probability, the customer's own decision detail
    and bettor uid (audit only; never used by any decision)."""
    from sportsbook_v4 import customers_v5
    slate = run["slate"]
    sport = slate.sport
    ws, we = to_epoch(slate.window_start), to_epoch(slate.window_end)
    until_e = we if until is None else min(until, we)
    base_key = run["rng_key"]
    alt_key = base_key if seed == 0 else sha256_hex("lab_seed", base_key, seed)

    def key_for(arch_name):
        return alt_key if (rekey is None or arch_name in rekey) else base_key
    rng_key = alt_key
    staked, stats_cache, events, own = {}, {}, [], {}
    v2 = cfg.get("v2", False)
    ind2 = {}
    if v2:
        from lab_v4 import behavior_v2 as BV2
    for b in pop["bettors"]:
        if cfg.get("pop_frac", 1.0) < 1.0 and \
                random.Random(seed_int("v4b2_subsample", b["bettor_uid"])).random() >= cfg["pop_frac"]:
            continue                                            # population-size experiment: a fixed subset
        b, arch, active, _ = customers_v5.effective(b, snap[b["bettor_index"]], ws)
        if cfg.get("volume"):
            from lab_v4 import behavior_v2 as BVV
            BVV.install_gameday_profiles()
            b, arch, active, _reg = BVV.volume(b, arch, active, sport, ws)
        if not active:
            continue
        if v2:
            b, arch, ind2[b["bettor_index"]] = BV2.adjust(b, arch, cfg.get("profile"))
        own[b["bettor_index"]] = arch
        for seq, t in enumerate(E.bettor_arrivals(key_for(b["archetype"]), b, ws, we, stats_cache, arch)):
            if ws < t <= until_e:
                events.append((t, b["bettor_index"], seq, b))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    view = LabView(market, cfg)
    risk = Risk(sport) if cfg["risk"] else None
    book_state, passes, wagers = {}, {}, []
    games = sorted(slate.games, key=lambda g: g.canonical_game_id)
    kick = {g.canonical_game_id: to_epoch(g.commence_time) for g in games}
    gran = BOOK["decision_time_granularity_seconds"]
    seen = {}
    info_passes = {}
    facts = facts_src if (v2 and facts_src is not None) else NoFacts()
    nh = BV2.narrative_hook(E) if v2 else contextlib.nullcontext()
    with risk_hook(risk, seen), sharp_hook(cfg.get("sharp_ev", False), sport), nh:
        for t, _, seq, b in events:
            arch = own[b["bettor_index"]]
            rk = key_for(b["archetype"])
            acc, reason = E._decide(rk, b, seq, t, games, view, facts, staked, book_state, arch=arch)
            if acc is not None:
                acc["overround"] = overround(seen.get(acc["game"].canonical_game_id, {}).get(acc["market"]), acc["market"])
            if acc is not None and cfg["price"] and not arch["is_sharp"]:
                acc, reason = _price_gate(acc, b, arch, view, sport, t - (t % gran), rk, seq)
            if acc is not None and v2 and not arch["is_sharp"]:
                acc, reason = _v2_gate(acc, b, arch, ind2[b["bettor_index"]], view, sport, t - (t % gran), rk, seq,
                                       staked)
            if acc is not None and cfg.get("max_win"):
                acc, reason = max_win_cap(acc, b["archetype"])
            if acc is not None and cfg["risk"]:
                if risk.closed.get((acc["game"].canonical_game_id, acc["market"])) == acc["side"]:
                    acc, reason = None, "limit_side_closed"     # the book refuses the heavy side (prices unchanged)
                else:
                    acc, reason = _stake_cap(acc, sport, arch, kick, t)
            if acc is None:
                passes[reason] = passes.get(reason, 0) + 1
                if record:
                    pa = info_passes.setdefault(b["archetype"], {})
                    pa[reason] = pa.get(reason, 0) + 1
                continue
            staked[b["bettor_index"]] = staked.get(b["bettor_index"], 0.0) + acc["stake"]
            if "book_base" in acc["detail"]:
                book_state[(acc["game"].canonical_game_id, acc["market"])] = acc["detail"]["book_base"]
            g = acc["game"]
            if risk:
                risk.add(g.canonical_game_id, acc["market"], acc["side"], acc["line"], acc["price"], acc["stake"], t)
            wagers.append({
                "wager_id": sha256_hex("v4_wager", run["run_id"], b["bettor_uid"], seq)[:40],
                "bettor_index": b["bettor_index"], "archetype": b["archetype"], "arrival_seq": seq,
                "canonical_game_id": g.canonical_game_id, "sport": sport, "market": acc["market"], "side": acc["side"],
                "accepted_line": acc["line"], "accepted_home_spread": acc["home_spread"], "accepted_price": acc["price"],
                "stake": acc["stake"], "accepted_at": from_epoch(t), "t": t, "respected": bool(arch["is_sharp"]),
                "book_mode": acc["detail"].get("book_mode"), "overround": acc.get("overround"),
                "p_fair": acc.get("p_fair")})
            if record:
                d = acc["detail"]
                wagers[-1].update(
                    bettor_uid=b["bettor_uid"], bankroll=snap[b["bettor_index"]],
                    p_fair_decision=fair_cover(view, sport, g.canonical_game_id, acc["market"], acc["side"], acc["line"],
                                               t - (t % gran)),
                    edge_points=d.get("edge_points"), edge_prob=d.get("edge_prob"), edge_units=d.get("edge_units"),
                    p_side=d.get("p_home") if acc["side"] == "home" else (1 - d["p_home"]) if "p_home" in d and acc["side"] == "away"
                    else d.get("p_over") if acc["side"] == "over" else (1 - d["p_over"]) if "p_over" in d else None)
    info = {"passes": passes, "events": len(events), "risk_log": risk.log if risk else [],
            "reference_mode_wagers": sum(1 for w in wagers if w["book_mode"] == "reference"),
            "passes_by_archetype": info_passes, "active_by_archetype": dict(__import__("collections").Counter(
                b["archetype"] for b in pop["bettors"] if b["bettor_index"] in own))}
    return wagers, info


def _v2_gate(acc, b, arch, ind, view, sport, ref_t, rng_key, seq, staked):
    """V4b2: underdog-size participation, price-aware acceptance, impulse stakes (own random streams)."""
    from lab_v4 import behavior_v2 as BV2
    rng = random.Random(seed_int("v4b2_gate", rng_key, b["bettor_uid"], seq))
    u_dog, u_price, u_imp, u_mult = rng.random(), rng.random(), rng.random(), rng.random()
    g, m, side = acc["game"], acc["market"], acc["side"]
    name = b["archetype"]
    is_dog = (m == "spread" and (acc["line"] or 0) > 0) or (m == "moneyline" and acc["price"] > 0)
    if name == "UNDERDOG_CONTRARIAN" and is_dog and u_dog >= BV2.dog_participation(m, acc):
        return None, "dog_too_small"
    pf = fair_cover(view, sport, g.canonical_game_id, m, side, acc["line"], ref_t)
    ev = None if pf is None else pf * profit_on_win(1.0, acc["price"]) - (1 - pf)
    loyal = b["traits"].get("loyal_team")
    own = loyal is not None and m != "total" and loyal == (g.home_team_id if side == "home" else g.away_team_id)
    ok, reason = BV2.price_accept(name, ind, ev, own, u_price)
    if not ok:
        return None, reason
    mult = BV2.impulse_multiplier(ind, u_imp, u_mult) if name != "SELECTIVE_PRICE_SENSITIVE" else 1.0
    if mult > 1.0:
        limit = BOOK["max_wager_by_archetype"].get(name, BOOK["max_wager_default"])
        budget = b["bankroll"] * arch["weekly_risk_fraction"] - staked.get(b["bettor_index"], 0.0)
        acc["stake"] = float(math.floor(max(acc["stake"], min(acc["stake"] * mult, limit, budget))))
        acc["detail"]["impulse_mult"] = round(mult, 3)
    acc["detail"]["ev_at_maker_fair"] = None if ev is None else round(ev, 5)
    if pf is not None:
        acc["p_fair"] = round(pf, 5)
    return acc, "accepted"


def _price_gate(acc, b, arch, view, sport, ref_t, rng_key, seq):
    g = acc["game"]
    pf = fair_cover(view, sport, g.canonical_game_id, acc["market"], acc["side"], acc["line"], ref_t)
    if pf is None:
        return acc, "accepted"                      # no market-maker fair at this minute: behaviour unchanged
    d = acc["detail"]
    if "p_over" in d:
        p_side = d["p_over"] if acc["side"] == "over" else 1 - d["p_over"]
    else:
        p_side = d.get("p_home", 0.5) if acc["side"] == "home" else 1 - d.get("p_home", 0.5)
    perceived = pf + PRICE_SENSITIVITY["bias_weight"] * (p_side - 0.5) - implied_probability(acc["price"])
    tol = PRICE_SENSITIVITY["price_tolerance"]
    beta = PRICE_SENSITIVITY["price_beta"].get(b["archetype"], 25.0)
    p_acc = 1.0 if perceived >= -tol else math.exp(beta * (perceived + tol))
    if random.Random(seed_int("lab_price", rng_key, b["bettor_uid"], seq)).random() >= p_acc:
        return None, "price_not_attractive"
    acc["p_fair"] = round(pf, 5)
    return acc, "accepted"


def _stake_cap(acc, sport, arch, kick, t):
    m = acc["market"]
    lim = RISK["wager_limit"][sport]["spread" if m == "moneyline" else m]
    hours = (kick[acc["game"].canonical_game_id] - t) / 3600.0
    cap = lim * kickoff_factor(hours) * (RISK["respected_limit_factor"] if arch["is_sharp"] else 1.0)
    stake = float(math.floor(min(acc["stake"], cap)))
    if stake < BOOK["min_wager"]:
        return None, "limit_below_minimum"
    acc["stake"] = stake
    return acc, "accepted"


# ------------------------------------------------------------------------------------------------ realism corrections (v4r1)
def _devig(ph, pa, method):
    a, b = implied_probability(ph), implied_probability(pa)
    if method == "proportional":
        return a / (a + b)
    lo, hi = 1.0, 5.0                                    # power de-vig: a^k + b^k = 1
    for _ in range(50):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if a ** k + b ** k > 1 else (lo, k)
    return a ** k


def sharp_pick_ev(arch, open_games, view, ref_t, sport):
    """CUSTOMER BEHAVIOUR CORRECTION. A respected customer bets the side whose BOOK price has the highest expected
    value against the market makers' no-vig fair at the SAME minute (no later information), and only if that EV is
    at least arch["edge_threshold_prob"] (V4's own 1.5% threshold, now applied to EV instead of raw points).
    Spreads/totals: push- and key-number-aware normalisation (need_v6). Moneyline: EV must clear the threshold under
    BOTH proportional and power de-vig (robust to the de-vig model). Same return shape as engine._sharp_pick."""
    best = None
    thr = arch["edge_threshold_prob"]
    for g, offers in open_games:
        refs = view.offers(g.canonical_game_id, ref_t, books=BOOK["reference_books"])
        for market in ("spread", "total", "moneyline"):
            offer, ref = offers.get(market), refs.get(market)
            if offer is None or ref is None:
                continue
            for side in E.SIDES[market]:
                price = E._price_of(market, offer, side)
                if price is None or implied_probability(price) > E._max_prob(arch, market):
                    continue
                win = profit_on_win(1.0, price)
                if market == "moneyline":
                    if ref.get("home_price") is None or ref.get("away_price") is None:
                        continue
                    evs = []
                    for mth in ("proportional", "power"):
                        ph = _devig(ref["home_price"], ref["away_price"], mth)
                        p = ph if side == "home" else 1 - ph
                        evs.append(p * win - (1 - p))
                    ev = min(evs)
                else:
                    line = E._line_of(market, offer, side)
                    nz = normalizer(sport, market)
                    ref_mu = _ref_mu(nz, market, ref)
                    if ref_mu is None:
                        continue
                    pw, pp = nz.prob_at(ref_mu, side, line)
                    ev = pw * win - (1 - pw - pp)
                if ev < thr:
                    continue
                units = ev / thr
                key = (units, g.canonical_game_id, market, side)
                if best is None or key > best[0]:
                    best = (key, g, market, offer, side, {"edge_points": 0.0, "edge_prob": round(ev, 6),
                                                          "edge_units": round(units, 6), "ev_vs_maker_fair": round(ev, 6),
                                                          "ref_books": ref.get("books")})
    return best


def _ref_mu(nz, market, ref):
    if market == "spread":
        if None in (ref.get("home_spread"), ref.get("home_price"), ref.get("away_price")):
            return None
        z = nz.quote("home", ref["home_spread"], ref["home_price"], "away", -ref["home_spread"], ref["away_price"])
    else:
        if None in (ref.get("total"), ref.get("over_price"), ref.get("under_price")):
            return None
        z = nz.quote("over", ref["total"], ref["over_price"], "under", ref["total"], ref["under_price"])
    return None if z is None else z["mu"]


@contextlib.contextmanager
def sharp_hook(enabled, sport):
    orig = E._sharp_pick
    if enabled:
        E._sharp_pick = lambda arch, open_games, view, ref_t: sharp_pick_ev(arch, open_games, view, ref_t, sport)
    try:
        yield
    finally:
        E._sharp_pick = orig


def max_win_cap(acc, archetype):
    """RISK MANAGEMENT IMPROVEMENT. Standard 'to win' limit: a wager may not win more than the customer's existing
    V4 wager limit (10,000; SHARP 3,000). Only plus-money prices are affected (a minus price never wins more than
    the stake)."""
    limit = BOOK["max_wager_by_archetype"].get(archetype, BOOK["max_wager_default"])
    if acc["price"] <= 0:
        return acc, "accepted"
    cap = limit * 100.0 / acc["price"]
    if acc["stake"] > cap:
        stake = float(math.floor(cap))
        if stake < BOOK["min_wager"]:
            return None, "max_win_limit"
        acc["stake"] = stake
        acc["detail"]["max_win_capped"] = True
    return acc, "accepted"
