"""
edge_v4/staking.py -- stake sizing and portfolio limits.

Rules (enforced, and tested):
  * only BET decisions receive a stake; WATCH and NO BET always get 0;
  * Kelly is FRACTIONAL and computed on the pessimistic probability;
  * stakes never increase because of prior losses (no chasing, no
    martingale): the drawdown throttle can only reduce a stake;
  * hard caps per bet, per game, per day and per correlation group.
"""

from dataclasses import dataclass

from edge_v4 import pricing


def kelly_fraction(p_win, p_push, american):
    """Growth-optimal fraction with a push outcome:
    maximise p ln(1+bf) + q ln(1-f)  =>  f* = (b p - q) / (b (p + q))."""
    b = pricing.net_profit_per_unit(american)
    q = max(0.0, 1.0 - p_win - p_push)
    if p_win + q <= 0:
        return 0.0
    return max(0.0, (b * p_win - q) / (b * (p_win + q)))


@dataclass(frozen=True)
class StakingPolicy:
    version: str = "staking_v4.0"
    mode: str = "fractional_kelly"      # or "flat"
    flat_units: float = 1.0
    unit_size: float = 0.01             # 1 unit = 1% of bankroll
    kelly_multiplier: float = 0.25
    max_bet_frac: float = 0.02
    max_game_frac: float = 0.03
    max_day_frac: float = 0.10
    max_group_frac: float = 0.04        # correlated positions share one cap
    drawdown_soft: float = 0.10         # beyond this, stakes are scaled down
    drawdown_hard: float = 0.25         # beyond this, staking stops (paper only)


def drawdown_multiplier(current_bankroll, peak_bankroll, policy):
    dd = 0.0 if peak_bankroll <= 0 else max(0.0, 1.0 - current_bankroll / peak_bankroll)
    if dd >= policy.drawdown_hard:
        return 0.0
    if dd <= policy.drawdown_soft:
        return 1.0
    span = policy.drawdown_hard - policy.drawdown_soft
    return max(0.0, 1.0 - (dd - policy.drawdown_soft) / span)


def size_stake(decision_row, bankroll, peak_bankroll, exposure, policy=StakingPolicy(), z_prob_lo=None):
    """
    decision_row: output of ev.evaluate(). exposure: dict with keys
    'game' {game_id: amount}, 'day' amount, 'group' {group: amount} and the
    row may carry 'corr_group'. Returns (stake, notes).
    """
    if decision_row["decision"] != "BET":
        return 0.0, ["no stake: decision is " + decision_row["decision"]]
    notes = []
    if policy.mode == "flat":
        raw = policy.flat_units * policy.unit_size * bankroll
    else:
        p_lo = z_prob_lo if z_prob_lo is not None else max(
            0.0, decision_row["p_win_model"] - 1.645 * decision_row["p_win_se"])
        f = kelly_fraction(p_lo, decision_row["p_push_model"], decision_row["american"])
        raw = policy.kelly_multiplier * f * bankroll
        notes.append(f"kelly f*={f:.4f} on pessimistic p={p_lo:.4f} x{policy.kelly_multiplier}")
    m = drawdown_multiplier(bankroll, peak_bankroll, policy)
    if m < 1.0:
        notes.append(f"drawdown throttle x{m:.2f}")
    stake = raw * m
    caps = [
        ("bet", policy.max_bet_frac * bankroll),
        ("game", policy.max_game_frac * bankroll - exposure.get("game", {}).get(decision_row["game_id"], 0.0)),
        ("day", policy.max_day_frac * bankroll - exposure.get("day", 0.0)),
    ]
    grp = decision_row.get("corr_group")
    if grp:
        caps.append(("group", policy.max_group_frac * bankroll - exposure.get("group", {}).get(grp, 0.0)))
    for name, cap in caps:
        if stake > cap:
            stake = max(0.0, cap)
            notes.append(f"capped by {name} limit")
    return round(stake, 2), notes
