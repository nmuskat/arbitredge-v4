"""
sportsbook_v4/engine.py -- the event-sourced synthetic sportsbook clock.

LIFECYCLE
---------
create_run()  fixes the run identity: population, slate (games + betting
              window), params hash, engine version, market source.
advance()     moves the run's clock from its watermark to `until`, inside
              ONE transaction:
                1. every bettor's arrival times over the whole window are a
                   pure function of (run_id, bettor_uid) -- so the arrivals
                   falling in (watermark, until] are identical however the
                   window is chopped into intervals;
                2. each arrival decides using ONLY the market as of its own
                   timestamp (offered number = representative market as of
                   t - offer_lag; reference market as of t) and facts as of
                   the start of its hour;
                3. accepted wagers are INSERTed (append-only table) together
                   with the interval row that moves the watermark;
                4. COMMIT -- or ROLLBACK of everything on any error.

GUARANTEES (each has a test in tests_v4/)
  * A committed wager is never recomputed, repriced or re-sided.
  * Re-advancing to a time <= watermark is a no-op (idempotent).
  * advance(a->c) == advance(a->b) + advance(b->c), wager for wager.
  * The clock cannot pass `data_complete_through` (no simulating ahead of
    the market data that will later exist).
  * A run refuses to continue under a different params hash or engine
    version: model upgrades create NEW runs, never rewrite old ones.
  * advance() is the ONLY function in V4 that writes wagers; nothing in
    ledger_read.py / decision.py writes.
"""

import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sportsbook_v4 import ENGINE_VERSION
from sportsbook_v4.facts import recency_signal
from sportsbook_v4.market import implied_probability, representative_offer
from sportsbook_v4.params import ARCHETYPES, BOOK, TIMING_PROFILES, params_hash
from sportsbook_v4.population import load_population, team_popularity
from sportsbook_v4.util import (
    ISO_FMT, canonical_json, from_epoch, json_hash, seed_int, sha256_hex, to_epoch,
)

CENTRAL = ZoneInfo("America/Chicago")


class ClockError(RuntimeError):
    pass


class RunMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class GameSpec:
    canonical_game_id: str
    home_team_id: str
    away_team_id: str
    commence_time: str
    standalone: bool = False
    national_tv: bool = False


@dataclass(frozen=True)
class SlateSpec:
    sport: str
    slate_id: str
    window_start: str
    window_end: str
    games: tuple


# --------------------------------------------------------------------- run

def _games_payload(slate):
    return [asdict(g) for g in sorted(slate.games, key=lambda g: g.canonical_game_id)]


def compute_run_id(population_id, slate, market_source_id):
    return sha256_hex(
        "v4_run", ENGINE_VERSION, population_id, params_hash(), slate.sport, slate.slate_id,
        slate.window_start, slate.window_end, canonical_json(_games_payload(slate)), market_source_id,
    )


def create_run(conn, population_id, slate, market):
    pop = conn.execute("SELECT sport FROM v4_population WHERE population_id=?", (population_id,)).fetchone()
    if pop is None:
        raise KeyError(population_id)
    if pop[0] != slate.sport:
        raise RunMismatch(f"population sport {pop[0]!r} != slate sport {slate.sport!r}")
    ws, we = to_epoch(slate.window_start), to_epoch(slate.window_end)
    if ws >= we:
        raise ValueError("window_start must precede window_end")
    for g in slate.games:
        if to_epoch(g.commence_time) > we:
            raise ValueError(f"{g.canonical_game_id} kicks off after window_end")
    run_id = compute_run_id(population_id, slate, market.source_id)
    if conn.execute("SELECT 1 FROM v4_run WHERE run_id=?", (run_id,)).fetchone() is None:
        conn.execute(
            "INSERT INTO v4_run (run_id, population_id, sport, slate_id, window_start, window_end, games_json, "
            "engine_version, params_hash, market_source_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, population_id, slate.sport, slate.slate_id, from_epoch(ws), from_epoch(we),
             canonical_json(_games_payload(slate)), ENGINE_VERSION, params_hash(), market.source_id,
             datetime.now(timezone.utc).strftime(ISO_FMT)),
        )
        conn.commit()
    return run_id


