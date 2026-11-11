"""
need_v6/desk.py -- the trading desk of the synthetic sportsbook (one desk state per game-market).

The desk SETS its own price: a number (spread/total) plus juice derived from its fair value, with a deliberate
shade (in no-vig probability) when it chooses to. Fair value = market makers' no-vig implied mean (market.py);
the desk has NO independent pricing edge, so it never claims one: every deliberate position is labelled
"risk acceptance (no validated informational edge)".

Actions (every decision is logged with its rationale):
  MOVE            re-centre on the market number (new market information) or move after respected action
  DEFEND          keep a key number (NFL 3/7) and adjust juice when the normalised fair moved too little to cross it
  BALANCE         shade against the heavy side / close it when worst-case loss exceeds the game limit
  ACCEPT EXPOSURE deliberately hold a material position when the price is settled, risk is within the limit and
                  expected book P&L is at least CONFIG accept_ev_to_sd x its standard deviation
  NO POSITION     otherwise. Trading Desk NEED = NONE.
The desk never opposes customer money merely because one side is popular: ACCEPT needs the EV/risk test.
"""

import math

import numpy as np

from need_v6.config import CONFIG
from need_v6.market import SIDES


def american(p):
    """Implied probability -> American price (rounded to whole cents)."""
    p = min(max(p, 0.02), 0.98)
    return float(round(-100 * p / (1 - p))) if p >= 0.5 else float(round(100 * (1 - p) / p))


def implied(price):
    return 100.0 / (price + 100.0) if price > 0 else -price / (-price + 100.0)


