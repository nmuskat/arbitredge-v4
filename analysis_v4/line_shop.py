"""
analysis_v4/line_shop.py -- does line shopping against the sharp consensus
produce positive expected value on REAL collected odds?

For every stored snapshot (point in time, nothing later visible), every
fresh book price is evaluated with edge_v4.forward.scan: fair probability
from the sharp-weighted no-vig consensus, re-priced to that book's own
number with the sport's key-number distribution. An opportunity is a price
whose EV stays >= the policy threshold at the pessimistic end of the
uncertainty interval. Each opportunity is counted ONCE, at first sighting
(same event/market/side/book/line/price later is the same opportunity).

Scoring, strongest available first:
  closing EV   EV of the price taken, evaluated at the OBSERVED closing
               consensus (the market's final, most-informed estimate). This
               is the CLV test. Games whose close was not observed are
               excluded and counted, never imputed.
  realized     win/loss once results exist (noisy; needs many bets).
Confidence intervals bootstrap whole GAMES (opportunities within a game are
correlated).

  python -m analysis_v4.line_shop --db ~/ArbitrEdgeV4/data/market_live.db
"""

import argparse
import json
import random

from edge_v4 import ev
from edge_v4.fairline import SpreadFair, TotalFair
from edge_v4.forward import scan
from market_v4 import asof
from sims_v4 import distributions as D

import os

NFL_PARAMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sims_v4", "nfl_distribution_2026.json")
SPORT_TOTAL = {"americanfootball_nfl": (None, None), "americanfootball_ncaaf": (16.5, 16.0)}


def models(sport, nfl_params_path=NFL_PARAMS):
    """Key-number distributions. NFL: weights learned from NFL history.
    CFB: NO key-number weights (NFL patterns do not transfer) and wider sigmas
    -- an explicit, labelled approximation until a CFB history is ingested."""
    mk = D.KeyNumberModel(D.M_RANGE, True)
    tk = D.KeyNumberModel(D.T_RANGE, False)
    if sport == "americanfootball_nfl":
        p = json.load(open(nfl_params_path))
        mk.w = {int(k): v for k, v in p["margin_weights"].items()}
        tk.w = {int(k): v for k, v in p["total_weights"].items()}
        return mk, p["margin_sigma"], tk, p["total_sigma"], "nfl_key_numbers"
    mk.w = {int(abs(m)): 1.0 for m in D.M_RANGE}
    tk.w = {int(t): 1.0 for t in D.T_RANGE}
    ms, ts = SPORT_TOTAL.get(sport, (16.5, 16.0))
    return mk, ms, tk, ts, "plain_normal_unvalidated"


def _events(conn, sport):
    return [{"event_id": e, "kickoff": k, "home": h, "away": a} for e, k, h, a in conn.execute(
        "SELECT event_id, MIN(commence), home, away FROM mkt_quote WHERE sport=? GROUP BY event_id", (sport,))]


def find_opportunities(conn, sport, policy=ev.LIVE_POLICY, max_age_s=asof.DEFAULT_MAX_AGE_S):
    mk, ms, tk, ts, model_label = models(sport)
    events = _events(conn, sport)
    times = [t for (t,) in conn.execute(
        "SELECT DISTINCT fetched_at FROM mkt_fetch WHERE sport=? AND n_quotes>0 ORDER BY 1", (sport,))]
    seen, opps = set(), []
    for t in times:
        live = [e for e in events if e["kickoff"] > t]
        for r in scan(conn, None, live, t, mk, ms, tk, ts, policy=policy, max_age_s=max_age_s, record=False):
            if r["decision"] == ev.NO_BET or r.get("selection") is None:
                continue
            key = (r["game_id"], r["market"], r["selection"], r["book"], r["line"], r["american"])
            if key in seen:
                continue
            seen.add(key)
            r["first_seen"] = t
            r["hours_to_kickoff"] = (next(e["kickoff"] for e in events if e["event_id"] == r["game_id"]) - t) / 3600
            r["model"] = model_label
            opps.append(r)
    return opps, (mk, ms, tk, ts)