def load_run(conn, run_id):
    row = conn.execute(
        "SELECT population_id, sport, slate_id, window_start, window_end, games_json, engine_version, "
        "params_hash, market_source_id FROM v4_run WHERE run_id=?", (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(run_id)
    pid, sport, slate_id, ws, we, gj, ev, ph, msid = row
    games = tuple(GameSpec(**g) for g in json.loads(gj))
    return {
        "run_id": run_id, "population_id": pid, "engine_version": ev, "params_hash": ph,
        "market_source_id": msid,
        "slate": SlateSpec(sport=sport, slate_id=slate_id, window_start=ws, window_end=we, games=games),
    }


def watermark(conn, run_id):
    row = conn.execute(
        "SELECT seq, interval_end FROM v4_interval WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,),
    ).fetchone()
    if row is None:
        start = conn.execute("SELECT window_start FROM v4_run WHERE run_id=?", (run_id,)).fetchone()[0]
        return -1, to_epoch(start)
    return row[0], to_epoch(row[1])


# ----------------------------------------------------------------- arrivals

def _density(profile, epoch):
    local = datetime.fromtimestamp(epoch, CENTRAL)
    hour_w = next(w for a, b, w in profile["hours"] if a <= local.hour < b)
    return profile["weekday"][local.weekday()] * hour_w


def _profile_stats(profile_name, ws, we):
    profile = TIMING_PROFILES[profile_name]
    samples = [_density(profile, t) for t in range(ws, we, 3600)] or [_density(profile, ws)]
    dmax = max(profile["weekday"]) * max(w for _, _, w in profile["hours"])
    return profile, sum(samples) / len(samples), dmax


def bettor_arrivals(run_id, bettor, ws, we, stats_cache):
    """Deterministic arrival epochs for one bettor over the whole window."""
    arch = ARCHETYPES[bettor["archetype"]]
    if arch["timing"] not in stats_cache:
        stats_cache[arch["timing"]] = _profile_stats(arch["timing"], ws, we)
    profile, mean_d, dmax = stats_cache[arch["timing"]]
    window = we - ws
    expected = bettor["traits"]["arrivals_per_week"] * window / (7 * 86400)
    if expected <= 0 or mean_d <= 0:
        return []
    lam_max = expected * dmax / (mean_d * window)  # per second, thinning envelope
    rng = random.Random(seed_int("v4_arrivals", run_id, bettor["bettor_uid"]))
    out, t = [], float(ws)
    while True:
        t += rng.expovariate(lam_max)
        if t >= we:
            return out
        if rng.random() < _density(profile, int(t)) / dmax:
            out.append(int(t))


# ----------------------------------------------------------------- decisions

class _MarketView:
    """Per-advance memo of point-in-time market lookups (never looks past t)."""

    def __init__(self, market):
        self.market = market
        self._offers, self._quotes = {}, {}

    def quotes(self, gid, t):
        key = (gid, t)
        if key not in self._quotes:
            self._quotes[key] = self.market.latest_quotes(gid, t)
        return self._quotes[key]

    def offer(self, gid, t, books=None):
        key = (gid, t, tuple(books or ()))
        if key not in self._offers:
            self._offers[key] = representative_offer(self.quotes(gid, t), books=books)
        return self._offers[key]


def _floor(t, g):
    return t - (t % g)


def _side_line(offer_home_spread, side):
    return offer_home_spread if side == "home" else -offer_home_spread


def _side_price(offer, side):
    return offer["home_price"] if side == "home" else offer["away_price"]


def _best_alternative(quotes, side):
    """Best line for `side` across all books, and the cheapest price at that line."""
    best_line, best_prob = None, None
    for q in quotes:
        price = q.home_price if side == "home" else q.away_price
        try:
            prob = implied_probability(price)
        except Exception:
            continue
        line = _side_line(float(q.home_spread), side)
        if best_line is None or line > best_line or (line == best_line and prob < best_prob):
            best_line, best_prob = line, prob
    return best_line, best_prob


def _choose_game(rng, bettor, arch, open_games, t):
    tr = bettor["traits"]
    weights = []
    for g, _offer in open_games:
        w = 1.0
        w *= 1.0 + arch["primetime_weight"] * (1 if g.standalone else 0)
        w *= 1.0 + arch["national_tv_weight"] * (1 if g.national_tv else 0)
        pw = max(0.0, tr["popularity_weight"])
        w *= (team_popularity(g.home_team_id) * team_popularity(g.away_team_id)) ** (0.5 * pw)
        if tr.get("loyal_team") in (g.home_team_id, g.away_team_id):
            w *= arch.get("own_team_game_weight", 1.0)
        hours = (to_epoch(g.commence_time) - t) / 3600.0
        w *= math.exp(-hours / arch["kickoff_proximity_hours"])
        weights.append(max(w, 1e-12))
    target = rng.random() * sum(weights)
    acc = 0.0
    for (g, offer), w in zip(open_games, weights):
        acc += w
        if target < acc:
            return g, offer
    return open_games[-1]


def _recreational_side(rng, bettor, arch, game, offer, facts):
    tr = bettor["traits"]
    hs = offer["home_spread"]
    fav_sign = 1.0 if hs < 0 else (-1.0 if hs > 0 else 0.0)
    loyal = tr.get("loyal_team")
    loyalty = arch["loyalty"] * (1.0 if loyal == game.home_team_id else -1.0 if loyal == game.away_team_id else 0.0)
    # Every term is antisymmetric under a home/away swap except home_pref,
    # whose population mean is 0.0 in the active params (validated).
    u = (
        tr["fav_pref"] * fav_sign
        + tr["popularity_weight"] * (math.log(team_popularity(game.home_team_id))
                                     - math.log(team_popularity(game.away_team_id)))
        + tr["recency_weight"] * (recency_signal(facts, "home") - recency_signal(facts, "away"))
        + loyalty
        + tr["home_pref"]
    )
    p_home = 1.0 / (1.0 + math.exp(-u))
    side = "home" if rng.random() < p_home else "away"
    return side, {"u": round(u, 6), "p_home": round(p_home, 6)}


def _sharp_pick(arch, open_games, view, ref_t):
    """Best (game, side) where the book's number is worse than the reference market."""
    best = None
    for g, offer in open_games:
        ref = view.offer(g.canonical_game_id, ref_t, books=BOOK["reference_books"])
        if ref is None:
            continue
        for side in ("home", "away"):
            price = _side_price(offer, side)
            ref_price = _side_price(ref, side)
            if price is None:
                continue
            pts = _side_line(offer["home_spread"], side) - _side_line(ref["home_spread"], side)
            prob_edge = 0.0
            if pts == 0 and ref_price is not None:
                prob_edge = implied_probability(ref_price) - implied_probability(price)
            qualifies = pts >= arch["edge_threshold_points"] or (pts == 0 and prob_edge >= arch["edge_threshold_prob"])
            if not qualifies or implied_probability(price) > arch["max_implied_prob"]:
                continue
            units = pts / arch["edge_threshold_points"] + prob_edge / arch["edge_threshold_prob"]
            key = (units, g.canonical_game_id, side)
            if best is None or key > best[0]:
                best = (key, g, offer, side, {"edge_points": pts, "edge_prob": round(prob_edge, 6),
                                              "edge_units": round(units, 6), "ref_home_spread": ref["home_spread"],
                                              "ref_books": ref["books"]})
    return best


def _decide(run_id, bettor, seq, t, games, view, facts_source, staked):
    arch = ARCHETYPES[bettor["archetype"]]
    tr = bettor["traits"]
    gran = BOOK["decision_time_granularity_seconds"]
    ref_t = _floor(t, gran)
    offer_t = _floor(t - BOOK["offer_lag_seconds"], gran)
    rng = random.Random(seed_int("v4_decide", run_id, bettor["bettor_uid"], seq))

    open_games = []
    for g in games:
        if t >= to_epoch(g.commence_time):
            continue
        offer = view.offer(g.canonical_game_id, offer_t)
        if offer is not None:
            open_games.append((g, offer))
    if not open_games:
        return None, "no_open_market"

    if arch["is_sharp"]:
        pick = _sharp_pick(arch, open_games, view, ref_t)
        if pick is None:
            return None, "no_edge"
        _, game, offer, side, detail = pick
        stake_mult = 1.0 + arch["edge_stake_multiplier"] * max(0.0, detail["edge_units"] - 1.0)
    else:
        game, offer = _choose_game(rng, bettor, arch, open_games, t)
        facts = facts_source.facts(game.canonical_game_id, _floor(t, 3600))
        side, detail = _recreational_side(rng, bettor, arch, game, offer, facts)
        p_side = detail["p_home"] if side == "home" else 1.0 - detail["p_home"]
        if p_side < arch["conviction_threshold"]:
            return None, "low_conviction"
        stake_mult = 1.0

    price = _side_price(offer, side)
    if price is None:
        return None, "no_price"
    prob = implied_probability(price)
    if prob > arch["max_implied_prob"]:
        return None, "price_too_expensive"
    line = _side_line(offer["home_spread"], side)
    if arch["shops"] and not arch["is_sharp"]:
        alt_line, alt_prob = _best_alternative(view.quotes(game.canonical_game_id, ref_t), side)
        if alt_line is not None:
            if alt_line - line >= arch["shop_line_tolerance"]:
                return None, "shopped_elsewhere"
            if alt_line == line and prob - alt_prob >= arch["shop_prob_tolerance"]:
                return None, "shopped_elsewhere"

    raw = bettor["bankroll"] * tr["unit_fraction"] * math.exp(arch["stake_log_sd"] * rng.gauss(0.0, 1.0)) * stake_mult
    limit = BOOK["max_wager_by_archetype"].get(bettor["archetype"], BOOK["max_wager_default"])
    budget = bettor["bankroll"] * arch["weekly_risk_fraction"] - staked.get(bettor["bettor_index"], 0.0)
    stake = float(math.floor(min(raw, limit, budget)))
    if stake < BOOK["min_wager"]:
        return None, "budget_or_minimum"

    detail.update({"p_side_threshold": arch["conviction_threshold"], "raw_stake": round(raw, 4),
                   "limit": limit, "budget_before": round(budget, 4)})
    return {
        "game": game, "side": side, "line": line, "home_spread": offer["home_spread"], "price": float(price),
        "stake": stake, "offer_t": offer_t,
        "quote_refs": {"books": offer["books"], "refs": offer["refs"], "book_count": offer["book_count"]},
        "detail": detail,
    }, "accepted"


# ------------------------------------------------------------------ advance

def advance(conn, run_id, until, market, facts, data_complete_through, _fail_after_inserts=None):
    """Advance the run clock to `until` (ISO). Returns an interval summary dict."""
    run = load_run(conn, run_id)
    if run["engine_version"] != ENGINE_VERSION or run["params_hash"] != params_hash():
        raise RunMismatch("run was created under a different engine/params version; start a new run")
    if run["market_source_id"] != market.source_id:
        raise RunMismatch(f"run expects market source {run['market_source_id']!r}, got {market.source_id!r}")
    slate = run["slate"]
    ws, we = to_epoch(slate.window_start), to_epoch(slate.window_end)
    until_e = min(to_epoch(until), we)
    if until_e > to_epoch(data_complete_through):
        raise ClockError("cannot advance past data_complete_through (market data for that time does not exist yet)")
    if conn.in_transaction:
        raise ClockError("advance() requires a connection with no open transaction")

    conn.execute("BEGIN IMMEDIATE")
    try:
        last_seq, wm = watermark(conn, run_id)
        if until_e <= wm:
            conn.rollback()
            return {"run_id": run_id, "status": "noop", "watermark": from_epoch(wm)}

        pop = load_population(conn, run["population_id"])
        staked = dict(conn.execute(
            "SELECT bettor_index, SUM(stake) FROM v4_wager WHERE run_id=? GROUP BY bettor_index", (run_id,),
        ).fetchall())
        stats_cache, events = {}, []
        for b in pop["bettors"]:
            for seq, t in enumerate(bettor_arrivals(run_id, b, ws, we, stats_cache)):
                if wm < t <= until_e:
                    events.append((t, b["bettor_index"], seq, b))
        events.sort(key=lambda e: (e[0], e[1], e[2]))

        view = _MarketView(market)
        games = sorted(slate.games, key=lambda g: g.canonical_game_id)
        new_seq = last_seq + 1
        passes, wagers = {}, []
        for t, _, seq, b in events:
            accepted, reason = _decide(run_id, b, seq, t, games, view, facts, staked)
            if accepted is None:
                passes[reason] = passes.get(reason, 0) + 1
                continue
            staked[b["bettor_index"]] = staked.get(b["bettor_index"], 0.0) + accepted["stake"]
            g = accepted["game"]
            wagers.append({
                "wager_id": sha256_hex("v4_wager", run_id, b["bettor_uid"], seq)[:40],
                "run_id": run_id, "interval_seq": new_seq, "population_id": run["population_id"],
                "bettor_index": b["bettor_index"], "bettor_uid": b["bettor_uid"], "archetype": b["archetype"],
                "arrival_seq": seq, "canonical_game_id": g.canonical_game_id, "sport": slate.sport,
                "market": "spread", "side": accepted["side"],
                "team_id": g.home_team_id if accepted["side"] == "home" else g.away_team_id,
                "accepted_line": accepted["line"], "accepted_home_spread": accepted["home_spread"],
                "accepted_price": accepted["price"], "stake": accepted["stake"],
                "accepted_at": from_epoch(t), "offer_as_of": from_epoch(accepted["offer_t"]),
                "quote_refs_json": canonical_json(accepted["quote_refs"]),
                "decision_json": canonical_json(accepted["detail"]),
                "engine_version": ENGINE_VERSION, "params_hash": run["params_hash"],
            })

        cols = list(wagers[0].keys()) if wagers else []
        for i, w in enumerate(wagers):
            if _fail_after_inserts is not None and i >= _fail_after_inserts:
                raise RuntimeError("injected failure (test)")
            conn.execute(
                f"INSERT INTO v4_wager ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [w[c] for c in cols],
            )
        wagers_hash = json_hash([[w["wager_id"], w["side"], w["accepted_line"], w["accepted_price"],
                                  w["stake"], w["accepted_at"]] for w in wagers])
        conn.execute(
            "INSERT INTO v4_interval (run_id, seq, interval_start, interval_end, arrivals, accepted, passes_json, "
            "wagers_hash, data_complete_through, committed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, new_seq, from_epoch(wm), from_epoch(until_e), len(events), len(wagers),
             canonical_json(passes), wagers_hash, data_complete_through,
             datetime.now(timezone.utc).strftime(ISO_FMT)),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {
        "run_id": run_id, "status": "advanced", "seq": new_seq,
        "interval_start": from_epoch(wm), "interval_end": from_epoch(until_e),
        "arrivals": len(events), "accepted": len(wagers), "passes": passes, "wagers_hash": wagers_hash,
    }
