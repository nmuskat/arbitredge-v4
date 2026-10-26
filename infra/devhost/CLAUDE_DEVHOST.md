# ArbitrEdge V4 — development server rules (root-owned; read first)

You are on the **dedicated V4 development droplet**. V3 production (143.244.160.20) is a different machine. You have no credentials for it, and you must not attempt to reach it.

## Hard rules

- **Production:** never access, modify or deploy to V3 production. Never restart its services. No production deployment of V4 without the owner's explicit approval.
- **Odds API access:** only through the local gateway (`edge_v4.odds_client.OddsClient`, socket `/run/arbitredge-v4-gateway/gateway.sock`). You do not have the key and must not try to obtain it.
  - The gateway enforces the V4 budget: 6,000 credits per month, 400 per 24 hours, a 2,000-credit floor protecting V3, and 200 per request. Treat its refusals as final.
  - Live calls are off until the owner enables them; build against recorded fixtures and the imported production copy.
  - Before any historical backfill, write its credit estimate to `docs_v4/` and stop for owner approval. The 200-per-request ceiling is a backstop; the approval step is still required.
- **Data:** never fabricate data. Missing odds, closes, splits or injuries are reported as unavailable, never imputed from the future or from closing lines.
- **Official plays:** none until the evidence criteria in `docs_v4/V4_REBUILD_PLAN.md` §G are met. Default decision: NO BET.
- **Testing:** never claim a test passed unless it ran. Report exact counts.

## Layout

| Path | Purpose | Your access |
|---|---|---|
| `/srv/arbitredge_v4/repo` | V4 git repository (branch `v4-dev`) | read/write |
| `/srv/arbitredge_v4/data`, `logs`, `backups` | working data, logs, nightly backups | read/write |
| `/srv/arbitredge_v4/import` | read-only copy of the production database | read only |
| `/srv/arbitredge_v4/venv` | Python environment | read/write |
| PostgreSQL `arbitredge_v4` (local socket, peer auth) | V4 operational database | owner role |
| `/etc/arbitredge-v4`, `/opt/arbitredge-v4-gateway`, `/var/lib/arbitredge-v4-gateway` | gateway key, config, code and ledger | none (by design) |

## Working style

- Commit often on `v4-dev`; the nightly backup bundles the repository.
- The staging website binds to `127.0.0.1` only. The owner views it through an SSH tunnel.
- Keep a development log in `docs_v4/DEVLOG.md`: milestones, blockers and measured results.
- Interrupt the owner only for spending, credentials, permissions, material scope changes, or deployment.
