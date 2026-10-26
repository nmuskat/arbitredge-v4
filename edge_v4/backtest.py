"""
edge_v4/backtest.py -- walk-forward backtests and controls.

1. closing_line_backtest  REAL NFL outcomes. Engine C (refit each season on
   prior seasons only) vs the listed near-close line in games.csv. Bets are
   HYPOTHETICAL: that line's book/time are unknown, so it is not a verified
   executable price (labelled on every bet).
2. synthetic_line_shop    CONTROLS for the line-shopping strategy, on a
   simulated market with KNOWN truth:
     positive control  soft books mis-price around an accurate sharp
                       consensus -> the framework must find a real edge;
     negative control  soft books = consensus + vig        -> no edge;
     noisy consensus   soft books are ACCURATE (true price + vig) but the
                       "sharp" consensus is noisy -> every apparent edge is
                       consensus error; the evidence gate must NOT certify it.
Every bet goes through the same pipeline: candidate -> ev.evaluate ->
staking -> ledger -> grade -> validation.edge_evidence_summary.
"""

import math
import random

import numpy as np
import pandas as pd

from edge_v4 import ev, ledger, pricing, validation
from edge_v4.fairline import SpreadFair
from sims_v4 import distributions as D
from sims_v4 import model as M

REG_TIME = "2026-10-26T00:00:00Z"


