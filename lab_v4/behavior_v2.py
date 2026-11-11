"""
lab_v4/behavior_v2.py -- RESEARCH-ONLY revised customer behaviour ("V4b2"). Production engine untouched.

What changes vs production (sportsbook_v5.r2), and why (root causes in docs_v4/BETTOR_OVERHAUL.md):
  1. REAL pre-kickoff facts. Production passes NoFacts(), so every recency / narrative weight multiplies zero.
     RealFacts gives each game the two teams' last-3 results (wins, average margin, current streak, points scored),
     from finals of games that kicked off >= 4 h before the decision time. Never a result of the game itself.
  2. Persistent INDIVIDUAL decision traits (deterministic in the customer's id, own random stream):
     discipline (0..1), price tolerance and sensitivity, impulse-stake probability, loyalty strength, recency
     overreaction. Two customers of one group now decide differently, not only bet different sizes.
  3. PRICE-AWARE acceptance: every recreational bet is checked against the market makers' no-vig fair at the same
     minute (information the customer could see on any screen). Acceptance falls with how bad the price is, at an
     individual tolerance; price-sensitive selective customers use an explicit hard threshold; team-loyal customers
     do not price-check bets on their own team.
  4. Group-specific mechanisms: underdog contrarians' participation rises with the size of the underdog; narrative
     chasers over-react to streaks and to recent scoring (totals); high-stakes customers lean harder to favourites,
     overs and national-TV games and make occasional oversized bets; sharps use the V4r1 EV rule.
  5. Max-win limit (V4r1).
Every number below is an A PRIORI behavioural assumption (documented, never fitted to results or to NEED).
No customer ever sees a final score of the game it bets on.
"""
import bisect
import collections
import math
import random

from sportsbook_v4.util import seed_int

V2 = {
    "facts_min_age_s": 4 * 3600,            # a game's final counts as known 4 h after its kickoff
    # price acceptance (EV of the offered price at the market makers' no-vig fair; retail median is about -2.3%)
    "tolerance": {"REC_CASUAL": 0.05, "HIGH_STAKES_REC": 0.06, "NARRATIVE_CHASER": 0.06, "TEAM_LOYAL": 0.07,
                  "UNDERDOG_CONTRARIAN": 0.05},
    "sensitivity": {"REC_CASUAL": 20.0, "HIGH_STAKES_REC": 12.0, "NARRATIVE_CHASER": 12.0, "TEAM_LOYAL": 8.0,
                    "UNDERDOG_CONTRARIAN": 20.0},
    "selective_threshold": (-0.030, -0.010),  # SELECTIVE: individual hard EV floor, uniform in this range
    "discipline_beta": {"REC_CASUAL": (2, 3), "HIGH_STAKES_REC": (2, 3), "NARRATIVE_CHASER": (1.5, 4),
                        "TEAM_LOYAL": (2, 3), "UNDERDOG_CONTRARIAN": (3, 3), "SELECTIVE_PRICE_SENSITIVE": (6, 2),
                        "SHARP": (8, 1.5)},
    # high-stakes recreational
    "hs_fav_shift": 0.30, "hs_over_shift": 0.20, "hs_national_tv_mult": 2.0,
    "impulse_beta": (1.0, 14.0),             # P(oversized bet) per customer, mean ~6.7%; impulsive customers higher
    "impulse_mult": (2.0, 4.0),              # oversized bet = 2-4 x the normal stake (still within limit and budget)
    # narrative chasers
    "narr_recency_mult": (1.5, 0.4),         # lognormal(mean log 1.5, sd 0.4) x their recency weight
    "narr_scoring_weight": 0.8,              # logit on (recent combined scoring - league average) / 14 for totals
    # team loyalty
    "loyalty_log_sd": 0.5, "loyal_home_pref": (0.30, 0.20),
    # underdog contrarian participation by underdog size
    "dog_participation": (0.35, 0.09),       # P(bet) = min(1, a + b x |points|) for spreads; ML: by implied prob
}