class Desk:
    def __init__(self, sport, market, gm):
        self.sport, self.market, self.gm = sport, market, gm
        self.line = None            # home spread / total currently posted
        self.shade = 0.0            # added to side A's (home/over) implied probability
        self.closed = set()         # sides closed by the risk limit
        self.last_respected = None
        self.fair_mu_at_move = None
        self.decision = {"action": "NO MARKET", "desk_need": None, "rationale": "no market observed yet"}
        self.log = []

    # ---------------------------------------------------------------- prices
    def fair_q(self, mu, line):
        """No-push probability that side A covers at the posted number."""
        a = SIDES[self.market][0]
        pw, pp = self.gm.fair_prob(mu, a, self.gm.side_point(a, line))
        return pw / max(1e-9, 1 - pp), pp

    def offers(self, mu):
        """{side: (point, price)} at the desk's posted number; closed sides are not offered."""
        if self.line is None or mu is None:
            return {}
        key = (self.line, round(self.shade, 6), tuple(sorted(self.closed)), mu)
        c = self.__dict__.setdefault("_offer_cache", {})
        if key in c:
            return c[key]
        q, _ = self.fair_q(mu, self.line)
        m = CONFIG["margin"] / 2
        a, b = SIDES[self.market]
        out = {a: (self.gm.side_point(a, self.line), american(q + self.shade + m)),
               b: (self.gm.side_point(b, self.line), american(1 - q - self.shade + m))}
        res = {s: v for s, v in out.items() if s not in self.closed}
        if len(c) > 5000:
            c.clear()
        c[key] = res
        return res

    def _record(self, t, action, rationale, need=None, **extra):
        self.decision = {"action": action, "desk_need": need, "rationale": rationale, "at": t,
                         "line": self.line, "shade": round(self.shade, 4), **extra}
        self.log.append(dict(self.decision))

    def _move_line(self, direction):
        """Move the number half a point. direction +1 = make side A (home/over) more expensive."""
        if self.market == "spread":
            self.line -= 0.5 * direction          # home -3 -> -3.5 makes home more expensive
        else:
            self.line += 0.5 * direction          # total 44.5 -> 45 makes over more expensive
        self.shade = 0.0

    def _is_key(self, line):
        return self.market == "spread" and self.sport == "nfl" and abs(line) in CONFIG["key_numbers"]

    # ---------------------------------------------------------------- events
    def on_observation(self, t, ms, exposure, kickoff):
        """A new external market observation (or a re-evaluation tick)."""
        if ms.get("status") != "OK":
            if self.line is None:
                return
            self._record(t, "NO POSITION", f"market {ms.get('status', 'NO_DATA')}: price held, no new information",
                         stale=True)
            return
        mu, line = ms["fair_mu"], ms["line"]
        if self.line is None:
            self.line, self.shade, self.fair_mu_at_move = line, 0.0, mu
            self._record(t, "MOVE", f"opened at the market number {line} (fair from {ms['basis']})")
        elif line != self.line:
            moved = abs(mu - (self.fair_mu_at_move if self.fair_mu_at_move is not None else mu))
            if self._is_key(self.line) and moved < CONFIG["key_defend_max_mu_move"]:
                self._record(t, "DEFEND", f"kept key number {self.line} while the market shows {line}: normalised "
                                          f"fair moved only {moved:.2f} pts; juice re-priced at our number")
            else:
                self.line, self.shade, self.fair_mu_at_move = line, 0.0, mu
                self._record(t, "MOVE", f"re-centred on the market number {line} (fair moved {moved:.2f} pts)")
        if self._risk(t, exposure):
            return
        self._evaluate_position(t, ms, exposure)

    def on_wager(self, t, side, respected, ms, exposure):
        if respected:
            self.last_respected = t
            a = SIDES[self.market][0]
            d = 1 if side == a else -1
            if abs(self.shade + d * CONFIG["shade_step"]) > CONFIG["max_shade"] and not self._is_key(self.line):
                self._move_line(d)
                self._record(t, "MOVE", f"respected action on {side}: number moved to {self.line}")
            else:
                self.shade += d * CONFIG["shade_step"]
                self._record(t, "MOVE" if not self._is_key(self.line) else "DEFEND",
                             f"respected action on {side}: juice moved against {side}"
                             + (f" (key number {self.line} defended)" if self._is_key(self.line) else ""))
        self._risk(t, exposure)

    # ---------------------------------------------------------------- risk and position
    def _risk(self, t, exposure):
        limit = CONFIG["game_limit"][self.sport]
        worst = exposure.worst()
        heavy = exposure.heavy_side(SIDES[self.market])
        if worst >= -limit or heavy is None:
            if self.closed and worst >= -0.8 * limit:
                self.closed.clear()
            return False
        a = SIDES[self.market][0]
        d = 1 if heavy == a else -1
        if worst < -1.25 * limit:
            self.closed.add(heavy)
        if abs(self.shade + d * CONFIG["shade_step"]) > CONFIG["max_shade"]:
            self._move_line(d)
        else:
            self.shade += d * CONFIG["shade_step"]
        self._record(t, "BALANCE", f"worst-case loss ${-worst:,.0f} exceeds the ${limit:,.0f} game limit: "
                                   f"price shaded against {heavy}" + (", side closed" if heavy in self.closed else ""),
                     risk_used=round(-worst / limit, 3))
        return True

    def _evaluate_position(self, t, ms, exposure):
        limit = CONFIG["game_limit"][self.sport]
        if not exposure.n:
            self._record(t, "NO POSITION", "no wagers yet")
            return
        settled = self.last_respected is None or t - self.last_respected >= CONFIG["respected_quiet_s"]
        pmf = self.gm_pmf(ms["fair_mu"])
        ev, sd = exposure.stats(pmf)
        worst = exposure.worst()
        a, b = SIDES[self.market]
        pb, pa = exposure.clean()          # book P&L when B / A covers every accepted number
        need = a if pa > pb else b
        swing = abs(pa - pb)
        material = swing >= max(CONFIG["materiality_abs"], CONFIG["materiality_share"] * exposure.handle)
        info = {"ev": round(ev, 2), "sd": round(sd, 2), "worst": round(worst, 2), "swing": round(swing, 2),
                "risk_used": round(-worst / limit, 3) if worst < 0 else 0.0, "settled": settled,
                "uncertainty_pts": ms["uncertainty"]}
        if settled and material and worst >= -limit and sd > 0 and ev >= CONFIG["accept_ev_to_sd"] * sd:
            self._record(t, "ACCEPT EXPOSURE",
                         f"price settled, position within limit (worst ${-worst:,.0f} of ${limit:,.0f}), expected book "
                         f"P&L ${ev:,.0f} >= {CONFIG['accept_ev_to_sd']} x SD ${sd:,.0f}: desk holds its price and "
                         f"accepts needing {need}. Basis: risk acceptance (no validated informational edge)",
                         need=need, **info)
        else:
            why = ("respected action too recent" if not settled else "position immaterial" if not material else
                   "expected P&L does not justify the risk" if ev < CONFIG["accept_ev_to_sd"] * sd else "risk limit")
            self._record(t, "NO POSITION", f"no deliberate position: {why}", **info)

    def gm_pmf(self, mu):
        from need_v6.market import pmf_on_grid
        return pmf_on_grid(self.gm, mu, self.market)
