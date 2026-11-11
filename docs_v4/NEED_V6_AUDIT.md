# NEED v6 audit: why the desk always agreed with Financial NEED, and why every NEED loses

Branch `dev/need-engine-v6.1` (v6.0 is unchanged on `dev/need-engine-v6`). Development data only: 2026 games
already studied. Nothing is deployed. Reproduce with `tools_v4/need_v6_audit_horizons.py` and
`tools_v4/need_v6_audit_tables.py`.

## 1. Trading Desk NEED = Financial NEED on 67 of 67: by construction, not by agreement

`need_v6/desk.py`, `_evaluate_position`:

```python
pb, pa = exposure.clean()          # book P&L when B / A covers every accepted number
need = a if pa > pb else b         # <- the desk's "NEED" side
```

The Financial NEED (`sportsbook_v4.settlement.exposure` / `total_exposure`) is the side with the better
clean-cover P&L, computed from the same accepted wagers. **The desk's side is therefore the Financial NEED side by
definition.** ACCEPT EXPOSURE only decides *whether* to keep that liability: settled price, material, within the
game limit, and EV ≥ 0.20 × SD. It never decides the direction. The 67/67 match is a tautology, not evidence that
two independent views agree.

**Can the desk ever choose a different direction under the v6.0 rules? No.** None of its actions creates
exposure against the customers' flow:

| Action | What sets its direction |
|---|---|
| MOVE (open / re-centre) | Market makers' line (Pinnacle, else the BetOnline group) |
| MOVE / DEFEND after respected action | Shades **against** the side the sharp bet, i.e. follows the sharp |
| BALANCE | Shades or closes the **heavy** side, i.e. reduces existing liability |
| ACCEPT EXPOSURE | Keeps the existing liability (direction = Financial NEED) |
| NO POSITION | None |

The desk's fair value **is** the market makers' price. It has no price of its own, so it has nothing to disagree
with.

**Tolerating liability vs having a pricing opinion.**
- *Tolerating liability:* customers create the position; the desk decides only whether to keep it or lay it off.
  That is what v6.0 does.
- *Having a pricing opinion:* the desk's fair value differs from the market's, and it deliberately prices to
  attract money to the side it believes is wrong, so its exposure direction can differ from where customers put
  the money.

The second needs an independent, validated estimate of the true price. v6.0 has none, and §3 finds none in our
data.

## 2. Performance diagnosis (frozen cards, development replay)

**How to read the tables.** Every NEED is graded at the **book's own frozen price**, which carries the desk's
4.5% margin plus any shade. So the expected ROI of betting any side the book offers is about −4% before luck.
CLV of about −4.2% everywhere confirms it: the price is −vig against the closing fair price in every group.

The **information test** is the last column: actual wins minus the wins that the market makers' fair price at the
close expected (pushes excluded), with a z-score. A NEED that knows something should be positive. **No group is
reliably positive.** The few groups with |z| ≈ 2 are what dozens of slices produce by chance. They were not
pre-registered and must not be tuned on.

CIs are 95%, bootstrapped by game. Four official records drop out of this table (322 vs 326) because the market
had no fresh maker quote at the close, so CLV could not be computed.

### NEED type (frozen, all)
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| official | 322 | 146-168-8 | -11.3% [-21.6%, -0.4%] | -4.1% [-4.2%, -3.9%] | -4.2% | -11.5 (z -1.30) |
| financial | 636 | 290-335-11 | -11.4% [-18.8%, -4.0%] | -4.2% [-4.3%, -4.1%] | -4.2% | -23.0 (z -1.84) |
| desk | 67 | 29-37-1 | -16.9% [-40.0%, +7.7%] | -4.2% [-4.6%, -3.8%] | -4.2% | -4.2 (z -1.04) |