def ind2(customer):
    """Persistent individual decision traits (own stream; deterministic in bettor_uid)."""
    a = customer["archetype"]
    rng = random.Random(seed_int("v4b2_individual", customer["bettor_uid"]))
    disc = rng.betavariate(*V2["discipline_beta"][a])
    tol = V2["tolerance"].get(a, 0.05) * math.exp(rng.gauss(0, 0.4)) * (1.4 - 0.8 * disc)
    sens = V2["sensitivity"].get(a, 20.0) * math.exp(rng.gauss(0, 0.4)) * (0.6 + 0.8 * disc)
    lo, hi = V2["selective_threshold"]
    imp = rng.betavariate(*V2["impulse_beta"]) * (1.5 - disc)
    return {"discipline": disc, "tolerance": tol, "sensitivity": sens, "ev_floor": rng.uniform(lo, hi),
            "impulse_p": imp, "impulse_u": rng.random(),
            "loyalty_mult": math.exp(rng.gauss(0, V2["loyalty_log_sd"])),
            "loyal_home_pref": rng.gauss(*V2["loyal_home_pref"]),
            "narr_mult": math.exp(rng.gauss(math.log(V2["narr_recency_mult"][0]), V2["narr_recency_mult"][1]))}


# Three alternative sportsbook populations (three-book experiment). Fixed a priori; multipliers on V4b2 behaviour.
PROFILES = {
    "public": {"fav_add": 0.50, "over_add": 0.40, "popularity_mult": 2.0, "tv_mult": 2.0, "primetime_mult": 1.5,
               "recency_mult": 1.5, "loyalty_mult": 1.0, "tol_mult": 1.0, "sens_mult": 1.0, "impulse_mult": 1.0,
               "shop_all": False},
    "emotion": {"fav_add": 0.0, "over_add": 0.0, "popularity_mult": 1.0, "tv_mult": 1.0, "primetime_mult": 1.0,
                "recency_mult": 2.5, "loyalty_mult": 1.75, "tol_mult": 1.5, "sens_mult": 0.6, "impulse_mult": 2.5,
                "shop_all": False},
    "price": {"fav_add": 0.0, "over_add": 0.0, "popularity_mult": 1.0, "tv_mult": 1.0, "primetime_mult": 1.0,
              "recency_mult": 1.0, "loyalty_mult": 1.0, "tol_mult": 0.5, "sens_mult": 2.0, "impulse_mult": 0.25,
              "shop_all": True},
}


def adjust(b, arch, profile=None):
    """Apply the group mechanisms to the customer's own (customers_v5) parameters. Returns (b, arch, ind)."""
    b, arch, ind = _adjust(b, arch)
    P = PROFILES.get(profile)
    if not P or arch["is_sharp"]:
        return b, arch, ind
    tr = dict(b["traits"])
    tr["fav_pref"] += P["fav_add"]
    tr["over_pref"] = tr.get("over_pref", 0.0) + P["over_add"]
    tr["popularity_weight"] *= P["popularity_mult"]
    tr["recency_weight"] *= P["recency_mult"]
    arch["national_tv_weight"] *= P["tv_mult"]
    arch["primetime_weight"] *= P["primetime_mult"]
    arch["loyalty"] = arch["loyalty"] * P["loyalty_mult"]
    if P["shop_all"]:
        arch["shops"] = True
        arch.setdefault("shop_line_tolerance", 0.5)
        arch.setdefault("shop_prob_tolerance", 0.015)
    ind = dict(ind, tolerance=ind["tolerance"] * P["tol_mult"], sensitivity=ind["sensitivity"] * P["sens_mult"],
               impulse_p=min(1.0, ind["impulse_p"] * P["impulse_mult"]))
    return dict(b, traits=tr), arch, ind


def _adjust(b, arch):
    ind = ind2(b)
    tr = dict(b["traits"])
    arch = dict(arch)
    name = b["archetype"]
    if name == "HIGH_STAKES_REC":
        tr["fav_pref"] += V2["hs_fav_shift"]
        tr["over_pref"] = tr.get("over_pref", 0.0) + V2["hs_over_shift"]
        arch["national_tv_weight"] *= V2["hs_national_tv_mult"]
    if name == "NARRATIVE_CHASER":
        tr["recency_weight"] *= ind["narr_mult"]
    if name == "TEAM_LOYAL":
        arch["loyalty"] = arch["loyalty"] * ind["loyalty_mult"]
        tr["home_pref"] = ind["loyal_home_pref"]
    return dict(b, traits=tr), arch, ind


