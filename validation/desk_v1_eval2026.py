"""
validation/desk_v1_eval2026.py -- EXPLORATORY 2026 evaluation of the FROZEN DESK-v1 (commit b2675de).

  python -m validation.desk_v1_eval2026 --raw DIR --market-db market.db --rankings DIR --results DIR --out file.json

2026 has already been studied, so nothing here is out-of-sample proof. Decision time T = kickoff - 90 min; market =
the original collected snapshot at or before T (<= 6 h old): Pinnacle no-vig fair, else the BetOnline group.
DESK-v1 uses only its frozen parameters; ratings for a game use 2026 plays of EARLIER weeks + the 2025 prior.
Outputs per sport-market: DESK decisions (LEAN / NO POSITION with reason), market-only baseline accuracy and
calibration, the football-only component's incremental accuracy, the closing-proxy MISMATCH diagnostics, and a
labelled COUNTERFACTUAL (w = 1, same thresholds) that is NOT the desk.
"""
import argparse
import json
import math
import random
import sqlite3
from collections import defaultdict

import numpy as np

from data_v4 import rankings as RK
from data_v4.results import NFL_ABBR
from desk_v1 import data as DT
from desk_v1 import model as M
from desk_v1.spec import SPEC
from intel_v7.history import History, view
from sportsbook_v4.util import to_epoch

MK = ("spread", "total")


def school_map(rankings_dir):
    from collect_v4 import backfill as B
    from tools_v4.backfill_replay import load_polls
    snaps = [s for s in B.poll_snapshots(load_polls(rankings_dir)) if s.get("teams")]
    return RK.TeamMatcher(max(snaps, key=lambda s: s["fetched_at"])["teams"]) if snaps else None


def ratings_by_week(sport, raw, params):
    """{week: {(market): (ratings, pace, league)}} for 2026 with the frozen (lambda, rho) per market."""
    pl = DT.plays(sport, raw, 2026)
    prev = DT.plays(sport, raw, 2025)
    teams = sorted(set(pl.off) | set(pl["def"]) | set(prev.off) | set(prev["def"]))
    clean, prev_clean = pl[~pl.garbage], prev[~prev.garbage]
    _, prev_league = M.pace_table(prev)
    out = defaultdict(dict)
    for m in MK:
        lam, rho = params[m]["combo"]
        prior = M.scaled(M.fit_ratings(prev_clean, teams, lam), rho)
        for wk in sorted(pl.week.unique().tolist() + [int(pl.week.max()) + 1]):
            pace, league = M.pace_table(pl[pl.week < wk])
            out[wk][m] = (M.fit_ratings(clean[clean.week < wk], teams, lam, prior), pace, league or prev_league)
    return out, pl


def match_game(sport, g, sched, matcher):
    """Market game (full names) -> schedule row (week, abbreviations/schools) by teams and date (+-1 day)."""
    if sport == "nfl":
        inv = {v: k for k, v in NFL_ABBR.items()}
        h, a = inv.get(g["home"]), inv.get(g["away"])
    else:
        h = matcher.school_for(g["home"]) if matcher else None
        a = matcher.school_for(g["away"]) if matcher else None
    if not h or not a:
        return None
    k = g["kickoff"]
    rows = sched[(sched.home == h) & (sched.away == a)]
    for r in rows.itertuples():
        ko = r.kickoff_utc
        try:
            t = ko.timestamp() if hasattr(ko, "timestamp") else to_epoch(str(ko))
        except Exception:
            continue
        if abs(t - k) <= 36 * 3600:
            return r
    return None


