"""
edge_v4/ev.py -- expected value and the BET / WATCH / NO BET decision.

EV per 1 unit staked:
    EV = P(win) * net_profit - P(loss) * 1          (a push returns the stake)

A raw model-vs-market discrepancy is NOT an opportunity. A selection can
only reach BET when ALL of these hold:
  * the price is fresh and the quote is from a real, recorded sportsbook;
  * a no-vig market benchmark exists for the same line;
  * EV at the pessimistic end of the model's uncertainty interval clears
    the minimum edge (after an explicit operational-cost haircut);
  * the model does not disagree with the market by an implausible amount
    (large disagreements are far more often model error than market error);
  * the strategy that produced the probability has passed validation
    (validated_strategy=True). Unvalidated edges are capped at WATCH so they
    are paper-tracked, never staked.
NO BET is the default for every other case.
"""

from dataclasses import asdict, dataclass, field

from edge_v4 import pricing

BET, WATCH, NO_BET = "BET", "WATCH", "NO BET"


def expected_value(p_win, p_push, american):
    if not (0 <= p_win <= 1 and 0 <= p_push <= 1 and p_win + p_push <= 1 + 1e-12):
        raise ValueError("invalid probabilities")
    p_loss = max(0.0, 1.0 - p_win - p_push)
    return p_win * pricing.net_profit_per_unit(american) - p_loss


def breakeven_win_prob(p_push, american):
    """Win probability at which EV == 0, given a push probability."""
    b = pricing.net_profit_per_unit(american)
    return (1.0 - p_push) / (1.0 + b)


def spread_outcome_probs(margin_dist, line):
    """
    margin_dist: {integer home-minus-away margin: probability}.
    line: spread applied to the HOME side (e.g. -3.0 means home -3).
    Returns (p_cover, p_push, p_fail) for the home side.
    """
    total = sum(margin_dist.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"margin distribution sums to {total}")
    cover = push = 0.0
    for m, p in margin_dist.items():
        adj = m + line
        if abs(adj) < 1e-9:
            push += p
        elif adj > 0:
            cover += p
    return cover, push, max(0.0, 1.0 - cover - push)


@dataclass(frozen=True)
class DecisionPolicy:
    version: str = "policy_v4.0_conservative"
    min_edge_lo: float = 0.02         # EV/unit required at the pessimistic bound
    watch_edge: float = 0.01          # point EV/unit worth paper-tracking
    z: float = 1.645                  # one-sided 95% on model probability
    max_divergence: float = 0.08      # |p_model - p_market| beyond this => suspect model
    max_price_age_s: int = 15 * 60
    op_cost_per_unit: float = 0.0     # fees / transfer costs, per unit staked
    max_plausible_ev: float | None = None   # pessimistic EV above this => suspect stale/erroneous quote
    max_american: int | None = None         # longer prices (thin longshot markets) are not evaluated


# Live line shopping on real multi-book odds: same thresholds, plus plausibility guards.
LIVE_POLICY = DecisionPolicy(version="policy_v4.1_live_line_shop", max_plausible_ev=0.10, max_american=1000)


@dataclass
class Candidate:
    game_id: str
    market: str            # h2h | spreads | totals
    selection: str
    line: float | None
    book: str
    american: int
    price_age_s: float
    p_win_model: float
    p_push_model: float
    p_win_se: float        # standard error of the model's win probability
    p_win_market: float | None   # no-vig benchmark for the same selection/line
    p_push_market: float = 0.0
    supporting: list = field(default_factory=list)
    conflicting: list = field(default_factory=list)
    validated_strategy: bool = False
    observed_at: str = ""


def evaluate(c: Candidate, policy: DecisionPolicy = DecisionPolicy()):
    reasons = []
    ev_point = expected_value(c.p_win_model, c.p_push_model, c.american) - policy.op_cost_per_unit
    p_lo = max(0.0, c.p_win_model - policy.z * c.p_win_se)
    p_hi = min(1.0 - c.p_push_model, c.p_win_model + policy.z * c.p_win_se)
    ev_lo = expected_value(p_lo, c.p_push_model, c.american) - policy.op_cost_per_unit
    ev_hi = expected_value(p_hi, c.p_push_model, c.american) - policy.op_cost_per_unit
    implied = pricing.implied_prob(c.american)

    decision = NO_BET
    if policy.max_american is not None and c.american > policy.max_american:
        reasons.append(f"longshot price {c.american:+d} beyond +{policy.max_american}: thin market, not evaluated")
    elif c.price_age_s > policy.max_price_age_s:
        reasons.append(f"stale price ({int(c.price_age_s)}s old)")
    elif c.p_win_market is None:
        reasons.append("no no-vig market benchmark for this line")
    elif abs(c.p_win_model - c.p_win_market) > policy.max_divergence:
        reasons.append(
            f"model disagrees with market by {abs(c.p_win_model - c.p_win_market):.3f}"
            " > max_divergence; treated as probable model error"
        )
    elif policy.max_plausible_ev is not None and ev_lo > policy.max_plausible_ev:
        reasons.append(f"EV {ev_lo:.1%} even at the pessimistic bound is implausibly large: "
                       "check for a stale or erroneous quote")
    elif ev_lo >= policy.min_edge_lo:
        if c.validated_strategy:
            decision = BET
            reasons.append("uncertainty-adjusted EV clears threshold; strategy validated")
        else:
            decision = WATCH
            reasons.append("edge clears threshold but strategy is NOT validated: paper only")
    elif ev_point >= policy.watch_edge:
        decision = WATCH
        reasons.append("point EV positive but not robust to model uncertainty")
    else:
        reasons.append("no uncertainty-adjusted edge")

    return {
        **{k: v for k, v in asdict(c).items()},
        "implied_prob_with_vig": implied,
        "breakeven_win_prob": breakeven_win_prob(c.p_push_model, c.american),
        "edge_vs_market": None if c.p_win_market is None else c.p_win_model - c.p_win_market,
        "ev_per_unit": ev_point,
        "ev_interval": (ev_lo, ev_hi),
        "expected_roi": ev_point,
        "decision": decision,
        "reasons": reasons,
        "policy_version": policy.version,
    }