# ------------------------------------------------------------------------------------------------ facts
class RealFacts:
    """Point-in-time team form from real finals. games: [(sport, gid, kickoff, home, away)]; finals {(s,g): (h, a)}."""

    def __init__(self, sport, games, finals):
        self.sport = sport
        self.by_team = collections.defaultdict(list)          # team -> [(kickoff, pts_for, pts_against)]
        self.teams = {}
        for s, gid, k, h, a in games:
            if s != sport:
                continue
            self.teams[gid] = (h, a)
            f = finals.get((s, gid))
            if f and None not in f:
                self.by_team[h].append((k, f[0], f[1]))
                self.by_team[a].append((k, f[1], f[0]))
        for v in self.by_team.values():
            v.sort()
        self.keys = {t: [x[0] for x in v] for t, v in self.by_team.items()}
        allpts = [pf + pa for v in self.by_team.values() for _, pf, pa in v]
        self.league_total = (sum(allpts) / len(allpts)) if allpts else None

    def _form(self, team, as_of):
        ks = self.keys.get(team)
        if not ks:
            return None
        i = bisect.bisect_right(ks, as_of - V2["facts_min_age_s"])
        hist = self.by_team[team][:i]
        if not hist:
            return None
        last = hist[-3:]
        streak = 0
        for _, pf, pa in reversed(hist):
            if pf > pa:
                streak += 1
            else:
                break
        return {"wins_last_3": sum(1 for _, pf, pa in last if pf > pa), "n": len(last),
                "point_margin_avg_last_3": sum(pf - pa for _, pf, pa in last) / len(last),
                "current_win_streak": streak, "points_total_avg_last_3": sum(pf + pa for _, pf, pa in last) / len(last)}

    def facts(self, game_id, as_of_epoch):
        ck = (game_id, as_of_epoch)
        c = self.__dict__.setdefault("_fc", {})
        if ck in c:
            return c[ck]
        c[ck] = self._facts(game_id, as_of_epoch)
        return c[ck]

    def _facts(self, game_id, as_of_epoch):
        if game_id not in self.teams:
            return {}
        out = {}
        for side, team in zip(("home", "away"), self.teams[game_id]):
            f = self._form(team, as_of_epoch)
            if f:
                if f["n"] == 3:
                    out[f"{side}_wins_last_3"] = f["wins_last_3"]
                out[f"{side}_point_margin_avg_last_3"] = f["point_margin_avg_last_3"]
                out[f"{side}_current_win_streak"] = f["current_win_streak"]
                out[f"{side}_points_total_avg_last_3"] = f["points_total_avg_last_3"]
        out["league_total_avg"] = self.league_total_at(as_of_epoch)
        return out

    def league_total_at(self, t):
        c = self.__dict__.setdefault("_lc", {})
        if t not in c:
            vals = [pf + pa for v in self.by_team.values() for k, pf, pa in v if k <= t - V2["facts_min_age_s"]]
            c[t] = sum(vals) / len(vals) if vals else None
        return c[t]


# ------------------------------------------------------------------------------------------------ decision pieces
def scoring_signal(facts):
    """Bounded [-1, 1]: how much the two teams' recent games out-scored the league (narrative for totals)."""
    h, a, lg = facts.get("home_points_total_avg_last_3"), facts.get("away_points_total_avg_last_3"), facts.get("league_total_avg")
    if h is None or a is None or lg is None:
        return 0.0
    return max(-1.0, min(1.0, ((h + a) / 2 - lg) / 14.0))


def dog_participation(market, acc):
    """P(an underdog contrarian actually places this underdog bet), rising with the size of the underdog."""
    a, b = V2["dog_participation"]
    if market == "spread":
        return min(1.0, a + b * abs(acc["line"] or 0.0))
    if market == "moneyline":
        p = 100.0 / (acc["price"] + 100.0) if acc["price"] > 0 else 1.0
        return min(1.0, a + 2.0 * max(0.0, 0.5 - p))
    return 1.0


