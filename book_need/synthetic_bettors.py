"""
ArbitrEdge Synthetic Bettor Population V1.

Creates a deterministic hypothetical sportsbook customer ledger from
ArbitrEdge's independently-derived bettor-pressure signal.

IMPORTANT:
This is a behavioral simulation, not observed sportsbook betting data.
Its calibration against real historical ticket/money splits is a
separate validation task.
"""

import hashlib
import random

VERSION = "synthetic_bettors_v1"
DEFAULT_BETTORS = 10000


def _seed(game_id, public_side, sp):
    raw = f"{game_id}|{public_side}|{sp:.6f}|{VERSION}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def simulate_bettor_ledger(
    canonical_game_id,
    public_side,
    side_preference_percentile,
    handle_intensity_percentile=None,
    bettor_count=DEFAULT_BETTORS,
):
    if public_side not in ("home", "away"):
        return None
    if side_preference_percentile is None:
        return None

    sp = float(side_preference_percentile)
    hi = 50.0 if handle_intensity_percentile is None else float(handle_intensity_percentile)

    rng = random.Random(_seed(canonical_game_id, public_side, sp))

    # Convert relative Side Preference strength into a plausible customer
    # ticket probability. Deliberately bounded: even extreme modeled public
    # pressure does not imply 90-100% of actual bettors take one side.
    public_ticket_prob = 0.50 + 0.22 * (sp / 100.0)
    public_ticket_prob = _clamp(public_ticket_prob, 0.50, 0.72)

    public_tickets = 0
    other_tickets = 0
    public_dollars = 0.0
    other_dollars = 0.0

    # Higher market activity modestly increases typical simulated stake size.
    activity_multiplier = 0.85 + 0.30 * (hi / 100.0)

    for _ in range(int(bettor_count)):
        takes_public = rng.random() < public_ticket_prob

        # Most customers are recreational-sized bettors, with a smaller
        # right tail of materially larger wagers.
        u = rng.random()
        if u < 0.72:
            stake = rng.uniform(20, 125)
        elif u < 0.94:
            stake = rng.uniform(125, 500)
        elif u < 0.992:
            stake = rng.uniform(500, 2000)
        else:
            stake = rng.uniform(2000, 7500)

        # Popular-side customers receive a modest average stake-size skew.
        # This allows money share to differ from ticket share without
        # hard-coding an extreme discrepancy.
        if takes_public:
            stake *= 1.0 + 0.10 * (sp / 100.0)

        stake *= activity_multiplier

        if takes_public:
            public_tickets += 1
            public_dollars += stake
        else:
            other_tickets += 1
            other_dollars += stake

    total_tickets = public_tickets + other_tickets
    total_dollars = public_dollars + other_dollars

    ticket_share = 100.0 * public_tickets / total_tickets
    money_share = 100.0 * public_dollars / total_dollars

    # Exposure imbalance measures how one-sided our fictional sportsbook is.
    ticket_imbalance = abs(ticket_share - 50.0) * 2.0
    money_imbalance = abs(money_share - 50.0) * 2.0
    exposure_score = 0.40 * ticket_imbalance + 0.60 * money_imbalance

    book_need_side = "away" if public_side == "home" else "home"

    return {
        "version": VERSION,
        "bettor_count": total_tickets,
        "public_side": public_side,
        "book_need_side": book_need_side,
        "public_ticket_share": round(ticket_share, 1),
        "public_money_share": round(money_share, 1),
        "public_tickets": public_tickets,
        "other_tickets": other_tickets,
        "public_dollars": round(public_dollars, 2),
        "other_dollars": round(other_dollars, 2),
        "synthetic_handle": round(total_dollars, 2),
        "exposure_score": round(exposure_score, 1),
    }