def closing_ev(conn, opp, mods, max_age_s=asof.DEFAULT_MAX_AGE_S):
    mk, ms, tk, ts = mods
    close, why = asof.observed_close(conn, opp["game_id"], opp["market"], max_age_s)
    if close is None:
        return None, why
    side, line, price = opp["selection"], opp["line"], opp["american"]
    if opp["market"] == "spreads":
        pw, pu = SpreadFair(mk, ms, close["line"], close["fair"]["home"]).side_probs(side, line)
    elif opp["market"] == "totals":
        pw, pu = TotalFair(tk, ts, close["line"], close["fair"]["over"]).side_probs(side, line)
    else:
        pw, pu = close["fair"][side], 0.0
    return ev.expected_value(pw, pu, price), "observed"


def _cluster_ci(values_by_game, n_boot=2000, seed=5):
    games = list(values_by_game)
    if not games:
        return None
    rng = random.Random(seed)
    flat = [v for g in games for v in values_by_game[g]]
    means = []
    for _ in range(n_boot):
        sample = [v for _ in games for v in values_by_game[games[rng.randrange(len(games))]]]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {"mean": sum(flat) / len(flat), "lo": means[int(0.025 * n_boot)], "hi": means[int(0.975 * n_boot) - 1],
            "p_le_0": (sum(m <= 0 for m in means) + 1) / (n_boot + 1), "n": len(flat), "games": len(games)}


def report(conn, sport, policy=ev.LIVE_POLICY):
    opps, mods = find_opportunities(conn, sport, policy)
    by_game, unobserved, rows = {}, 0, []
    for o in opps:
        cev, why = closing_ev(conn, o, mods)
        o["closing_ev"] = cev
        if cev is None:
            unobserved += 1
            continue
        by_game.setdefault(o["game_id"], []).append(cev)
        rows.append(o)

    def group(key):
        out = {}
        for o in rows:
            out.setdefault(key(o), {}).setdefault(o["game_id"], []).append(o["closing_ev"])
        return {k: _cluster_ci(v, n_boot=500) for k, v in sorted(out.items())}

    return {
        "sport": sport, "policy": policy.version, "model": opps[0]["model"] if opps else None,
        "opportunities": len(opps), "close_observed": len(rows), "close_unobserved": unobserved,
        "mean_ev_at_detection": (sum(o["ev_per_unit"] for o in opps) / len(opps)) if opps else None,
        "closing_ev": _cluster_ci(by_game),
        "by_book": group(lambda o: o["book"]), "by_market": group(lambda o: o["market"]),
        "by_hours_to_kickoff": group(lambda o: "<2h" if o["hours_to_kickoff"] < 2 else
                                     "2-24h" if o["hours_to_kickoff"] < 24 else ">24h"),
        "verdict": _verdict(_cluster_ci(by_game)),
        "opportunity_rows": [{k: o.get(k) for k in ("game_id", "market", "selection", "book", "line", "american",
                                                     "ev_per_unit", "closing_ev", "first_seen", "hours_to_kickoff")}
                             for o in opps],
    }


def _verdict(ci):
    if ci is None or ci["n"] < 30:
        return "INSUFFICIENT DATA"
    if ci["lo"] > 0:
        return "POSITIVE CLOSING EV (provisional: realized results still required, rebuild plan §G)"
    if ci["hi"] < 0:
        return "NEGATIVE CLOSING EV: apparent opportunities were not real"
    return "INCONCLUSIVE: interval includes zero"


def main(argv=None):
    import sqlite3
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/ArbitrEdgeV4/data/market_live.db"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    res = {s: report(conn, s) for s in ("americanfootball_nfl", "americanfootball_ncaaf")}
    for s, r in res.items():
        print(s, {k: r[k] for k in ("opportunities", "close_observed", "close_unobserved", "verdict")},
              "closing EV:", r["closing_ev"])
    if a.out:
        json.dump(res, open(a.out, "w"), indent=2, default=float)


if __name__ == "__main__":
    main()
