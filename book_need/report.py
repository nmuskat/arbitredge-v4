"""
book_need/report.py

Human-readable RUN SLATE report, concise by default, per the approved
output schema. Detailed per-book/segment provenance is available via
verbose=True, never in the default view.
"""


def format_report(result, verbose=False):
    if result is None:
        return "No data for this game."

    lines = []
    lines.append(f"{result['away_team_id']} @ {result['home_team_id']}  (as_of: {result['as_of']})")
    lines.append("")

    pp = result["public_pressure"]
    lines.append("PUBLIC PRESSURE")
    if pp is None:
        lines.append("  Not yet computable at this cutoff.")
    else:
        hi_pct = pp["handle_intensity_percentile"]
        lines.append(f"  Expected attention: {'%.0f' % hi_pct + 'th percentile' if hi_pct is not None else 'N/A (thin slate)'}")
        direction = pp["side_preference_direction"]
        side_name = result["home_team_id"] if direction == "home" else (result["away_team_id"] if direction == "away" else "N/A")
        lines.append(f"  Expected recreational side: {side_name}")
        mag_pct = pp["side_preference_magnitude_percentile"]
        if pp["side_preference_magnitude_undefined"]:
            lines.append("  Side preference strength: UNDEFINED (degenerate slate -- no real separation)")
        elif mag_pct is None:
            lines.append("  Side preference strength: N/A")
        else:
            lines.append(f"  Side preference strength: {'%.0f' % mag_pct}th percentile")

    lines.append("")
    lines.append("MARKET RESPONSE")
    seg_name = result["current_segment_name"]
    if seg_name is None:
        lines.append("  No governing segment yet -- all segments NOT_YET_OBSERVED.")
    else:
        current_seg = next(s for s in result["segments"] if s["name"] == seg_name)
        lines.append(f"  Current governing regime: {current_seg.get('aggregate_regime')} "
                      f"(segment {seg_name}, {current_seg['status']}, breadth={current_seg.get('breadth')})")
        if result["fallback_occurred"]:
            lines.append("  NOTE: the most recent segment lacked usable data -- this reflects an earlier segment.")
        if result["contradiction_flag"]:
            lines.append("  NOTE: this regime contradicts the nearest earlier usable segment.")

    for seg in result["segments"]:
        if seg["status"] == "NOT_YET_OBSERVED":
            lines.append(f"  Segment {seg['name']} ({seg['start']} to {seg['end']}): NOT YET OBSERVED")

    lines.append("")
    lines.append("INFERRED BOOK NEED")
    side = result["inferred_book_need_side"]
    if side == "NOT_YET_EVALUABLE":
        lines.append("  NOT YET EVALUABLE")
        lines.append(f"  Earliest evaluation window begins: {result['earliest_evaluation_window_start']}")
    elif side in ("NO_CLEAR_NEED", "INSUFFICIENT_EVIDENCE"):
        lines.append(f"  {side}")
    else:
        side_name = result["home_team_id"] if side == "home" else result["away_team_id"]
        lines.append(f"  Side: {side_name}")
        lines.append(f"  Need strength: {result['need_strength']} (evidence-convergence label, NOT a probability)")
    lines.append(f"  Data quality: {result['data_quality']}")

    if verbose:
        lines.append("")
        lines.append("--- VERBOSE: full per-book/segment detail ---")
        for seg in result["segments"]:
            lines.append(f"  Segment {seg['name']}: status={seg['status']} horizon={seg.get('evaluation_horizon')}")
            for book, r in seg.get("per_book", {}).items():
                lines.append(f"    [{book}] {r}")

    return "\n".join(lines)
