"""
data_v4/observed_issue.py -- turn one "Observed betting split" issue form (body in env BODY) into validated
OBSERVED records and save them (data_v4.observed_splits). Prints a short reply for the issue.

    BODY="..." python -m data_v4.observed_issue --out DIR --issue 12
"""

import argparse
import datetime
import os
import re
import sys
from zoneinfo import ZoneInfo

from data_v4 import observed_splits as OS

LABELS = {
    "Sport": "sport", "Away team (as listed)": "away", "Home team (as listed)": "home",
    "Kickoff date, Eastern (YYYY-MM-DD)": "kickoff_et_date",
    "Sportsbook the split is for (e.g. draftkings, circa)": "sportsbook",
    "One sportsbook or several?": "aggregation", "Market": "market",
    "Line shown (home spread or total; blank for moneyline)": "line",
    "First side: home / over — tickets %, handle % (e.g. 62, 48)": "side_a",
    "Second side: away / under — tickets %, handle %": "side_b",
    "When you saw it (e.g. 2026-11-01 12:05 ET)": "observed_at",
    "Where it was published (site / page name)": "source", "Page URL (optional)": "source_url",
}
NO_RESPONSE = "_No response_"


def parse_body(body):
    """GitHub issue-form markdown: '### Label\\n\\nvalue' blocks."""
    out = {}
    for block in re.split(r"^### ", body or "", flags=re.M)[1:]:
        label, _, value = block.partition("\n")
        value = value.strip()
        if label.strip() in LABELS:
            out[LABELS[label.strip()]] = "" if value == NO_RESPONSE else value
    return out


def _time(text):
    """'2026-11-01 12:05 ET' (or an ISO time with a zone) -> UTC ISO."""
    t = text.strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})\s*(ET|EST|EDT)?", t, flags=re.I)
    if m:
        d = datetime.datetime.fromisoformat(f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}")
        d = d.replace(tzinfo=ZoneInfo("America/New_York"))
        return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return t


def _side(text):
    if not text:
        return "", ""
    parts = [p.strip().rstrip("%") for p in text.split(",")]
    if len(parts) != 2:
        raise OS.InvalidObservation(f"expected 'tickets %, handle %', got {text!r}")
    return parts[0], parts[1]


def records(fields, issue):
    sides = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}
    market = (fields.get("market") or "").lower()
    if market not in sides:
        raise OS.InvalidObservation(f"unknown market {market!r}")
    out = []
    for key, sel in zip(("side_a", "side_b"), sides[market]):
        t, h = _side(fields.get(key, ""))
        if not t and not h:
            continue
        row = {k: fields.get(k, "") for k in ("sport", "home", "away", "kickoff_et_date", "sportsbook", "aggregation",
                                               "line", "source", "source_url")}
        row.update(market=market, selection=sel, ticket_pct=t, handle_pct=h, observed_at=_time(fields.get("observed_at", "")),
                   collection_method="manual_entry", entered_by="owner (issue form)", notes=f"issue #{issue}")
        out.append(OS.normalize(row))
    if not out:
        raise OS.InvalidObservation("no percentages were entered")
    problems = OS.check_pairs(out)
    if problems:
        raise OS.InvalidObservation("; ".join(problems))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--issue", required=True)
    a = ap.parse_args(argv)
    try:
        recs = records(parse_body(os.environ.get("BODY", "")), a.issue)
    except OS.InvalidObservation as e:
        print(f"Not saved: {e}. Please open a new form with the correction.")
        return 1
    OS.write(recs, a.out)
    print(f"Saved {len(recs)} observed record(s) (OBSERVED, third-party; used only for calibration): " +
          "; ".join(f"{r['market']} {r['selection']} tickets {r['ticket_pct']}% handle {r['handle_pct']}%" for r in recs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
