# Bettor FADE/TAIL study: generated tables (DEVELOPMENT data; `python -m lab_v4.bettor_report`)

## Out-of-sample walk-forward results: production behaviour (seed 0)

112 tests (30 group rules x 2 sports x tail/fade). Benjamini-Hochberg FDR 10%: **0 significant**.


### Top 10 candidate FADE groups (by out-of-sample evidence; fading = betting the OTHER side)

| Group rule | Sport | Distinct bettors selected (all weeks) | Independent game-markets | Training (in-sample, last cutoff): record, ROI (z) | OUT-OF-SAMPLE record | OOS ROI at best executable price [95% CI] | OOS CLV vs close | OOS excess-vs-market z: seed 0 / 1 / 2 | one-sided p | Survives FDR? |
|---|---|---|---|---|---|---|---|---|---|---|
| BOTTOM15_REC_CASUAL | cfb | 28 | 79 | 21-161-2 -81.9% (z -7.84) | 60-45-2 | +13.1% [-7.8%, +32.8%] | -2.4% | +1.6 / +0.5 / +0.6 | 0.06 | no |
| BOTTOM15_spread_underdog | nfl | 12 | 15 | 0-68-0 -100.0% (z -2.23) | 10-5-0 | +30.8% [-19.9%, +73.2%] | -3.6% | +1.4 / +1.1 / -0.4 | 0.083 | no |
| TOP15_REC_CASUAL | cfb | 32 | 64 | 132-34-1 +78.1% (z 8.06) | 44-33-0 | +11.9% [-9.9%, +29.9%] | -2.2% | +1.3 / -1.2 / +0.3 | 0.103 | no |
| BOTTOM15_ALL | cfb | 29 | 76 | 22-161-2 -82.5% (z -8.11) | 58-47-2 | +9.4% [-12.2%, +29.6%] | -2.5% | +1.2 / -0.7 / +0.2 | 0.117 | no |
| BOTTOM15_spread_favourite | nfl | 19 | 36 | 10-137-1 -97.7% (z -3.16) | 22-14-0 | +19.1% [-8.8%, +52.0%] | -0.1% | +1.2 / -0.5 / -0.5 | 0.117 | no |
| BOTTOM15_spread_underdog | cfb | 17 | 29 | 1-77-0 -99.8% (z -3.04) | 17-12-0 | +15.1% [-25.5%, +49.6%] | -4.5% | +1.1 / +0.1 / -0.7 | 0.139 | no |
| TOP15_NARRATIVE_CHASER | cfb | 40 | 83 | 118-26-4 +77.2% (z 7.54) | 68-52-2 | +6.7% [-9.4%, +21.2%] | -2.4% | +1.0 / -1.6 / -1.0 | 0.149 | no |
| BOTTOM15_moneyline_favourite | cfb | 7 | 7 | 17-32-0 -50.5% (z -3.8) | 3-4-0 | +41.9% [-61.7%, +165.6%] | -8.7% | +0.9 / +1.9 / -0.6 | 0.183 | no |
| TOP15_SHARP | nfl | 7 | 8 | 59-31-0 +27.3% (z 2.4) | 7-3-2 | +29.0% [-23.1%, +79.1%] | +0.6% | +0.9 / +0.6 / -0.1 | 0.189 | no |
| TOP15_TEAM_LOYAL | nfl | 37 | 56 | 111-34-2 +80.6% (z 6.69) | 54-44-2 | +6.6% [-8.8%, +22.9%] | -1.6% | +0.8 / -1.8 / -1.0 | 0.21 | no |

### Top 10 candidate TAIL groups (by out-of-sample evidence; tailing = the SAME side)

