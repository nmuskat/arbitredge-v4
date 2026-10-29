"""
sportsbook_v4/cohorts.py -- PRE-REGISTERED prospective paper-tracking cohorts (WATCH / paper only).

Registered 2026-10-29 by the owner's instruction, before the next eligible game. Rules are frozen:
  * a cohort applies only to snapshots taken at or after its registered_at;
  * membership is evaluated WHEN THE SNAPSHOT IS TAKEN and stored inside the immutable snapshot, so a
    selection can never be revised after results are known;
  * changing any rule means a NEW cohort id with a new registered_at (tests pin REGISTRY_HASH).

Every cohort reads only the snapshot's modeled sportsbook figures (pressure, Need from the book's P&L,
the recorded offer). Nothing manufactures public pressure or forces a Need side.

C1 is the primary strategy (the owner's original five-rule filter). C2-C4 are EXPLORATORY comparisons;
results are reported with a multiple-testing (Holm) adjustment across all four.
"""

import math

from sportsbook_v4.util import json_hash

REGISTERED_AT = "2026-10-29T20:00:00Z"

COHORTS = {
    "C1_selective_need": {
        "name": "Original selective Need strategy",
        "role": "PRIMARY",
        "registered_at": REGISTERED_AT,
        "pick": "need_side",
        "rules": {
            "need": "DIRECTIONAL (book P&L differs between the two sides covering)",
            "weekly_spread_handle_rank": "top 25% of the sport's weekly run (rank <= ceil(0.25 x games))",
            "min_spread_wagers": 100,
            "max_single_bettor_share": 0.25,
            "min_rec_handle_share_one_side": 0.70,
            "rec_ticket_share_same_side": "> 0.50",
            "alignment": "ALIGNED (the side fading the recreational majority is the side the book needs)",
        },
    },
    "C2_high_handle_need": {
        "name": "High-handle Need strategy",
        "role": "EXPLORATORY",
        "registered_at": REGISTERED_AT,
        "pick": "need_side",
        "rules": {
            "need": "DIRECTIONAL",
            "weekly_spread_handle_rank": "top 25% of the sport's weekly run",
            "min_spread_wagers": 100,
            "max_single_bettor_share": 0.25,
            "public_pressure": "not required (any split; Need may sit on either side)",
        },
    },
    "C3_broad_rec_pressure_need": {
        "name": "Broad recreational public-pressure Need strategy",
        "role": "EXPLORATORY",
        "registered_at": REGISTERED_AT,
        "pick": "need_side",
        "rules": {
            "need": "DIRECTIONAL",
            "min_spread_wagers": 100,
            "max_single_bettor_share": 0.25,
            "min_rec_handle_share_one_side": 0.60,
            "min_rec_ticket_share_same_side": 0.55,
            "alignment": "ALIGNED",
            "weekly_spread_handle_rank": "not required",
        },
    },
    "C4_rec_vs_sharp_disagreement": {
        "name": "Recreational-versus-sharp disagreement strategy",
        "role": "EXPLORATORY",
        "registered_at": REGISTERED_AT,
        "pick": "sharp_side (the side the simulated SHARP archetype backed); the book's Need is recorded alongside",
        "rules": {
            "min_spread_wagers": 100,
            "min_rec_handle_share_one_side": 0.60,
            "min_sharp_handle_share_of_game": 0.10,
            "min_sharp_bettors": 2,
            "min_sharp_handle_share_other_side": 0.60,
        },
    },
}

REGISTRY_HASH = json_hash(COHORTS)


def _share(p, side, key):
    return p["sides"][side][key]


def evaluate(snapshot_fields):
    """{cohort_id: {eligible, pick_side, failed: [...]}} from one snapshot's own figures.
    snapshot_fields: pressure, qualification, need, alignment, week_handle_rank, week_games."""
    p, q, nd = snapshot_fields["pressure"], snapshot_fields["qualification"], snapshot_fields["need"]
    rank, n, al = snapshot_fields["week_handle_rank"], snapshot_fields["week_games"], snapshot_fields["alignment"]
    need_side = nd.get("side")
    top = rank <= max(1, math.ceil(0.25 * n))
    wagers_ok = p["wagers"] >= 100
    conc_ok = p["largest_bettor_share"] is not None and p["largest_bettor_share"] <= 0.25
    pub = q.get("public_side")
    rec_h = _share(p, pub, "rec_handle_share") if pub else None
    rec_t = _share(p, pub, "rec_ticket_share") if pub else None

    def res(checks, pick):
        failed = [k for k, ok in checks if not ok]
        return {"eligible": not failed, "pick_side": pick if not failed else None, "failed": failed}

    out = {
        "C1_selective_need": res([("need_directional", need_side is not None), ("filter_qualified", q["qualified"]),
                                  ("aligned", al == "ALIGNED")], need_side),
        "C2_high_handle_need": res([("need_directional", need_side is not None), ("weekly_handle_top_25pct", top),
                                    ("min_100_spread_wagers", wagers_ok), ("no_bettor_over_25pct", conc_ok)], need_side),
        "C3_broad_rec_pressure_need": res([
            ("need_directional", need_side is not None), ("min_100_spread_wagers", wagers_ok),
            ("no_bettor_over_25pct", conc_ok), ("rec_handle_60pct_one_side", rec_h is not None and rec_h >= 0.60),
            ("rec_tickets_55pct_same_side", rec_t is not None and rec_t >= 0.55), ("aligned", al == "ALIGNED")],
            need_side),
    }
    sharp_h = p.get("sharp_handle") or 0.0
    other = ("away" if pub == "home" else "home") if pub else None
    sharp_other = (p["sides"][other]["sharp_handle"] / sharp_h) if (other and sharp_h) else None
    out["C4_rec_vs_sharp_disagreement"] = res([
        ("min_100_spread_wagers", wagers_ok), ("rec_handle_60pct_one_side", rec_h is not None and rec_h >= 0.60),
        ("sharp_10pct_of_handle", (p.get("sharp_handle_share") or 0) >= 0.10),
        ("min_2_sharp_bettors", (p.get("sharp_bettors") or 0) >= 2),
        ("sharp_60pct_other_side", sharp_other is not None and sharp_other >= 0.60)], other)
    for cid, r in out.items():
        r["need_side"] = need_side
        r["pick_is_need_side"] = (r["pick_side"] == need_side) if r["pick_side"] else None
    return out