### official: sport x market
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| nfl spread | 99 | 47-49-3 | -7.1% [-26.1%, +12.6%] | -3.9% [-4.2%, -3.6%] | -4.0% | -1.5 (z -0.31) |
| nfl total | 67 | 29-36-2 | -14.8% [-37.8%, +8.6%] | -4.3% [-4.6%, -3.9%] | -4.2% | -3.4 (z -0.85) |
| cfb spread | 105 | 50-52-3 | -6.1% [-23.6%, +13.0%] | -4.0% [-4.3%, -3.6%] | -4.2% | -0.9 (z -0.19) |
| cfb total | 51 | 20-31-0 | -25.4% [-51.4%, +0.6%] | -4.2% [-4.7%, -3.8%] | -4.2% | -5.6 (z -1.57) |

### financial: sport x market
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| nfl spread | 135 | 63-67-5 | -8.4% [-25.6%, +8.5%] | -4.0% [-4.2%, -3.7%] | -4.0% | -2.9 (z -0.50) |
| nfl total | 136 | 68-66-2 | -2.7% [-19.4%, +13.9%] | -4.3% [-4.5%, -4.1%] | -4.3% | +1.2 (z +0.21) |
| cfb spread | 172 | 79-90-3 | -10.4% [-25.1%, +2.8%] | -4.2% [-4.5%, -4.0%] | -4.3% | -5.3 (z -0.81) |
| cfb total | 193 | 80-112-1 | -20.5% [-32.9%, -7.0%] | -4.3% [-4.5%, -4.0%] | -4.2% | -16.1 (z -2.33) |

### official: desk action at freeze
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| NO POSITION | 252 | 117-128-7 | -8.8% [-20.4%, +3.4%] | -4.0% [-4.2%, -3.9%] | -4.2% | -5.7 (z -0.73) |
| ACCEPT EXPOSURE | 67 | 29-37-1 | -16.9% [-40.0%, +7.7%] | -4.2% [-4.6%, -3.8%] | -4.2% | -4.2 (z -1.04) |
| BALANCE | 3 | 0-3-0 | -100.0% [-100.0%, -100.0%] | -1.4% [-2.8%, -0.6%] | -1.8% | -1.6 (z -1.80) |

### financial: desk action at freeze
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| NO POSITION | 566 | 261-295-10 | -10.3% [-18.1%, -2.3%] | -4.2% [-4.3%, -4.1%] | -4.2% | -17.2 (z -1.46) |
| ACCEPT EXPOSURE | 67 | 29-37-1 | -16.9% [-40.0%, +7.7%] | -4.2% [-4.6%, -3.8%] | -4.2% | -4.2 (z -1.04) |
| BALANCE | 3 | 0-3-0 | -100.0% [-100.0%, -100.0%] | -1.4% [-2.8%, -0.6%] | -1.8% | -1.6 (z -1.80) |

### official: decision rule / source
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| TRADING DESK | 67 | 29-37-1 | -16.9% [-40.0%, +7.7%] | -4.2% [-4.6%, -3.8%] | -4.2% | -4.2 (z -1.04) |
| FINANCIAL | 255 | 117-131-7 | -9.9% [-21.3%, +1.9%] | -4.0% [-4.2%, -3.8%] | -4.2% | -7.3 (z -0.93) |

### financial vs desk
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| Financial NEED, desk also holds it | 67 | 29-37-1 | -16.9% [-40.0%, +7.7%] | -4.2% [-4.6%, -3.8%] | -4.2% | -4.2 (z -1.04) |
| Financial NEED, desk NONE | 569 | 261-298-10 | -10.8% [-18.6%, -3.3%] | -4.2% [-4.3%, -4.1%] | -4.2% | -18.8 (z -1.59) |
| Trading Desk NEED | 67 | 29-37-1 | -16.9% [-40.0%, +7.7%] | -4.2% [-4.6%, -3.8%] | -4.2% | -4.2 (z -1.04) |

