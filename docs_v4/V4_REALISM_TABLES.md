# V4 realism audit: generated tables (DEVELOPMENT data; `python -m lab_v4.realism_report`)

## Accounting reconciliation (independent re-settlement of every wager)

| Ledger | Wagers | Unsettled | Result mismatches | P&L mismatches |
|---|---|---|---|---|
| A_all_s1 | 147,026 | 0 | 0 | 0 |
| A_all_s2 | 146,576 | 0 | 0 | 0 |
| A_hs_s1 | 146,345 | 0 | 0 | 0 |
| A_hs_s2 | 146,227 | 0 | 0 | 0 |
| A_otherrec_s1 | 146,824 | 0 | 0 | 0 |
| A_otherrec_s2 | 146,555 | 0 | 0 | 0 |
| A_s0 | 146,205 | 0 | 0 | 0 |
| A_sharp_s1 | 146,265 | 0 | 0 | 0 |
| A_sharp_s2 | 146,207 | 0 | 0 | 0 |
| RMAXWIN_s0 | 146,206 | 0 | 0 | 0 |
| RSHARP_s0 | 144,018 | 0 | 0 | 0 |
| R_s0 | 144,020 | 0 | 0 | 0 |
| R_s1 | 144,753 | 0 | 0 | 0 |
| R_s2 | 144,421 | 0 | 0 | 0 |

## Book economics: expected hold (at decision-time market-maker fair) vs realized

| Ledger | Scope | Wagers | Handle | Book P&L | Expected hold | Realized hold |
|---|---|---|---|---|---|---|
| A_s0 | nfl | 82,958 | $9,029,378 | $519,248 | +4.02% | +5.75% |
| A_s0 | cfb | 63,247 | $7,058,456 | $496,620 | +3.58% | +7.04% |
| A_s0 | all | 146,205 | $16,087,834 | $1,015,868 | +3.83% | +6.31% |
| A_all_s1 | nfl | 83,160 | $9,211,268 | $474,848 | +4.06% | +5.16% |
| A_all_s1 | cfb | 63,866 | $7,435,909 | $489,248 | +3.71% | +6.58% |
| A_all_s1 | all | 147,026 | $16,647,177 | $964,096 | +3.91% | +5.79% |
| A_all_s2 | nfl | 82,789 | $8,713,686 | $593,114 | +4.05% | +6.81% |
| A_all_s2 | cfb | 63,787 | $7,189,323 | $575,974 | +3.64% | +8.01% |
| A_all_s2 | all | 146,576 | $15,903,009 | $1,169,088 | +3.87% | +7.35% |
| RSHARP_s0 | nfl | 82,063 | $8,338,439 | $442,357 | +4.21% | +5.31% |
| RSHARP_s0 | cfb | 61,955 | $5,896,863 | $474,709 | +4.02% | +8.05% |
| RSHARP_s0 | all | 144,018 | $14,235,302 | $917,067 | +4.14% | +6.44% |
| RMAXWIN_s0 | nfl | 82,959 | $9,009,085 | $507,367 | +4.02% | +5.63% |
| RMAXWIN_s0 | cfb | 63,247 | $6,963,739 | $390,370 | +3.80% | +5.61% |
| RMAXWIN_s0 | all | 146,206 | $15,972,824 | $897,737 | +3.93% | +5.62% |
| R_s0 | nfl | 82,065 | $8,320,229 | $433,741 | +4.22% | +5.21% |
| R_s0 | cfb | 61,955 | $5,816,958 | $384,333 | +4.17% | +6.61% |
| R_s0 | all | 144,020 | $14,137,187 | $818,075 | +4.20% | +5.79% |
| R_s1 | nfl | 82,217 | $8,446,360 | $345,659 | +4.27% | +4.09% |
| R_s1 | cfb | 62,536 | $6,238,507 | $470,741 | +4.20% | +7.55% |
| R_s1 | all | 144,753 | $14,684,867 | $816,399 | +4.24% | +5.56% |
| R_s2 | nfl | 81,944 | $8,055,730 | $535,026 | +4.25% | +6.64% |
| R_s2 | cfb | 62,477 | $5,946,645 | $519,391 | +4.10% | +8.73% |
| R_s2 | all | 144,421 | $14,002,375 | $1,054,417 | +4.19% | +7.53% |

## Sharp customers: decision quality (no later information)

