"""
operations/validate_odds_credential.py

Owner-gated, single-request credential check for The Odds API.

DEFAULT IS A DRY RUN: no network request is made. It prints whether
THE_ODDS_API_KEY is visible to this process, its length, and a 12-char
SHA-256 fingerprint (the same fingerprint operations/run_collection.py logs on
every run, so it can be compared with the fingerprint in the production logs
before and after 2026-10-25 01:15 UTC), and the exact request it WOULD make.

LIVE MODE makes EXACTLY ONE request and only if ALL of the following hold:
  1. --live is given;
  2. --approval-token matches the token printed by today's dry run (UTC date
     + key fingerprint), proving the operator ran the dry run first;
  3. stdin is an interactive terminal (refuses under cron/systemd);
  4. the operator types the exact confirmation phrase.

The request goes to GET /v4/sports (the sport list), not the odds endpoint.
The Odds API documentation describes this endpoint as not counting against
the usage quota -- THE OWNER MUST RE-CONFIRM THAT ON THE CURRENT DOCS PAGE
BEFORE APPROVING (it could not be fetched from the investigation sandbox).
No retries, no redirects followed, 10 s timeout, nothing written to any DB.
Output: HTTP status, sanitized provider diagnostics (error_code, scrubbed
message, x-requests-* headers) or, on success, only the number of sports
returned. The key, the full URL and request headers are never printed.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors.provider_errors import extract_provider_error, safe_headers, scrub, summarize  # noqa: E402

SPORTS_URL = "https://api.the-odds-api.com/v4/sports"
CONFIRM_PHRASE = "I APPROVE ONE ODDS API REQUEST"


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def approval_token(key, today=None):
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(f"APPROVE-ODDS-VALIDATION|{today}|{fingerprint(key)}".encode()).hexdigest()[:10]


def dry_run(key, out=print):
    if not key:
        out("THE_ODDS_API_KEY: NOT SET in this process's environment (length=0). No request possible.")
        return 2
    out(f"THE_ODDS_API_KEY: present, length={len(key)}, fingerprint={fingerprint(key)}")
    out(f"Would make EXACTLY ONE request: GET {SPORTS_URL}?apiKey=<redacted> (no retries, no redirects, 10s timeout)")
    out("No request has been made. To proceed, the owner must approve and re-run with:")
    out(f"  --live --approval-token {approval_token(key)}   (valid for UTC date {datetime.now(timezone.utc):%Y-%m-%d})")
    return 0


def live_check(key, token, session=None, stdin=None, out=print):
    stdin = stdin or sys.stdin
    if not key:
        out("REFUSED: THE_ODDS_API_KEY not set.")
        return 2
    if token != approval_token(key):
        out("REFUSED: approval token missing or not valid for today's dry run.")
        return 3
    if not stdin.isatty():
        out("REFUSED: live validation must be run interactively by the owner (stdin is not a terminal).")
        return 4
    out(f"Type exactly '{CONFIRM_PHRASE}' to send ONE request:")
    if stdin.readline().strip() != CONFIRM_PHRASE:
        out("REFUSED: confirmation phrase not entered. No request made.")
        return 5

    import requests
    for name in ("urllib3", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)  # never log the request line
    session = session or requests.Session()
    try:
        resp = session.request("GET", SPORTS_URL, params={"apiKey": key}, timeout=(5, 10),
                               allow_redirects=False)
    except Exception as e:  # network error text can contain the URL: scrub it
        out(json.dumps({"request_sent": True, "result": "NETWORK_ERROR",
                        "detail": scrub(f"{type(e).__name__}: {e}", (key,))}))
        return 6
    if resp.status_code == 200:
        try:
            n = len(resp.json())
        except ValueError:
            n = None
        out(json.dumps({"request_sent": True, "http_status": 200, "result": "KEY_ACCEPTED",
                        "sports_returned": n, "safe_headers": safe_headers(resp.headers)}))
        return 0
    diag = extract_provider_error(resp, (key,))
    out(json.dumps({"request_sent": True, "http_status": resp.status_code, "result": diag["classification"],
                    "summary": summarize(diag)}))
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--live", action="store_true", help="make ONE real request (owner approval required)")
    ap.add_argument("--approval-token", default=None)
    args = ap.parse_args(argv)
    key = os.environ.get("THE_ODDS_API_KEY")
    if not args.live:
        return dry_run(key)
    return live_check(key, args.approval_token)


if __name__ == "__main__":
    sys.exit(main())
