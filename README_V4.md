# ArbitrEdge V4

V4 is built independently of V3 production. The V3 source in this repository (the baseline commit) is reference material only; nothing here deploys to or touches production.

## Engines

| Package | Role | Status |
|---|---|---|
| `sportsbook_v4/` | **Engine A**: synthetic sportsbook. 10,000 persistent bettors per sport; spreads, totals and moneylines; append-only wager ledger; exact exposure and **Need** for every market and the joint book. The book closes when quotes go stale. | Working. Behaviour parameters are unvalidated assumptions. |
| `sharp_v4/` | **Engine B**: sharp-money evidence from real price movement (first mover, steam, key numbers, buyback, market-making vs recreational books). 0–100 score; can return INSUFFICIENT EVIDENCE. | Working. **Experimental**: uncalibrated until real multi-book history exists. |
| `sims_v4/` | **Engine C**: NFL point-in-time features (Elo, EPA), margin and total distributions with key numbers, walk-forward validated. | Validated: **does not beat the closing market** (see docs). |
| `edge_v4/` | Decision layer: vig removal, EV with pushes, BET/WATCH/NO BET, fractional Kelly with caps, Odds API credit gate, strategy registry, paper ledger, backtests, controls, forward EV scanner. | Working. **No strategy validated.** |
| `market_v4/` | Odds API payload normaliser, append-only quote store, point-in-time market state, observed closes, data-quality monitors. | Working (fixtures and demo data). |
| `data_v4/` | nflverse downloads with SHA-256 manifest. | Working |
| `web_v4/` | Read-only website (8 sections, provenance badges, mobile). | Working |

## Run

```bash
python -m pip install -r requirements-v4.txt
python -m data_v4.fetch_nflverse --dest data/raw --seasons 2006-2026      # ~380 MB, no credentials
python - <<'PY'                                                           # derived features
import glob, pandas as pd
from sims_v4 import nfl_data as nd, features as ft
g = nd.load_games("data/raw/games.csv"); g = g[g.season >= 2006]
e = nd.team_game_epa(sorted(glob.glob("data/raw/play_by_play_*.parquet")), cache="data/derived/team_game_epa.parquet")
ft.build_features(g, e).to_parquet("data/derived/features.parquet")
PY
python -m sims_v4.evaluate                          # Engine C walk-forward report -> docs_v4/engine_c_eval.json
python tools_v4/run_controls.py                     # framework controls -> docs_v4/controls_synthetic.json
python -m web_v4.snapshot --as-of 2026-10-26T21:00:00Z
uvicorn web_v4.app:app --host 127.0.0.1 --port 8400
python -m pytest -q tests_v4 tests_edge tests_market tests_sharp tests_sims tests_web
```

## Documents (`docs_v4/`)

- `V4_REBUILD_PLAN.md`: architecture, data sources, methodology, evidence bar (§G), risks
- `ENGINE_C_REPORT.md`: Engine C validation against the market
- `BACKTEST_REPORT.md`: real-data backtests and framework controls
- `DATA_SOURCES.md`: provider audit and licences
- `DEVLOG.md`: development log, milestones and blockers