| Ledger | Sport | Wagers | Handle | Mean EV at decision vs maker no-vig fair | Share +EV | CLV at professional close (spread/total) | ROI (outcome) |
|---|---|---|---|---|---|---|---|
| A_s0 | nfl | 990 | $793,027 | -2.22% | 16% | -2.87% | -11.03% |
| A_s0 | cfb | 1,516 | $1,338,120 | -1.85% | 19% | -3.05% | -1.43% |
| RSHARP_s0 | nfl | 55 | $98,301 | +5.14% | 100% | +5.47% | +32.47% |
| RSHARP_s0 | cfb | 122 | $170,259 | +3.30% | 100% | +1.06% | +7.26% |
| R_s0 | nfl | 55 | $97,101 | +5.14% | 100% | +5.47% | +34.11% |
| R_s0 | cfb | 122 | $169,051 | +3.30% | 100% | +1.06% | +8.02% |
| R_s1 | nfl | 49 | $88,489 | +5.83% | 100% | +5.17% | +35.90% |
| R_s1 | cfb | 113 | $153,350 | +3.73% | 100% | +2.22% | +2.95% |
| R_s2 | nfl | 53 | $88,189 | +5.95% | 100% | +5.14% | +24.14% |
| R_s2 | cfb | 119 | $181,162 | +4.45% | 100% | +2.73% | +1.70% |

## Moneyline: expected vs realized hold (game-level variance; power de-vig)

| Ledger | Sport | Wagers | Handle | Long-shot (+500 or longer) handle share | Largest potential win | Expected hold (power) | Expected hold (proportional) | Realized hold | SD of hold | z |
|---|---|---|---|---|---|---|---|---|---|---|
| A_s0 | cfb | 7,522 | $808,672 | 24.6% | $269,850 | +10.53% | +1.53% | +26.19% | 14.9% | 1.05 |
| A_s0 | nfl | 14,601 | $1,578,129 | 0.5% | $24,242 | +4.09% | +3.69% | +9.35% | 4.6% | 1.14 |
| RMAXWIN_s0 | cfb | 7,522 | $703,866 | 16.1% | $10,000 | +8.15% | +3.21% | +15.09% | 8.4% | 0.82 |
| RMAXWIN_s0 | nfl | 14,601 | $1,552,573 | 0.5% | $10,000 | +4.06% | +3.71% | +8.73% | 4.2% | 1.1 |
| R_s0 | cfb | 7,487 | $688,422 | 15.9% | $10,000 | +8.11% | +3.37% | +15.21% | 8.4% | 0.85 |
| R_s0 | nfl | 14,555 | $1,521,100 | 0.5% | $10,000 | +4.29% | +3.96% | +9.01% | 4.0% | 1.18 |
| R_s1 | cfb | 7,553 | $704,228 | 16.3% | $10,000 | +7.51% | +3.25% | +21.47% | 8.6% | 1.63 |
| R_s1 | nfl | 14,372 | $1,610,050 | 0.6% | $10,000 | +4.35% | +3.90% | +2.10% | 4.4% | -0.51 |
| R_s2 | cfb | 7,597 | $691,569 | 15.8% | $10,000 | +7.60% | +3.07% | +26.20% | 9.1% | 2.04 |
| R_s2 | nfl | 14,378 | $1,478,404 | 0.4% | $10,000 | +4.38% | +4.09% | +9.47% | 3.9% | 1.3 |

## Financial NEED at T-1h: concentration and materiality

| Ledger | Game-markets with NEED | Material | Median share of handle in the single largest wager |
|---|---|---|---|
| A_s0 | 668 | 377 | 21.9% |
| RSHARP_s0 | 668 | 368 | 24.6% |
| RMAXWIN_s0 | 668 | 377 | 21.9% |
| R_s0 | 668 | 368 | 24.7% |

## NEED stability: which randomness moves the T-1h NEED side? (base = production seed 0; others re-keyed)

| Experiment (what is re-randomised) | Runs | Same side in all runs | Material NEED same side in all runs | Mean pairwise reversal | Material frequency | Median CV of signed liability |
|---|---|---|---|---|---|---|
| everyone (arrivals, choices, stakes) | 3 | 37% | 43% | 42% | 59% | 1.28 |
| high-stakes recreational only | 3 | 39% | 45% | 41% | 59% | 1.22 |
| other recreational only | 3 | 91% | 100% | 6% | 57% | 0.14 |
| sharps only | 3 | 89% | 98% | 7% | 57% | 0.14 |
| V4r1 corrected, everyone | 3 | 32% | 36% | 45% | 56% | 1.37 |

V4 (seed 0) vs V4r1 (seed 0, common random numbers): T-1h NEED side differs in 105 of 668 game-markets (16%); 39 of those are material in either.