def _iso(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def closing_line_backtest(features, seasons, variant, conn, policy=ev.DecisionPolicy(), stake=1.0, label=""):
    sid, ver = f"C_{variant}_vs_listed_line{label}", "1"
    ledger.register(conn, sid, ver, f"Engine C ({variant}) probability vs the listed near-close line; flat 1u",
                    {"variant": variant, "policy": policy.__dict__, "stake": stake}, REG_TIME)
    n_dec = 0
    for season in seasons:
        sm = M.fit_season(features, season, variant)
        g = features[(features.season == season) & features.played]
        for p in M.predict_games(sm, g):
            r = g[g.game_id == p["game_id"]].iloc[0]
            kick = r.kickoff_utc
            placed = _iso(kick - pd.Timedelta(minutes=5))
            cands = []
            if not (pd.isna(r.spread_line) or pd.isna(r.home_spread_odds) or pd.isna(r.away_spread_odds)):
                fair_home = pricing.devig([int(r.home_spread_odds), int(r.away_spread_odds)])[0]
                for side, price, line in (("home", r.home_spread_odds, -r.spread_line),
                                          ("away", r.away_spread_odds, r.spread_line)):
                    hl = line if side == "home" else -line
                    c, pu, f = D.margin_probs(p["margin_pmf"], hl)
                    pw = c if side == "home" else f
                    mk = (fair_home if side == "home" else 1 - fair_home) * (1 - pu)
                    se = abs(D.margin_probs(p["margin_pmf"], hl + 0.5)[0] - D.margin_probs(p["margin_pmf"], hl - 0.5)[0]) \
                        * p["se_margin"]
                    cands.append(("spreads", side, line, int(price), pw, pu, se, mk))
            if not (pd.isna(r.total_line) or pd.isna(r.over_odds) or pd.isna(r.under_odds)):
                fair_over = pricing.devig([int(r.over_odds), int(r.under_odds)])[0]
                o, pu, u = D.total_probs(p["total_pmf"], r.total_line)
                se_t = abs(D.total_probs(p["total_pmf"], r.total_line - 0.5)[0]
                           - D.total_probs(p["total_pmf"], r.total_line + 0.5)[0]) * p["se_total"]
                cands.append(("totals", "over", r.total_line, int(r.over_odds), o, pu, se_t, fair_over * (1 - pu)))
                cands.append(("totals", "under", r.total_line, int(r.under_odds), u, pu, se_t, (1 - fair_over) * (1 - pu)))
            for market, side, line, price, pw, pu, se, mk in cands:
                cand = ev.Candidate(game_id=r.game_id, market=market, selection=side, line=float(line), book="listed_line",
                                    american=price, price_age_s=0, p_win_model=pw, p_push_model=pu, p_win_se=se,
                                    p_win_market=mk, p_push_market=pu, validated_strategy=True)
                row = ev.evaluate(cand, policy)
                did = ledger.record_decision(conn, sid, ver, "backtest", row, placed, _iso(kick))
                n_dec += 1
                if row["decision"] == ev.BET:
                    bid = ledger.record_bet(conn, did, sid, ver, "backtest", r.game_id, market, side, float(line), price,
                                            stake, placed, _iso(kick), pw, pu, row["ev_per_unit"],
                                            "nfldata listed line (approx. close; NOT verified executable)",
                                            close_fair_prob=mk / (1 - pu) if pu < 1 else None)
                    ledger.grade(conn, bid, int(r.home_score), int(r.away_score), _iso(kick + pd.Timedelta(hours=4)),
                                 "nfldata games.csv final score")
        conn.commit()
    return sid, n_dec


# ---------------------------------------------------------------- synthetic controls

SOFT = ["soft_a", "soft_b", "soft_c", "soft_d"]
SHARPS = ["sharp_a", "sharp_b"]


def _vig_pair(p_home_cover, vig=0.0238):
    """Two-way prices for a fair cover probability with ~-110 style margin."""
    ph, pa = p_home_cover * (1 + vig), (1 - p_home_cover) * (1 + vig)
    return pricing.decimal_to_american(1 / ph), pricing.decimal_to_american(1 / pa)


def _true_cover(true_pmf, side, line):
    """Stand-in for an EFFICIENT closing price in the controls: the true no-push
    cover probability. Used only to score CLV, never to make a decision."""
    hl = line if side == "home" else -line
    c, _, f = D.margin_probs(true_pmf, hl)
    return (c if side == "home" else f) / (c + f)


def synthetic_line_shop(conn, scenario, n_games=4000, seed=1, sigma=13.3, policy=ev.DecisionPolicy(), stake=1.0):
    """scenario: 'positive' | 'negative' | 'noisy_consensus'."""
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    kn = D.KeyNumberModel(D.M_RANGE, True)
    # key-number weights learned from a synthetic training history drawn the same way
    mus_tr = nprng.normal(0, 6, 3000)
    kn.fit_ipf(mus_tr, np.rint(nprng.normal(mus_tr, sigma)).astype(int), sigma, iters=15)
    sid, ver = f"S1_line_shop_synthetic_{scenario}", "1"
    ledger.register(conn, sid, ver, "Bet soft-book spreads whose price beats the sharp no-vig consensus (synthetic control)",
                    {"scenario": scenario, "n_games": n_games, "seed": seed, "policy": policy.__dict__}, REG_TIME)
    soft_noise = {"positive": 1.25, "negative": 0.0, "noisy_consensus": 0.0}[scenario]
    sharp_noise = {"positive": 0.25, "negative": 0.25, "noisy_consensus": 1.5}[scenario]
    t0 = pd.Timestamp("2027-09-01T17:00:00Z")
    for i in range(n_games):
        true_mu = rng.gauss(0, 6)
        true_pmf = kn.pmf(true_mu, sigma)
        cons_line = -round(true_mu * 2) / 2
        # sharp books: fair prices around their (noisy) belief at the consensus line
        sharp_quotes = {}
        for b in SHARPS:
            belief = kn.pmf(true_mu + rng.gauss(0, sharp_noise), sigma)
            c, _, f = D.margin_probs(belief, cons_line)
            sharp_quotes[b] = _vig_pair(c / (c + f), vig=0.02)
        fair = pricing.consensus_fair_prob(sharp_quotes)
        sf = SpreadFair(kn, sigma, cons_line, fair)
        # ex-ante uncertainty of the consensus: dispersion between sharp books (+ floor)
        se = max(0.005, float(np.std([pricing.devig(list(q))[0] for q in sharp_quotes.values()])) / math.sqrt(len(SHARPS)))
        kick = t0 + pd.Timedelta(days=i // 14, minutes=i % 14)
        placed = _iso(kick - pd.Timedelta(hours=2))
        # outcome from the TRUE distribution
        u, acc, margin = rng.random(), 0.0, 0
        for m, p in true_pmf.items():
            acc += p
            if u <= acc:
                margin = m
                break
        for b in SOFT:
            belief = kn.pmf(true_mu + rng.gauss(0, soft_noise), sigma)
            c, _, f = D.margin_probs(belief, cons_line)
            hp, ap = _vig_pair(c / (c + f))
            for side, price in (("home", hp), ("away", ap)):
                line = cons_line if side == "home" else -cons_line
                pw, pu = sf.side_probs(side, line)
                cand = ev.Candidate(game_id=f"syn{i}", market="spreads", selection=side, line=line, book=b,
                                    american=int(price), price_age_s=60, p_win_model=pw, p_push_model=pu, p_win_se=se,
                                    p_win_market=pw, p_push_market=pu, validated_strategy=True)
                row = ev.evaluate(cand, policy)
                did = ledger.record_decision(conn, sid, ver, "backtest", row, placed, _iso(kick))
                if row["decision"] == ev.BET:
                    bid = ledger.record_bet(conn, did, sid, ver, "backtest", f"syn{i}", "spreads", side, line, int(price),
                                            stake, placed, _iso(kick), pw, pu, row["ev_per_unit"], f"synthetic:{b}",
                                            close_fair_prob=_true_cover(true_pmf, side, line))
                    ledger.grade(conn, bid, max(0, margin) + 20, max(0, -margin) + 20,
                                 _iso(kick + pd.Timedelta(hours=4)), "synthetic truth")
        if i % 500 == 0:
            conn.commit()
    conn.commit()
    return sid


def summarize(conn, sid, n_boot=2000):
    bets = ledger.graded_bets(conn, sid)
    s = validation.edge_evidence_summary(bets, ledger.strategies_tested(conn), n_boot=n_boot) if bets else None
    if s:
        s["mean_model_ev"] = float(np.mean([b["ev"] for b in bets]))
        s["by_market"] = {m: validation.record([b for b in bets if b["market"] == m])
                          for m in sorted({b["market"] for b in bets})}
    return {"strategy": sid, "bets": len(bets), "summary": s}
