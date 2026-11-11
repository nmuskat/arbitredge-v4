# CF1: CFB line-follow tracker (frozen; research; prospective only)

Code: `lab_v4/cfb_follow.py`. Rule fingerprint `RULE_HASH` 4723d320… Frozen at the commit that adds this file.

## Rule
- **Games:** CFB spreads and totals, every collected game.
- **Move:** from our first collected snapshot to the last snapshot ≤ 1 h before kickoff (≤ 3 h old), the
  consensus fair line moved **≥ 1.0 point**.
- **Bet:** the side the line moved toward, at the best price available in that snapshot. One bet per
  game-market.
- **Grading:** final score at that price; closing value vs the consensus close.

## Development evidence (EXPLORATORY; 2026 already studied)
- 334-282-4 (54.2%), ROI +5.1% (95% CI −2.3% to +12.3%; p = 0.10).
- Closing value −1.75%.
- Consistent before and after 3 Oct (54.8% / 53.7%).
- **Not significant after multiple testing.**

## Judgement (fixed before any prospective record)
After the 2026–27 bowl season, with ≥ 150 prospective signals:
- **Abandon:** ROI ≤ 0% or closing value ≤ −2.0%.
- **Promising:** one-sided p < 0.05 on ROI.
- **Otherwise:** continue tracking into 2027.
- **Validated:** needs a second independent season. Never bet before then.

## Running it
- **Records:** `python -m lab_v4.cfb_follow record --market-db <store> --now <ISO> --out <dir>`. Run any time
  after a game's decision snapshot (kickoff − 1 h) and before kickoff. Records are write-once and self-checksummed.
- **Grading:** `python -m lab_v4.cfb_follow grade ... --records <dir>`.
- **Production:** live recording needs one step added to the existing site-build workflow (owner approval
  required; not done).