def price_accept(name, ind, ev, own_team_bet, u):
    """(accepted?, reason). ev: EV of the offered price at the market makers' no-vig fair (None = unknown)."""
    if ev is None or name == "SHARP":
        return True, "accepted"
    if name == "SELECTIVE_PRICE_SENSITIVE":
        return (ev >= ind["ev_floor"]), ("accepted" if ev >= ind["ev_floor"] else "below_price_threshold")
    if name == "TEAM_LOYAL" and own_team_bet:
        return True, "accepted"                       # loyal fans do not price-check their own team
    tol, beta = ind["tolerance"], ind["sensitivity"]
    p = 1.0 if ev >= -tol else math.exp(beta * (ev + tol))
    return (u < p), ("accepted" if u < p else "price_not_attractive")


def impulse_multiplier(ind, rng_u, rng_m):
    if rng_u < ind["impulse_p"]:
        lo, hi = V2["impulse_mult"]
        return lo + (hi - lo) * rng_m
    return 1.0


import contextlib  # noqa: E402


@contextlib.contextmanager
def narrative_hook(E):
    """Narrative chasers' totals lean also follows the two teams' recent scoring (wraps engine._recreational_pick;
    restored on exit). Every other customer and market is passed through unchanged."""
    orig = E._recreational_pick

    def wrapped(rng, bettor, arch, game, market, offer, facts):
        if market == "total" and bettor["archetype"] == "NARRATIVE_CHASER":
            tr = dict(bettor["traits"])
            tr["over_pref"] = tr.get("over_pref", 0.0) + V2["narr_scoring_weight"] * scoring_signal(facts)
            bettor = dict(bettor, traits=tr)
        return orig(rng, bettor, arch, game, market, offer, facts)
    E._recreational_pick = wrapped
    try:
        yield
    finally:
        E._recreational_pick = orig


# ------------------------------------------------------------------------------------------------ game-day regulars
# Owner-specified realism (2026-11-11): most recreational bettors bet 3-4 games on the main game day every week
# (Saturday for CFB, Sunday for NFL). A FIXED share of each recreational customer becomes a "regular" (persistent,
# drawn from the customer's id). Side choice, stakes and prices are unchanged; only frequency and timing change.
VOLUME = {"regular_share": 0.60, "regular_groups": ("REC_CASUAL", "NARRATIVE_CHASER", "TEAM_LOYAL",
                                                    "UNDERDOG_CONTRARIAN", "HIGH_STAKES_REC"),
          "arrivals_per_week": (3.5, 5.0),        # uniform per regular; ~3-4 placed bets after normal passes
          "p_active": 0.90}
_HOURS = [(0, 8, 0.05), (8, 12, 0.80), (12, 18, 1.00), (18, 24, 0.70)]
GAMEDAY_PROFILES = {
    "gameday_sat": {"weekday": [0.02, 0.02, 0.02, 0.05, 0.10, 1.00, 0.05], "hours": _HOURS},   # CFB Saturday
    "gameday_sun": {"weekday": [0.12, 0.02, 0.02, 0.12, 0.03, 0.05, 1.00], "hours": _HOURS},   # NFL Sun (+TNF/MNF)
}


def install_gameday_profiles():
    from sportsbook_v4.params import TIMING_PROFILES
    for k, v in GAMEDAY_PROFILES.items():
        TIMING_PROFILES.setdefault(k, v)          # research process only; production params file untouched


def volume(b, arch, active, sport, week_start):
    """(b, arch, active, is_regular) for the game-day-regular experiment."""
    if b["archetype"] not in VOLUME["regular_groups"]:
        return b, arch, active, False
    rng = random.Random(seed_int("v4vol_regular", b["bettor_uid"]))
    if rng.random() >= VOLUME["regular_share"]:
        return b, arch, active, False
    lo, hi = VOLUME["arrivals_per_week"]
    tr = dict(b["traits"], arrivals_per_week=rng.uniform(lo, hi))
    arch = dict(arch, timing="gameday_sat" if sport == "cfb" else "gameday_sun")
    arch["timing_key"] = (arch["timing"], 0.0)
    busted = b["bankroll"] < 10.0
    act = (not busted) and random.Random(seed_int("v4vol_active", b["bettor_uid"], int(week_start))).random() < VOLUME["p_active"]
    return dict(b, traits=tr), arch, act, True
