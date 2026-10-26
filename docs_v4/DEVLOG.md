# ArbitrEdge V4 development log

Newest first. Test counts are from actual runs.

## 2026-10-26: P0 decision core; P2 market layer; dev-server package

- **edge_v4:** vig removal, EV with pushes, uncertainty-gated BET/WATCH/NO BET, fractional Kelly with caps, Odds API credit gate, validation statistics. 42 tests.
  - Mutation testing found a real defect: a bootstrap p-value of 0 nullified the multiple-testing correction. Fixed with (k+1)/(n+1).
  - The month boundary is now exact UTC (the provider resets credits on the 1st).
- **edge_v4.odds_client:** the only path from V4 to the Odds API, via the local gateway. V4 never holds the key.
- **market_v4:**
  - Odds API payload normaliser with provenance; malformed markets go to an anomaly log, never into the data.
  - Append-only quote store.
  - Point-in-time market state (OK / STALE / NO_DATA / CLOSED_KICKOFF / INCONSISTENT) with sharp-weighted no-vig consensus. The weights are an unvalidated assumption.
  - Observed closes are never imputed.
  - Monitors: fetch gaps, games affected by gaps, stale upcoming markets, thin markets, duplicate or swapped games, unmatched team names, single-book outlier moves.
  - 23 tests, including a replay of the 25–26 Oct outage shape (40.8 h gap closes markets; a game kicking off inside the gap has no observed close).
- **Dev-server package** (`arbitredge_v4_devhost/`): bootstrap, verification, Odds API gateway (6,000/month V4 cap, 2,000-credit V3 floor, live off by default), key and backup transfer, production disk survey and plan.
- **Process note:** a stale `.pyc` once made a restored file run its mutated version. All mutation checks were re-run with `PYTHONDONTWRITEBYTECODE=1`, and all 18 were caught.
- **Totals:** 129 V4 tests passing (64 sportsbook + 42 edge + 23 market).

**Blocked on owner:** droplet approval; GitHub repository; SIMS code.
