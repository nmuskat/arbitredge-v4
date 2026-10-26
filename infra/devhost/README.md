# ArbitrEdge V4 development server: setup package (for review)

Nothing in this package touches V3 production except three read-only steps:
- reading the one `THE_ODDS_API_KEY` line;
- streaming the newest existing backup file;
- an optional disk survey.

Nothing is installed on production. **No droplet is created until you approve the configuration below.**

---

## 1. Configuration and monthly cost (for your approval)

| Item | Choice | Monthly |
|---|---|---|
| Droplet | DigitalOcean **Basic, Regular SSD: 8 GiB RAM / 4 vCPU / 160 GiB SSD / 5 TB transfer**, Ubuntu 24.04 LTS | **$48.00** |
| Droplet backups | Weekly (20% of droplet price) | **$9.60** |
| PostgreSQL, Python, Claude Code, website staging | self-hosted on the droplet | $0 |
| **Total new V4 dev cost** | | **$57.60 / month** |
| *Option:* daily backups instead of weekly (30%) | | $62.40 total |
| *Separate decision (production disk fix, §6):* 100 GB volume on the **production** droplet | | about $10.00 |
| The Odds API | existing $30 plan, shared with V3 (V4 capped at 6,000 credits) | already paid |

DigitalOcean bills per second, with the monthly price as the cap. Prices are from DigitalOcean's droplet pricing table as of late October 2026; confirm on the order screen.

**Storage sizing for 160 GiB** (estimate; nothing measured yet):
- OS and tools: about 6 GB
- Production database copies (two): about 18 GB
- PostgreSQL V4 database: 15–25 GB
- Parquet research store: about 5 GB
- Seven nightly local dumps: 20–35 GB
- Logs: about 2 GB

That totals roughly 70–90 GB, leaving more than 70 GB headroom. A volume can be added later without downtime.

**Droplet settings when you approve:**
1. Region: the same region as production (fast copy).
2. **New VPC** named `arbitredge-v4-dev`. Do not use the region's default VPC, so the dev droplet has no private-network path to production.
3. **SSH key** authentication (not password).
4. Hostname `arbitredge-v4-dev`.
5. A separate DigitalOcean **project** `ArbitrEdge V4 Dev`.
6. Weekly backups on.

---

## 2. Install (owner time: about 15 minutes, once)

```bash
# On your computer: upload the package to the NEW droplet (not production)
scp arbitredge_v4_devhost.tar.gz root@<DEV_IP>:/root/
ssh -A root@<DEV_IP>                         # -A: your SSH key stays on your computer

# On the dev droplet
cd /root && tar -xzf arbitredge_v4_devhost.tar.gz && cd arbitredge_v4_devhost
bash devhost_bootstrap.sh --check            # preflight; changes nothing
bash devhost_bootstrap.sh                    # shows the plan, asks 'yes', installs, verifies
bash transfer/send_odds_key.sh               # key: production -> gateway (never displayed)
bash transfer/copy_prod_db.sh                # newest production BACKUP -> dev (read-only on prod)
sudo -iu arbitredge-dev
~/.local/bin/claude                          # /login in your browser, then /exit
tmux new -s v4 -c /srv/arbitredge_v4/repo "~/.local/bin/claude remote-control --name 'ArbitrEdge V4 dev'"
# answer y / y, then detach: Ctrl-b d
```

Then open **"ArbitrEdge V4 dev"** in the Claude app. Live Odds API calls stay **off** until you run `arbitredge-v4-gateway-admin enable-live`. Optionally run `arbitredge-v4-gateway-admin test-key` first: one `/v4/sports` call, which the provider documents as costing 0 credits.

---

## 3. Security model