def evaluate(raw, market_db, rankings_dir, results_dir, params_path):
    params = json.load(open(params_path))
    live = sqlite3.connect(market_db)
    matcher = school_map(rankings_dir)
    from lab_v4.experiment import game_list
    from collect_v4 import backfill as B
    from tools_v4.backfill_replay import load_polls
    from intel_v7.evaluate import load_finals
    polls = B.poll_snapshots(load_polls(rankings_dir))
    gl = game_list(live, polls)
    finals = load_finals(rankings_dir, results_dir, gl)
    rows = []
    for sport in ("nfl", "cfb"):
        P = params["sports"][sport]
        rb, _ = ratings_by_week(sport, raw, P)
        sched = DT.games(sport, raw, 2026)
        for s, gid, kick, home, away in [x for x in gl if x[0] == sport]:
            sr = match_game(sport, {"home": home, "away": away, "kickoff": kick}, sched, matcher)
            fin = finals.get((sport, gid))
            for m in MK:
                row = {"sport": sport, "market": m, "event_id": gid, "game": f"{away} @ {home}", "kickoff": kick,
                       "final": fin}
                h = History(live, sport, gid, m)
                T = kick - SPEC["decision_minutes_before_kickoff"] * 60
                ft, books = h.snap_at(T)
                fc, bc = h.snap_at(kick - 1)
                if ft is None or T - ft > SPEC["market_max_age_hours"] * 3600:
                    rows.append({**row, "decision": "PASS", "reason": "no market"})
                    continue
                v = view(books)
                vc = view(bc) if fc else {}
                if "pro_mu" not in v:
                    rows.append({**row, "decision": "PASS", "reason": "no market"})
                    continue
                mu_t = v["pro_mu"]
                mu_c = vc.get("pro_mu")
                row.update(mu_mkt_T=mu_t, mu_close=mu_c, snapshot_age_min=round((T - ft) / 60, 1), basis=v["pro_basis"])
                if sr is None:
                    rows.append({**row, "decision": "PASS", "reason": "game not found in play-by-play schedule"})
                    continue
                r, pace, league = rb.get(int(sr.week), rb[max(rb)])[m]
                feat = M.team_features(r, pace, league, SPEC["pace_shrink_to_league"], sr.home, sr.away, bool(sr.neutral))
                fb = M.football_lines(feat, P[m]["alpha"], P[m]["beta"])[0 if m == "spread" else 1]
                prior_games = min(pace.get(sr.home, (0, 0))[0], pace.get(sr.away, (0, 0))[0])
                row.update(mu_fb=fb, prior_games=prior_games, week=int(sr.week))
                nz = M.normalizer(sport, m, P[m]["sigma"])
                # ---- DESK-v1 decision (frozen)
                row.update(desk_decision(P[m]["w"], m, mu_t, fb, nz, books, prior_games, "w = 0 (frozen)"))
                # ---- COUNTERFACTUAL diagnostic (NOT the desk): w = 1, same thresholds
                cf = desk_decision(1.0, m, mu_t, fb, nz, books, prior_games, None)
                row["counterfactual_w1"] = cf
                for d in (row, cf):
                    if d.get("decision") == "LEAN" and fin:
                        d.update(grade(m, d, fin, nz, mu_c))
                rows.append(row)
    return rows


def desk_decision(w, m, mu_t, fb, nz, books, prior_games, zero_reason):
    mu_d = mu_t + w * (fb - mu_t)
    if w == 0 and zero_reason:
        return {"decision": "NO POSITION", "reason": zero_reason, "mu_desk": mu_d}
    if prior_games < SPEC["min_prior_games"]:
        return {"decision": "NO POSITION", "reason": "fewer than 3 prior games", "mu_desk": mu_d}
    gap = mu_d - mu_t
    if abs(gap) < SPEC["min_gap_points"][m]:
        return {"decision": "NO POSITION", "reason": "gap below threshold", "mu_desk": mu_d, "gap": gap}
    side = ("home" if gap > 0 else "away") if m == "spread" else ("over" if gap > 0 else "under")
    best = None
    for bk, q in books.items():
        if q["mu"] is None:
            continue
        pt = q["point"] if (m == "total" or side == "home") else -q["point"]
        pr = q["price_a"] if side in ("home", "over") else q["price_b"]
        e, pw, pp = M.ev(nz, mu_d, m, side, pt, pr)
        if best is None or e > best["ev"]:
            best = {"book": bk, "point": pt, "price": pr, "ev": e, "p_win": pw, "p_push": pp}
    if best is None or best["ev"] < SPEC["min_ev"]:
        return {"decision": "NO POSITION", "reason": "EV below threshold", "mu_desk": mu_d, "gap": gap}
    return {"decision": "LEAN", "side": side, "mu_desk": mu_d, "gap": gap, **best}


def grade(m, d, fin, nz, mu_close):
    hs, as_ = fin
    y = (hs - as_) if m == "spread" else (hs + as_)
    if m == "spread":
        v = y + d["point"] if d["side"] == "home" else -y + d["point"]
    else:
        v = y - d["point"] if d["side"] == "over" else d["point"] - y
    units = M.payout(d["price"]) if v > 0 else (-1.0 if v < 0 else 0.0)
    out = {"units": units}
    if mu_close is not None:
        pw, pp = nz.prob_at(mu_close, d["side"], d["point"])
        out["clv"] = pw * M.payout(d["price"]) - (1 - pw - pp)
    return out