### financial: price taken at frozen desk price
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| ≤ -125 | 10 | 8-2-0 | +42.3% [-10.8%, +78.4%] | -4.2% [-5.3%, -3.6%] | -4.0% | +2.6 (z +1.66) |
| -124..-111 | 275 | 124-144-7 | -13.4% [-24.5%, -2.6%] | -4.2% [-4.3%, -4.0%] | -4.1% | -13.1 (z -1.60) |
| -110..-101 | 336 | 150-182-4 | -12.3% [-21.8%, -2.4%] | -4.2% [-4.4%, -4.0%] | -4.3% | -13.5 (z -1.49) |
| +100 or better | 15 | 8-7-0 | +8.3% [-45.9%, +62.2%] | -4.3% [-5.1%, -3.4%] | -4.1% | +1.0 (z +0.49) |

### financial spreads: size of number
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| 0-3 | 71 | 27-41-3 | -23.7% [-46.7%, -2.0%] | -3.8% [-4.2%, -3.5%] | -4.1% | -6.9 (z -1.68) |
| 3.5-7 | 86 | 41-42-3 | -5.9% [-26.5%, +15.8%] | -4.2% [-4.5%, -3.8%] | -4.2% | -0.8 (z -0.17) |
| 7.5-14 | 75 | 38-36-1 | -3.6% [-25.4%, +18.3%] | -4.2% [-4.5%, -3.9%] | -4.2% | +0.3 (z +0.08) |
| 14.5+ | 75 | 36-38-1 | -6.4% [-29.9%, +14.0%] | -4.3% [-4.7%, -3.9%] | -4.2% | -0.8 (z -0.18) |

### financial spreads: NEED on favourite vs underdog
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| favourite (laying points) | 57 | 24-32-1 | -18.1% [-43.4%, +5.8%] | -4.5% [-4.9%, -4.2%] | -4.2% | -3.9 (z -1.05) |
| underdog (getting points) | 250 | 118-125-7 | -7.5% [-19.9%, +4.8%] | -4.0% [-4.2%, -3.8%] | -4.1% | -4.2 (z -0.54) |

### financial totals: over vs under
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| over | 100 | 48-52-0 | -7.6% [-26.9%, +11.7%] | -4.2% [-4.5%, -3.9%] | -4.3% | -1.8 (z -0.36) |
| under | 229 | 100-126-3 | -15.6% [-28.1%, -3.4%] | -4.3% [-4.5%, -4.1%] | -4.2% | -13.1 (z -1.74) |

### financial: status
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| MATERIAL | 322 | 146-168-8 | -11.3% [-21.6%, -0.4%] | -4.1% [-4.2%, -3.9%] | -4.2% | -11.5 (z -1.30) |
| MINOR | 314 | 144-167-3 | -11.5% [-21.2%, -1.4%] | -4.4% [-4.5%, -4.2%] | -4.3% | -11.5 (z -1.31) |

### financial: risk used at freeze
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| <25% | 483 | 218-258-7 | -12.5% [-21.0%, -4.5%] | -4.3% [-4.4%, -4.1%] | -4.2% | -20.1 (z -1.85) |
| 25-50% | 118 | 55-59-4 | -8.0% [-25.2%, +8.0%] | -4.1% [-4.3%, -3.8%] | -4.2% | -2.3 (z -0.42) |
| 50-100% | 32 | 17-15-0 | +1.6% [-33.6%, +37.1%] | -4.2% [-4.8%, -3.7%] | -4.1% | +0.9 (z +0.33) |
| >100% (over limit) | 3 | 0-3-0 | -100.0% [-100.0%, -100.0%] | -1.4% [-2.8%, -0.6%] | -1.8% | -1.6 (z -1.80) |

### financial: swing / handle (one-sidedness)
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| <20% | 142 | 68-72-2 | -7.2% [-23.5%, +8.7%] | -4.2% [-4.5%, -4.0%] | -4.2% | -2.1 (z -0.35) |
| 20-50% | 191 | 78-111-2 | -21.2% [-34.1%, -8.0%] | -4.3% [-4.5%, -4.0%] | -4.2% | -16.6 (z -2.41) |
| ≥50% | 303 | 144-152-7 | -7.1% [-17.9%, +3.2%] | -4.2% [-4.4%, -4.0%] | -4.2% | -4.4 (z -0.51) |