| Group rule | Sport | Distinct bettors selected (all weeks) | Independent game-markets | Training (in-sample, last cutoff): record, ROI (z) | OUT-OF-SAMPLE record | OOS ROI at best executable price [95% CI] | OOS CLV vs close | OOS excess-vs-market z: seed 0 / 1 / 2 | one-sided p | Survives FDR? |
|---|---|---|---|---|---|---|---|---|---|---|
| TOP15_total_over | cfb | 15 | 21 | 81-0-0 +91.3% (z 6.76) | 17-4-0 | +56.6% [+20.4%, +84.7%] | -0.5% | +2.7 / -0.6 / +1.6 | 0.003 | no |
| TOP15_spread_underdog | cfb | 15 | 28 | 92-8-1 +79.0% (z 6.11) | 20-8-0 | +40.1% [+4.8%, +68.8%] | -0.4% | +2.2 / +1.2 / +1.2 | 0.014 | no |
| BOTTOM15_HIGH_STAKES_REC | nfl | 30 | 50 | 19-93-3 -76.6% (z -6.28) | 45-31-0 | +17.6% [-3.7%, +38.5%] | -1.4% | +1.7 / -0.3 / -0.5 | 0.046 | no |
| TOP15_moneyline_favourite | cfb | 6 | 10 | 47-0-0 +38.5% (z 1.94) | 9-1-0 | +31.2% [-1.2%, +55.0%] | -1.6% | +1.5 / +1.1 / +1.1 | 0.071 | no |
| TOP15_moneyline_favourite | nfl | 13 | 18 | 69-0-0 +55.1% (z 4.38) | 14-4-0 | +18.0% [-10.8%, +44.6%] | -3.7% | +1.2 / -1.2 / +0.4 | 0.11 | no |
| TOP15_UNDERDOG_CONTRARIAN | nfl | 24 | 45 | 120-37-0 +87.4% (z 7.32) | 33-36-1 | +12.8% [-13.3%, +40.7%] | -2.8% | +1.1 / -1.2 / -1.6 | 0.137 | no |
| TOP15_ALL | nfl | 30 | 61 | 197-59-2 +79.4% (z 6.8) | 59-52-1 | +6.6% [-9.8%, +24.4%] | -2.4% | +0.9 / +1.0 / +0.6 | 0.172 | no |
| TOP15_HIGH_STAKES_REC | cfb | 30 | 52 | 84-23-5 +65.8% (z 4.42) | 39-30-0 | +8.9% [-11.1%, +30.0%] | -2.6% | +0.9 / -1.2 / +0.3 | 0.175 | no |
| TOP15_total_under | nfl | 17 | 20 | 73-5-2 +81.7% (z 4.69) | 12-8-0 | +17.4% [-31.0%, +56.9%] | -1.7% | +0.9 / -1.0 / +0.5 | 0.189 | no |
| BOTTOM15_SHARP | cfb | 17 | 30 | 29-73-1 -61.6% (z -4.51) | 22-16-1 | +11.9% [-16.4%, +38.8%] | -1.2% | +0.8 / +1.7 / +0.2 | 0.202 | no |

### The literal question: best and worst bettors, followed or faded next week (all sports, seed 0)

