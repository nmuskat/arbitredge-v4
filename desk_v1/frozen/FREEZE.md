# DESK-v1 freeze record

Frozen before ANY 2026 evaluation. spec sha256 `ad82a0af379ec592cd37dc50304f14a562fe9bd08308a6e880d204197d188c34`; params sha256 `7f0682a532416cf5d7e0d57d96f7452aac45a7f390d5608532670a09b69fa7cc`;
code = the commit that adds this file; input data checksums in `input_data_sha256.txt`.

Training market: 2025 CLOSING lines (Option B proxy, owner-approved 2026-11-11); NOT decision-time quotes.

| Sport / market | (lambda, rho) | alpha, beta | w_hat | w 95% CI | **frozen w** | sigma (= sigma0 x k) | games |
|---|---|---|---|---|---|---|---|
| cfb spread | (25, 0.5) | 26.93, 0.472 | -0.048 | [-0.173, +0.074] | **0.0** | 12.14 (15.18 x 0.8) | 646 |
| cfb total | (400, 0.5) | 26.38, 0.917 | +0.095 | [-0.096, +0.286] | **0.0** | 19.28 (15.42 x 1.25) | 670 |
| nfl spread | (400, 0.5) | 22.41, 0.669 | -0.295 | [-0.642, +0.012] | **0.0** | 10.53 (13.16 x 0.8) | 269 |
| nfl total | (400, 0.5) | 22.41, 0.669 | +0.221 | [-0.284, +0.704] | **0.0** | 16.56 (13.25 x 1.25) | 269 |

All four frozen w = 0: by the pre-specified rule DESK-v1 returns NO POSITION in every market; its forecast equals the market.
k reaches the edge of its grid in every market: with w = 0 the desk probability at the market line is ~0.5 whatever sigma, so k is not identified. Harmless for decisions (no leans possible); reported, not adjusted.