| Boundary | How it is enforced | Verified by |
|---|---|---|
| **Production** stays untouched | No V4 software on production. The bootstrap refuses to run on the production IP, on a host with `/opt/arbitredge`, or on a host with V3 services. Production reads are limited to: one `grep` for the key line, streaming one completed backup file at idle priority, and the optional survey. | `test_transfer.sh` (production files byte-identical, mtimes unchanged, nothing created) |
| Dev droplet has **no standing access to production** | No SSH keys on the droplet. Your key is used only through agent forwarding (`ssh -A`) during your own session. A separate VPC means no private-network path. The Claude policy denies any command containing the production IP. | `verify_devhost.sh` |
| Claude / V4 code **never sees the Odds API key** | Key file is `root:root 0600`. systemd hands a private copy to the gateway service only, which runs as `arbitredge-gw`. The dev user is in the socket group only. Key values are scrubbed from every response and log, including when the provider echoes them back. | gateway tests; sandbox end-to-end test (dev denied key file, credential copy, process environment, ledger) |
| **V3 credits protected** | The gateway (root-owned code, not editable by Claude) refuses a request when any of these would be exceeded: **6,000/month V4 cap**, 400 per 24 h, 200 per request, or a **2,000-credit floor** on the provider's shared remaining balance. Usage is logged to an append-only ledger. The circuit breaker stops all calls on an invalid key or exhausted credits until you reset it. **Live calls are off by default.** | gateway tests (cap, floor, ceiling, breaker, cooldown, cache, validation) |
| Dev user has **no privilege** | No sudo, password locked, not in admin groups. Gateway config, ledger, code, the Claude policy and `CLAUDE.md` are all root-owned. | `verify_devhost.sh` |
| **Network exposure** | UFW allows only 22/tcp inbound. SSH is key-only. fail2ban is on. PostgreSQL listens on localhost only. The website binds to 127.0.0.1 and you view it through an SSH tunnel. | `verify_devhost.sh` |
| **Production data copy** | Source is an existing backup file, never the live database. SHA-256 is checked end to end. Integrity is checked, and the copy is scanned for Odds API keys (the current one and any `apiKey=` value in logged URLs). Keys are redacted with same-length replacement, then integrity is re-checked. Private-key material or corruption triggers quarantine (root-only). The copy is read-only to the dev user. | `test_transfer.sh` |
| **Claude Code permissions** | Root-owned managed policy: bypass mode disabled. Denied: `sudo`/`su`, key paths, direct Odds API URLs, the production IP, editing `/etc` `/opt` `/usr`. Asks first: `rm -r`, `git push`, `curl`/`wget`/`ssh`/`scp`, dropping databases. Allowed: routine development commands, so you are not asked for approval on every test run. **Linux permissions are the real boundary, not this policy.** | `verify_devhost.sh` |

**Accepted residual risk:** Claude can make outbound network requests from the droplet through Python. It holds no production credentials and no API key, so the exposure is the V4 code and the redacted database copy.

---

## 4. Test results (run in the build sandbox)

| Suite | Result |
|---|---|
| `tests/test_gateway.py`: credit gate, key handling, validation, breaker, end-to-end socket | **31 passed** |
| `tests/test_transfer.sh`: key transfer and backup copy against a simulated production | **30 passed** |
| `tests/test_disk_survey.sh`: survey is read-only and redacts | **7 passed** |
| `devhost_bootstrap.sh` + `verify_devhost.sh` in container mode, with real Linux users and permissions | **22 passed** |
| V4 repository: sportsbook + decision core + market layer | **129 passed** |
| Mutation checks: deliberately broken copies of the gateway, decision core and market layer | **18 of 18 caught** |

An end-to-end sandbox run used real Linux users. The dev user made a budgeted request through the gateway. It was refused access to the key file, the service's credential copy, the gateway's process environment and the ledger (both reading and tampering). The key appeared in no log. Full output is in `TEST_RESULTS.txt`.

**Not testable in the sandbox; first exercised on the real droplet:** systemd unit start and hardening, UFW, the sshd reload, PostgreSQL setup, the Claude Code installer, and real SSH to production. Each of these is either checked by `verify_devhost.sh` after installation or guarded by a refusal before any change.

---

## 5. Files

| File | Purpose |
|---|---|
| `devhost_bootstrap.sh` | One-time setup of the dev droplet (`--check` = preflight only) |
| `verify_devhost.sh` | Read-only security verification; re-run any time |
| `gateway/` | Odds API gateway, its systemd unit, default config (live off) and root-only admin tool |
| `transfer/send_odds_key.sh` | Production key line → dev gateway, never displayed |
| `transfer/copy_prod_db.sh` | Newest production backup → dev, verified and redacted |
| `claude-managed-settings.json`, `CLAUDE_DEVHOST.md` | Claude Code policy and on-server rules |
| `backup/` | Nightly dev backup timer (PostgreSQL dump + repo bundle, keep 7) |
| `prod_readonly/prod_disk_survey.sh` | Read-only production disk survey |
| `PROD_DISK_PLAN.md` | Plan to fix production disk without deleting backups or stopping the collector |
| `arbitredge_v4.bundle` | V4 code repository (cloned onto the droplet) |

---

## 6. Production disk

See `PROD_DISK_PLAN.md`. The first step is a read-only survey you can run today:

```bash
ssh root@143.244.160.20 'bash -s' < prod_readonly/prod_disk_survey.sh > survey.txt
```
