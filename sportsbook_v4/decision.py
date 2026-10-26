"""
sportsbook_v4/decision.py -- three SEPARATE outputs per game:

1. FINANCIAL NEED       which settlement outcome the synthetic book prefers,
                        straight from its accepted-wager exposure. Nothing
                        downstream may overwrite or hide it.
2. MARKET CONFIRMATION  independent observed-market evidence about that
                        side (consensus line/price movement; optional
                        external evidence such as the legacy market-response
                        regime, passed in as a plain dict). Reported, never
                        used to alter #1.
3. OFFICIAL PLAY        a governed decision under a versioned policy. Until
                        a policy is validated prospectively, the active
                        policy always returns PASS and records what a
                        candidate rule WOULD have done, for later grading.

A financial Need is NOT a claim that betting that side is profitable.
"""

from dataclasses import dataclass, field

from sportsbook_v4.market import implied_probability, representative_offer

CONFIRMATION_VERSION = "market_confirmation_m1_unvalidated"
POLICY_VERSION = "official_play_policy_m1_validation_pending"


@dataclass(frozen=True)
class FinancialNeed:
    status: str                    # NO_WAGERS | TIE | DIRECTIONAL
    side: str | None
    pnl_if_home_covers_all: float | None
    pnl_if_away_covers_all: float | None
    swing: float | None
    worst_case_pnl: float | None
    tickets: int
    handle: float
    as_of: str
    run_id: str


def financial_need_from_exposure(exp, as_of, run_id):
    return FinancialNeed(
        status=exp["status"], side=exp.get("financial_need_side"),
        pnl_if_home_covers_all=exp.get("pnl_if_home_covers_all"),
        pnl_if_away_covers_all=exp.get("pnl_if_away_covers_all"),
        swing=exp.get("pnl_swing"), worst_case_pnl=exp.get("worst_case_pnl"),
        tickets=exp["tickets"], handle=exp["handle"], as_of=as_of, run_id=run_id,
    )


@dataclass(frozen=True)
class MarketConfirmation:
    status: str                    # CONFIRMS | CONTRADICTS | NEUTRAL | UNAVAILABLE
    version: str
    open_home_spread: float | None
    current_home_spread: float | None
    movement_toward_need_points: float | None
    price_movement_toward_need_prob: float | None
    external_evidence: dict = field(default_factory=dict)


def market_confirmation(need_side, market, game_id, open_epoch, as_of_epoch,
                        threshold_points=0.5, threshold_prob=0.02, external_evidence=None):
    """
    Movement of the observed consensus from the first available quote to
    as_of. 'Toward the Need side' = the market made the Need side MORE
    expensive (more points to lay / fewer to get, or a costlier price),
    i.e. independent money appears to agree with the side the book needs.
    UNVALIDATED heuristic; its usefulness is a question for prospective
    grading, not an assumption.
    """
    ext = dict(external_evidence or {})
    if need_side not in ("home", "away"):
        return MarketConfirmation("UNAVAILABLE", CONFIRMATION_VERSION, None, None, None, None, ext)
    opened = representative_offer(market.latest_quotes(game_id, open_epoch))
    now = representative_offer(market.latest_quotes(game_id, as_of_epoch))
    if opened is None or now is None:
        return MarketConfirmation("UNAVAILABLE", CONFIRMATION_VERSION, None, None, None, None, ext)
    sign = -1.0 if need_side == "home" else 1.0       # home side gets more expensive as home_spread falls
    pts = sign * (now["home_spread"] - opened["home_spread"])
    prob = None
    if pts == 0:
        key = "home_price" if need_side == "home" else "away_price"
        if opened[key] is not None and now[key] is not None:
            prob = implied_probability(now[key]) - implied_probability(opened[key])
    if pts >= threshold_points or (prob is not None and prob >= threshold_prob):
        status = "CONFIRMS"
    elif pts <= -threshold_points or (prob is not None and prob <= -threshold_prob):
        status = "CONTRADICTS"
    else:
        status = "NEUTRAL"
    return MarketConfirmation(status, CONFIRMATION_VERSION, opened["home_spread"], now["home_spread"],
                              pts, None if prob is None else round(prob, 6), ext)


@dataclass(frozen=True)
class OfficialPlay:
    decision: str                  # PLAY | PASS
    side: str | None
    reasons: tuple
    policy_version: str
    candidate_rule_would_play: bool


def official_play(need, confirmation, min_swing=25000.0, min_tickets=200):
    """
    Active policy: validation pending -> always PASS. The candidate rule is
    evaluated and recorded so it can be graded prospectively before anyone
    decides to activate it. It deliberately cannot change `need`.
    """
    candidate = (
        need.status == "DIRECTIONAL" and need.swing is not None and need.swing >= min_swing
        and need.tickets >= min_tickets and confirmation.status == "CONFIRMS"
    )
    reasons = ["POLICY_NOT_VALIDATED"]
    if need.status != "DIRECTIONAL":
        reasons.append(f"NEED_{need.status}")
    if confirmation.status != "CONFIRMS":
        reasons.append(f"MARKET_{confirmation.status}")
    return OfficialPlay("PASS", None, tuple(reasons), POLICY_VERSION, candidate)


def assess_game(need, confirmation):
    """Bundle the three outputs; `need` is passed through untouched."""
    return {"financial_need": need, "market_confirmation": confirmation,
            "official_play": official_play(need, confirmation)}