# ------------------------------------------------------------------------------------------------ summaries
def summarize(rows):
    out = {}
    for sport in ("nfl", "cfb"):
        for m in MK:
            xs = [r for r in rows if r["sport"] == sport and r["market"] == m]
            key = f"{sport} {m}"
            dec = defaultdict(int)
            for r in xs:
                dec[f"{r['decision']}: {r.get('reason', r.get('side', ''))}"] += 1
            s = {"game_markets": len(xs), "decisions": dict(dec)}
            ok = [r for r in xs if r.get("final") and r.get("mu_fb") is not None and r.get("mu_mkt_T") is not None]
            if ok:
                y = np.array([(r["final"][0] - r["final"][1]) if m == "spread" else sum(r["final"]) for r in ok], float)
                mt = np.array([r["mu_mkt_T"] for r in ok])
                fb = np.array([r["mu_fb"] for r in ok])
                mc = np.array([r["mu_close"] if r.get("mu_close") is not None else np.nan for r in ok])
                s["n_with_result"] = len(ok)
                s["rmse_market_T"] = float(np.sqrt(np.mean((y - mt) ** 2)))
                s["rmse_football_only"] = float(np.sqrt(np.mean((y - fb) ** 2)))
                ok_c = ~np.isnan(mc)
                s["rmse_market_close"] = float(np.sqrt(np.mean((y[ok_c] - mc[ok_c]) ** 2)))
                s["w_vs_T_2026_exploratory"] = boot_w(y - mt, fb - mt)
                s["w_vs_close_2026_exploratory"] = boot_w(y[ok_c] - mc[ok_c], fb[ok_c] - mc[ok_c])
                # mismatch: how far the market moves T -> close, and whether toward the football-only view
                s["mean_abs_move_T_to_close"] = float(np.mean(np.abs(mc[ok_c] - mt[ok_c])))
                s["market_move_toward_football_slope"] = boot_w(mc[ok_c] - mt[ok_c], fb[ok_c] - mt[ok_c])
                s["calibration_market_only"] = calib(ok, m)
            for label, get in (("DESK-v1", lambda r: r), ("COUNTERFACTUAL w=1 (not the desk)", lambda r: r.get("counterfactual_w1") or {})):
                sel = [get(r) for r in xs if get(r).get("decision") == "LEAN" and "units" in get(r)]
                s[label] = perf(sel)
            out[key] = s
    return out


def boot_w(y, x, n=2000, seed=11):
    y, x = np.asarray(y, float), np.asarray(x, float)
    if len(y) < 10 or not (x @ x):
        return None
    w = float((x @ y) / (x @ x))
    rng = random.Random(seed)
    bs = []
    idx = list(range(len(y)))
    for _ in range(n):
        ix = [rng.choice(idx) for _ in idx]
        xx, yy = x[ix], y[ix]
        bs.append(float((xx @ yy) / max(1e-9, xx @ xx)))
    bs.sort()
    return {"w": round(w, 3), "ci95": [round(bs[int(.025 * n)], 3), round(bs[int(.975 * n) - 1], 3)], "n": len(y)}


def calib(rows, m):
    """Market-only probability of side A covering the Pinnacle-implied line at T (here the desk = market)."""
    br, n = 0.0, 0
    for r in rows:
        y = (r["final"][0] - r["final"][1]) if m == "spread" else sum(r["final"])
        line = -r["mu_mkt_T"] if m == "spread" else r["mu_mkt_T"]
        line = round(line * 2) / 2
        cover = (y + line > 0) if m == "spread" else (y > line)
        push = (y + line == 0) if m == "spread" else (y == line)
        if push:
            continue
        br += (0.5 - cover) ** 2
        n += 1
    return {"brier_at_fair_line": round(br / n, 4) if n else None, "n": n,
            "note": "at the market's own fair line every market-only probability is ~0.5; Brier ~0.25 expected"}


def perf(sel):
    if not sel:
        return {"selections": 0}
    u = [d["units"] for d in sel]
    w = sum(x > 0 for x in u)
    l_ = sum(x < 0 for x in u)
    dec = max(1, w + l_)
    roi = sum(u) / dec
    se = math.sqrt(sum((x - roi) ** 2 for x in u) / max(1, len(u) - 1) / len(u))
    cl = [d["clv"] for d in sel if d.get("clv") is not None]
    return {"selections": len(sel), "record": f"{w}-{l_}-{len(u) - w - l_}", "roi": round(roi, 4),
            "roi_ci95": [round(roi - 1.96 * se, 4), round(roi + 1.96 * se, 4)],
            "mean_clv": round(sum(cl) / len(cl), 4) if cl else None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    for k in ("raw", "market-db", "rankings", "results", "params", "out"):
        ap.add_argument("--" + k, required=True)
    a = ap.parse_args(argv)
    rows = evaluate(a.raw, a.market_db, a.rankings, a.results, a.params)
    rep = {"label": "EXPLORATORY 2026 (already studied); frozen DESK-v1 b2675de", "summary": summarize(rows), "rows": rows}
    json.dump(rep, open(a.out, "w"), default=float, indent=1)
    print(json.dumps(rep["summary"], indent=1, default=float))


if __name__ == "__main__":
    main()