### financial: freeze time before kickoff
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| 30-60 min | 217 | 103-113-1 | -8.9% [-21.5%, +3.3%] | -4.1% [-4.3%, -4.0%] | -4.2% | -5.4 (z -0.74) |
| 1-3 h | 419 | 187-222-10 | -12.8% [-21.6%, -4.4%] | -4.3% [-4.4%, -4.1%] | -4.2% | -17.6 (z -1.74) |

### Financial NEED captured at T-48/24/6/1 h (each graded at the desk's price at that time)
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| T-48h | 575 | 294-270-11 | -0.5% [-8.1%, +7.7%] | -3.8% [-4.2%, -3.3%] | -4.2% | +9.8 (z +0.83) |
| T-24h | 613 | 303-299-11 | -3.8% [-11.1%, +3.6%] | -3.9% [-4.3%, -3.6%] | -4.2% | +0.1 (z +0.01) |
| T-6h | 635 | 286-337-12 | -12.3% [-20.0%, -5.0%] | -4.1% [-4.4%, -3.8%] | -4.2% | -27.0 (z -2.16) |
| T-1h | 636 | 286-339-11 | -12.6% [-19.8%, -5.2%] | -4.2% [-4.3%, -4.0%] | -4.2% | -27.2 (z -2.18) |

### Market-maker 'pricing opinion' side (Pinnacle vs rec gap ≥ 0.5 pt), at desk price
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| pricing-opinion side | 56 | 29-24-3 | +1.0% [-22.4%, +24.2%] | -4.4% [-4.8%, -3.9%] | -4.0% | +1.6 (z +0.45) |

### Financial NEED vs market-maker pricing opinion
| Group | n | W-L-P | ROI at frozen price [95% CI] | CLV vs close fair [95% CI] | EV at time vs fair | Wins vs fair expectation |
|---|---|---|---|---|---|---|
| agree | 30 | 17-11-2 | +11.1% [-21.1%, +43.5%] | -4.5% [-5.0%, -3.9%] | -4.0% | +2.5 (z +0.94) |
| disagree | 26 | 13-12-1 | +2.1% [-34.3%, +38.3%] | -4.2% [-4.9%, -3.5%] | -4.4% | +0.8 (z +0.34) |
**Seed sensitivity.** Re-running the same engine with a different random draw of customer behaviour (an
accidental version-label seed change, since fixed):

| Type | Seed A | Seed B |
|---|---|---|
| Official | 149-168-9, −10.4%, wins vs fair −9.9 | 141-158-7, −10.1%, −9.1 |
| Financial | 296-339-12, −11.0%, −22.0 | 308-327-12, −7.4%, −10.0 |
| Tolerated exposure | 29-37-1, −16.9%, −4.3 | 29-41-0, −21.8%, −6.4 |

Which games get a NEED, and on which side, depends on the simulated customers' random draw. A 12-win swing from
the seed alone is about the same size as every "pattern" in the tables above.

### Root causes of the negative ROI
1. **Grading at the book's own price.** −vig is the expected result for *any* side at that price. ROI at the
   frozen price can never show positive information by itself.
2. **The NEED is the side customers did not bet.** The book is positive EV *because* customers pay the margin.
   Our synthetic customers prefer favourites and overs (unders made 229 of 329 total NEEDs; underdogs 250 of 307
   spread NEEDs). The NEED side carries no market information beyond that preference.
3. **Results vs fair: −23 wins over 625 decided Financial NEEDs (z −1.84; same engine, seed B: −10).** That is negative but
   not significant, and smaller than the seed noise. It is concentrated in CFB totals (unders −16.1, z −2.33).
   The likeliest reading is a 2026 CFB scoring environment above the closing totals, which says nothing about the
   desk. It is **not** a reason to fade NEEDs: not pre-registered, development data.