| Group rule | Sport | Distinct bettors selected (all weeks) | Independent game-markets | Training (in-sample, last cutoff): record, ROI (z) | OUT-OF-SAMPLE record | OOS ROI at best executable price [95% CI] | OOS CLV vs close | OOS excess-vs-market z: seed 0 / 1 / 2 | one-sided p | Survives FDR? |
|---|---|---|---|---|---|---|---|---|---|---|
| TOP15_ALL | nfl | 30 | 61 | 197-59-2 +79.4% (z 6.8) | 59-52-1 | +6.6% [-9.8%, +24.4%] | -2.4% | +0.9 / +1.0 / +0.6 | 0.172 | no |
| TOP15_ALL | cfb | 35 | 72 | 118-23-1 +104.4% (z 7.18) | 37-48-3 | -16.0% [-35.7%, +5.6%] | -2.8% | -1.1 / +2.0 / +0.1 | 0.868 | no |
| TOP_DECILE_ALL | nfl | 690 | 88 | 4431-1636-92 +56.6% (z 9.71) | 236-242-10 | -2.5% [-7.0%, +2.3%] | -2.2% | -0.0 / -0.1 / +0.2 | 0.515 | no |
| TOP_DECILE_ALL | cfb | 593 | 145 | 3173-1162-67 +57.6% (z 11.14) | 289-317-8 | -6.8% [-11.0%, -2.5%] | -2.6% | -0.9 / -0.3 / -0.1 | 0.822 | no |
| BOTTOM15_ALL | nfl | 23 | 62 | 34-173-4 -86.8% (z -3.89) | 47-47-1 | -3.8% [-18.3%, +12.8%] | -1.6% | -0.2 / -0.4 / -0.3 | 0.585 | no |
| BOTTOM15_ALL | cfb | 29 | 76 | 22-161-2 -82.5% (z -8.11) | 58-47-2 | +9.4% [-12.2%, +29.6%] | -2.5% | +1.2 / -0.7 / +0.2 | 0.117 | no |
| BOTTOM_DECILE_ALL | nfl | 642 | 88 | 1520-4440-87 -68.1% (z -10.87) | 238-238-8 | -3.6% [-7.1%, +0.0%] | -2.1% | -0.3 / -1.2 / -0.5 | 0.628 | no |
| BOTTOM_DECILE_ALL | cfb | 558 | 144 | 1168-3364-53 -64.8% (z -9.93) | 308-280-3 | -1.3% [-5.1%, +2.0%] | -2.0% | +0.2 / +0.0 / +0.2 | 0.434 | no |

### Robustness: the same headline groups under other reasonable grouping methods (seed 0, out-of-sample)

| Variant | TOP15 tail NFL | TOP15 tail CFB | BOTTOM15 fade NFL | BOTTOM15 fade CFB | TOP decile tail (NFL / CFB) | BOTTOM decile fade (NFL / CFB) |
|---|---|---|---|---|---|---|
| ranked by evidence (z), k=15, from week 3 [primary] | 59-52-1 +6.6% z +0.9 | 37-48-3 -16.0% z -1.1 | 47-47-1 -3.8% z -0.2 | 58-47-2 +9.4% z +1.2 | 236-242-10 -2.5% z -0.0 / 289-317-8 -6.8% z -0.9 | 238-238-8 -3.6% z -0.3 / 308-280-3 -1.3% z +0.2 |
| k=10 | 45-30-1 +21.9% z +2.1 | 22-33-0 -23.9% z -1.5 | 38-34-0 +2.4% z +0.4 | 43-33-2 +10.4% z +1.1 | 236-242-10 -2.5% z -0.0 / 289-317-8 -6.8% z -0.9 | 238-238-8 -3.6% z -0.3 / 308-280-3 -1.3% z +0.2 |
| first eval week 5 | 42-36-1 +8.3% z +1.0 | 29-38-2 -16.5% z -1.0 | 23-29-0 -16.5% z -1.1 | 43-34-2 +8.7% z +0.9 | 156-161-5 -3.2% z -0.1 / 232-256-3 -6.8% z -0.8 | 162-160-6 -4.5% z -0.4 / 236-228-2 -3.6% z -0.4 |
| ranked by net $ P&L | 46-54-2 -11.9% z -1.0 | 32-27-0 +4.9% z +0.5 | 27-52-2 -27.5% z -2.2 | 30-33-1 -12.3% z -0.9 | 240-242-5 -2.6% z -0.1 / 283-303-4 -5.0% z -0.5 | 243-245-8 -3.2% z -0.2 / 317-286-4 -0.1% z +0.5 |
| ranked by ROI | 52-53-1 +0.9% z +0.3 | 33-41-1 -12.8% z -0.8 | 45-41-0 +1.3% z +0.3 | 50-46-1 +1.4% z +0.4 | 242-238-7 -0.9% z +0.2 / 286-306-7 -5.9% z -0.6 | 236-234-4 -2.8% z -0.2 / 309-279-3 +0.0% z +0.5 |
| largest 1% of wagers removed | 59-52-1 +6.6% z +0.9 | 36-46-2 -15.9% z -1.1 | 48-44-1 +3.6% z +0.5 | 58-47-2 +9.4% z +1.2 | 236-242-10 -2.5% z -0.0 / 289-314-8 -6.4% z -0.8 | 238-237-8 -3.4% z -0.3 / 308-280-3 -1.3% z +0.2 |

