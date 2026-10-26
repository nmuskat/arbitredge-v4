# Decision pipeline: backtests and controls (2026-10-26)

Every bet passes through the same path: candidate → `ev.evaluate` → ledger (append-only) → grade → `validation.edge_evidence_summary`. Strategies are registered before evaluation; the registry count is the multiple-testing denominator (4 real-data strategies + 3 synthetic controls registered so far).

## 1. Framework controls (synthetic market with known truth, 3,000 games each)

| Scenario | What it tests | Bets | Realized ROI (95% CI) | Mean CLV | Gate |
|---|---|---|---|---|---|
| Positive control: soft books mis-price around an accurate sharp consensus | Can the framework find a real edge? | 5,293 | +7.7% (+4.8% to +10.5%) | +4.0% | **SUPPORTED** ✔ |
| Negative control: soft books = consensus + vig | Does it invent edges? | **0** | — | — | no bets ✔ |
| Illusion: soft books accurate, consensus noisy | Does it certify false edges? | 1,916 | +2.7% (−1.7% to +7.1%), luck | **−1.2%** | **NOT PROVEN** ✔ |

**Lesson:** in the illusion case, the per-bet uncertainty filter alone did not stop the false edges, because two disagreeing books understate consensus error. Negative CLV and the confidence interval stopped them. CLV against an observed close is therefore a hard requirement before any strategy is validated, and a consensus needs several independent sharp books.

## 2. Real NFL data: Engine C vs the listed near-close line (`games.csv`), out of sample 2012–2025

Bets are **hypothetical**: the line's book and time are unknown, so these are not verified executable prices.

| Strategy | Bets | ROI (95% CI) | Mean CLV | Max drawdown | Gate |
|---|---|---|---|---|---|
| Independent model, conservative policy | 83 | −5.0% (−26.8% to +15.6%) | −1.3% | 11.4 u | NOT PROVEN |
| Anchored model, conservative policy | 20 | −20.0% (n too small) | −1.3% | 5.9 u | NOT PROVEN |
| Independent model, loose research policy | 6,451 | **−2.2%** (−4.7% to +0.2%) | −1.5% | 194 u | NOT PROVEN |
| Anchored model, loose research policy | 4,755 | −1.1% (−4.1% to +1.7%) | −1.4% | 84 u | NOT PROVEN |

- The independent model's largest "edges" (model EV ≥ 5%) lost 2.7% over 4,584 bets: big model-vs-market disagreements are mostly model error.
- One subgroup looks positive: the anchored model's EV ≥ 5% bets returned +2.8% over 1,331 bets. That is about 1 standard error from zero and was found **after** looking at the data. Treat it as a hypothesis to pre-register and test forward, not as a finding.
- 2026 to date: independent −15.9% (178 bets), anchored +4.2% (82 bets). Both samples are too small to mean anything.

## Conclusion

No strategy that can be evaluated with the data available today shows a validated edge. The pipeline behaves as required: it certifies a real edge, refuses to invent one, and refuses to certify an illusion.

The one strategy with a plausible mechanism, **S1 line shopping against a sharp consensus** (implemented in `edge_v4/forward.py`), needs **timestamped multi-book odds with observed closes** to be tested on real data. Neither games.csv nor anything reachable from this sandbox provides that. See the infrastructure note in DEVLOG.md.