4. **The desk adds no information** (§1). Tolerated exposure 29-37-1 is a subset of Financial NEED with the same
   CLV (−4.2%).
5. Not a cause: the book itself made +$362 k on $11.87 M handle (MODELED), because the margin works.
   "The NEED loses" and "the book wins" are the same fact.

## 3. Independent trading intelligence: inventory

| Input | Status | Evidence |
|---|---|---|
| Engine C NFL model (sims_v4) | **No signal** | `ENGINE_C_REPORT.md`: the independent model is significantly worse than the market (worse than a coin flip on spreads); the anchored model is the market (coefficient 0.92, team features add nothing). 2026: 106 games, within noise |
| Market-maker disagreement (Pinnacle vs rec gap ≥ 0.5) | **No signal** | Tier A A6/A7: no executable edge, CLV ≈ −vig, every price source tied on outcomes. Here: pricing-opinion side 29-24-3, +1.6 wins vs fair (z +0.45, n 56). The desk already prices **at** Pinnacle, so the gap is already in its price |
| Who moved first / steam | **Unmeasurable** | Leadership unresolvable at the 101-min median cadence; 56% of moves tied (Tier A A3) |
| Injury / lineup news | **Unavailable** | No feed integrated (`sharp_v4/signals.py` reports it UNAVAILABLE) |
| Real betting splits | **Unavailable** | No free provider (provider research); a paid feed needs owner approval |
| Synthetic sharp customers | **Circular** | They bet only against the desk's deviation from Pinnacle, so they carry no information about outcomes |
| Synthetic customer flow (the Need itself) | **No signal** | §2 |
| CFB: Pinnacle converges toward the rec books (Tier A A5, slope 0.68) | **Hypothesis about fair value, not edge** | Suggests a consensus fair may beat Pinnacle-only in CFB. Needs a pre-registered prospective CLV test before any use |

**Conclusion: no validated independent information exists.** The desk must report that and return NONE for any
informed directional position.

## 4. Smallest justified change (v6.1, on `dev/need-engine-v6.1`)

Same engine, same customers, same prices, same wagers. The replay reproduces v6.0's wagers, handle, book P&L,
desk actions and Financial NEED exactly. Only the meaning and labelling of the desk's output change:

1. **Trading Desk NEED = an informed directional position only.** It needs an independent signal listed in
   `CONFIG["validated_signals"]` (empty), so it is **NONE** with the reason printed. Listing an unimplemented
   signal raises an error (tested).
2. **New field: EXPOSURE TOLERANCE.** The desk's ACCEPT EXPOSURE decision, labelled "inherited from Financial NEED
   (liability tolerance, not an opinion)". A test asserts it always equals the Financial NEED side.
3. **Official policy `financial_first_v2`.** Material informed desk position → TRADING DESK (impossible today);
   else material Financial → FINANCIAL; else NONE. `desk_material_first_v1` stays selectable (tested). Headline
   sides are unchanged (149-168-9); the 67 former "TRADING DESK" headlines now say FINANCIAL, which is what they
   always were.
4. **Grading shows the information test.** Each card stores the fair no-push cover probability of each side.
   Validation reports wins vs fair expectation next to ROI, so −vig is not mistaken for skill or for failure.
5. **Simulation seed separated from the version label** (`SIM_SEED`), so a labelling release never reshuffles
   customers.

**Not done, deliberately:** no new desk opinion, no fading or following of any sliced pattern, no parameter tuned
to 2026 results.

**What a real deliberate desk would need:** a prospectively validated price source that differs from Pinnacle.
The only candidate in our data is the CFB fair-value construction hypothesis; it would need a frozen protocol
and owner approval. Until then the honest product is a book that manages liability (BALANCE / tolerate) and
reports that it has no opinion.