### Group consensus (Phase 4): independent selected bettors agreeing on the same side (seed 0, out-of-sample)

| Situation | Action | Independent game-markets | Record | ROI at best executable price | CLV vs close | Excess z |
|---|---|---|---|---|---|---|
| MIXED: best and worst bettors on the same side | tail | 231 | 481-512-8 | -4.3% | -2.1% | -0.59 |
| TAIL: >= 3 best bettors agree, no worst bettor with them | tail | 31 | 18-13-0 | +16.2% | -4.6% | 0.89 |
| DISAGREE: best bettors here, worst bettors on the other side (tail best) | tail | 17 | 8-9-1 | +3.2% | -2.0% | 0.18 |
| FADE: >= 3 worst bettors agree, no best bettor with them | fade | 24 | 12-15-0 | -22.6% | -1.2% | -1.18 |

### Stability across random seeds
Out-of-sample excess z has the same sign in all three seeds for **27 of 112** tests (pure chance: 25%).

FDR-significant tests in seed 1: 0, seed 2: 0; corrected V4r1 model (separate, seed 0): 0.


### Is there persistent bettor skill at all? (full season)

Empirical-Bayes between-bettor variance of excess return: tau^2 = 0.0000 (mean sampling variance 0.375).

| Sport / archetype | Bettors | Correlation of excess, odd vs even weeks |
|---|---|---|
| cfb|HIGH_STAKES_REC | 266 | -0.037 |
| cfb|NARRATIVE_CHASER | 1000 | -0.007 |
| cfb|REC_CASUAL | 3393 | -0.003 |
| cfb|SHARP | 215 | +0.122 |
| cfb|TEAM_LOYAL | 776 | -0.085 |
| cfb|UNDERDOG_CONTRARIAN | 465 | +0.019 |
| nfl|HIGH_STAKES_REC | 286 | -0.016 |
| nfl|NARRATIVE_CHASER | 1019 | +0.028 |
| nfl|REC_CASUAL | 3380 | -0.010 |
| nfl|SHARP | 171 | -0.011 |
| nfl|TEAM_LOYAL | 929 | -0.012 |
| nfl|UNDERDOG_CONTRARIAN | 493 | -0.098 |

### Side-type baselines in the evaluation weeks (every customer bet of that type, own price)

| Sport / market / side | Wagers | ROI | Excess vs decision fair |
|---|---|---|---|
| nfl|spread|favourite | 17,806 | -4.2% | +0.1% |
| nfl|spread|underdog | 12,770 | -1.6% | +2.3% |
| nfl|total|over | 9,451 | +3.0% | +7.1% |
| nfl|total|under | 7,044 | -17.8% | -14.2% |
| nfl|moneyline|favourite | 5,302 | +1.4% | +5.2% |
| nfl|moneyline|underdog | 4,752 | -16.6% | -12.8% |
| cfb|spread|favourite | 14,248 | -6.7% | -2.6% |
| cfb|spread|underdog | 10,140 | +2.1% | +5.9% |
| cfb|total|over | 8,040 | -4.9% | -1.1% |
| cfb|total|under | 6,334 | -9.9% | -6.4% |
| cfb|moneyline|favourite | 2,490 | -8.2% | -4.2% |
| cfb|moneyline|underdog | 3,719 | -25.0% | -21.8% |