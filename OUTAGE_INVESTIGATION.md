# ArbitrEdge — Odds API Outage Investigation (2026-10-25)

**Scope:** investigation and isolated code preparation only. Nothing was done to production: no changes, no restarts, no environment or credential changes, no Odds API requests, no credits spent. This work is kept separate from the V4 rebuild.

- **Workspace:** `/home/user/arbitredge_odds_outage`, a git repository.
  - Commit `77af480` is the unmodified archive.
  - Commit `a74a653` is this patch.
  - The full diff is in `odds_outage_patch.diff`.

**Evidence labels:**

| Label | Meaning |
|---|---|
| **[F]** | Fact you supplied |
| **[C]** | Confirmed from source code (file:line) |
| **[D]** | From the provider's public documentation (via search snippets) |
| **[H]** | Hypothesis |

---

## 1. Confirmed facts

| # | Fact | Basis |
|---|---|---|
| 1 | Last success 2026‑10‑25 00:55 UTC; first 401 at 01:15 UTC; all three sport keys fail | [F] |
| 2 | The environment file was last modified 2026‑10‑05; the service references it | [F] |
| 3 | The key is sent only as the `apiKey` query parameter. Logged and stored URLs are built without it (`collectors/odds_collector.py:262-271`) | [C] |
| 4 | Any status ≥ 400 outside the retryable set raises `NonRetryableRequestError("Non-retryable HTTP <status> from <url>")`. **The response body and every header are discarded**, including `x-requests-remaining`/`x-requests-used` (`collectors/base_collector.py:235-238`). Usage headers are only logged on success (`:240-242`). | [C] |
| 5 | `raw_responses` is written only after a successful request (`odds_collector.py:278`), so failed responses leave no stored evidence | [C] |
| 6 | `CollectionRun` suppresses the exception and records `error_type` and a redacted `error_detail` (≤ 2,000 chars) in `collection_log` (`base_collector.py:115-126`) | [C] |
| 7 | **After a failure, the scheduler retries every run indefinitely.** "Last successful poll" never advances (`operations/run_collection.py` `get_last_successful_odds_poll_for_sport`), so every sport is "due" on every scheduler invocation. `--force` makes it poll regardless. | [C] |
| 8 | **The system was designed around a 500-credit/month plan** (`operations/audit_september_credit_budget.py:34`, `operations/collection_health.py:245`). The repo's own simulation projects **109 credits/week, 469/month, a 31-credit margin**, modelling NFL + CFB only. | [C], re-run offline |
| 9 | **The scheduler also polls `nfl_preseason` every run** (`ODDS_SPORTS`). With no preseason game tracked, that sport is always in the once-daily discovery tier (`cadence_config.py:34,55-56`): about one extra request a day that the budget audit does not count. | [C] |
| 10 | The Odds API defines error codes **`INVALID_KEY`** (invalid, or not tied to a subscription), **`OUT_OF_USAGE_CREDITS`** (the monthly credit limit is reached), and **`DEACTIVATED_KEY`** (most commonly after a cancelled subscription). Remaining credits are reported in `x-requests-remaining`. **Credits reset on the 1st of each month.** | [D] |
| 11 | **Not established:** which HTTP status each of those codes uses. The docs page could not be fetched from this sandbox (DNS blocked), and the search snippets do not state the mapping. | — |
| 12 | `operations/run_collection.py:log_key_fingerprint` prints the key's length and a 12-char SHA-256 fingerprint on **every** run. The production logs therefore already show whether the key value seen by the service changed across 2026‑10‑25 01:15. | [C] |

---

## 2. Root-cause hypotheses (ranked)

**I am not claiming the key is invalid.** The 401 body, which would name the cause, was discarded by the current code.

| Rank | Hypothesis | Confidence | For | Against / open | Decisive read-only check |
|---|---|---|---|---|---|
| **1** | **Monthly usage credits exhausted (`OUT_OF_USAGE_CREDITS`)** | **Moderate** (best fit, not proven) | All three sports fail together (account level). It failed on the **very next 20-minute "highest"-tier poll**, during a Saturday-night CFB slate, the plan's heaviest polling window. The design margin was only 31 credits/month. There is unbudgeted daily `nfl_preseason` polling, plus any `--force` runs (3 credits each) and `run_odds_discovery.py` runs (real requests). The environment file did not change. | The repo's own model gives **~374 credits by 25 Oct** for September-like weeks, below 500, so exhaustion needs ~25% more usage than modelled. Whether this code arrives as HTTP 401 is unconfirmed (§1 #11). The plan size may have changed on 5 Oct, the date the environment file was edited. | (a) The last logged `x-requests-remaining` before 00:55 UTC (Appendix A, Q1). (b) Credits counted from `raw_responses` for October (Q2). (c) The provider dashboard's usage page. |
| **2** | **Key deactivated** (subscription cancelled or payment failure) | Low–moderate | Account level; sudden; no local change | No evidence of a billing event yet | Provider dashboard and billing emails (owner) |
| **3** | **Key invalid or regenerated on the provider side** | Low | Account level | The environment file is unchanged since 5 Oct; nothing locally explains a mid-session change | Fingerprint lines before and after 01:15 (Q4); dashboard key status |
| **4** | **The service process is reading a different key from the file** (environment drift) | Low | — | You confirmed the service references the expected file | Fingerprint lines (Q4) vs `validate_odds_credential.py` dry run |
| **5** | **A code or request change on 24–25 Oct** | Low | — | You confirmed the parameters are correct | Deploy and git history around 01:00 UTC |
| **6** | **A provider-side incident** | Very low | — | — | Provider status page or support |

**If #1 is confirmed:** no credential change is needed. Usage resets on **1 Nov 00:00 UTC** per the provider. Before then, the options are buying credits or upgrading the plan (an owner decision). Separately, the polling budget needs revisiting before November.

---

## 3. Audit of the existing error handling

1. **Provider errors are discarded** (fact #4). The fix must keep `error_code`, the message, and the credit headers, which are exactly what tell hypotheses 1–3 apart.
2. **Redaction relies on `collectors/config.redact()`.** That file is not in the archive. Its test (`tests/test_base_collector.py:348-353`) shows it pattern-matches `apiKey=`. Nothing redacts the **key value itself** if a provider echoed it in another form (bare, JSON field, percent-encoded).
3. **Network-error text can contain the full request URL including `apiKey`.** It passes through `redact()` only (`base_collector.py:209-212`).
4. The legacy duplicate orchestrator `collectors/run_collection.py` has the same scheduling behaviour and no production guard. It is not patched here; confirm whether anything schedules it.

---

## 4. The isolated patch (not deployed)

| File | Change |
|---|---|
| `collectors/provider_errors.py` (**new**) | `extract_provider_error()` keeps only: an allowlist of response headers (`x-requests-remaining/used/last`, `retry-after`, `content-type`, `date`); an `error_code` matching `^[A-Z][A-Z0-9_]{1,63}$`; a `message` that is scrubbed, stripped of control characters, and truncated to 300 chars; a `details_url` only if it is https, on a the-odds-api.com host, with no query, fragment or credentials; and the body's length and SHA-256. It **never** reads `response.url`, request headers, cookies, or stores the raw body. Classification is driven by the provider code, not the status: `QUOTA_EXHAUSTED`, `AUTH_INVALID_KEY`, `AUTH_DEACTIVATED_KEY`, `AUTH_UNKNOWN` (401/403 with no code), `RATE_LIMITED`, `CLIENT_ERROR`, `SERVER_ERROR`. `scrub()` removes each known secret (as literal or %XX-encoded characters, any case), `key=`/`apiKey=`/JSON credential fields, `Authorization`/`x-api-key`/Bearer tokens, and **any opaque token of 24+ characters**. It never raises. |
| `collectors/errors.py` | `NonRetryableRequestError` and `RetriesExhaustedError` accept an optional, already-sanitized `diagnostics` dict. The class names are unchanged, so existing `error_type` values stay the same. |
| `collectors/base_collector.py` | On non-retryable ≥ 400 and on retries-exhausted, it builds diagnostics and appends a one-line summary to the existing message prefix (`Non-retryable HTTP 401 from <url>; HTTP 401 classification=… error_code=… x-requests-remaining=… message="…"`). That line reaches `collection_log.error_detail` with **no schema change**. Adds hooks `secret_values()`/`diagnose_error_response()`, and scrubs every error string by secret value as well as through `redact()`. **Failed responses are still not written to `raw_responses`**, because the health report counts those rows as credits used. |
| `collectors/odds_collector.py` | `secret_values()` returns the API key, enabling value-based scrubbing |
| `operations/odds_circuit_breaker.py` (**new**) | Breaker state is derived from the existing `collection_log` rows (no new table); it also understands pre-patch "Non-retryable HTTP 401" rows. Policy: after **3 consecutive auth-class failures** it opens. **QUOTA_EXHAUSTED** stays suppressed until the documented reset (1st of the month, 00:00 UTC), then probes. **Other auth failures** allow one probe request every **6 h** across all sports. Any success closes it. **Operator reset:** touch `operations/state/odds_breaker_reset`. `--force` does **not** bypass it. Every suppression prints its reason, the failure count, and the next attempt time. `--status --db <path>` is a read-only status view. |
| `operations/run_collection.py` | Checks the breaker before building an `OddsCollector`. A suppressed check is recorded as a non-triggered cadence decision (`collector_run_result = NULL`, the value used today for "not due"), which avoids any risk from an unknown CHECK constraint. |
| `features/market_freshness.py` (**new**) | Per-game freshness against the collector's own cadence. **STALE** means older than 2 × the tier interval + 15 min; **NO_DATA** means never observed. Also reports per-sport newest observation, last successful poll, and failed runs since. |
| `web/app.py`, `web/static/app.js`, `web/static/style.css` | The API adds `market_freshness` and `market_data_stale` per game, plus `market_freshness` and `stale_market_games` at the top level. The UI shows a "MARKET DATA NOT CURRENT" banner, a per-card data age, a dashed outline on stale cards, and a "STALE MARKET DATA" label on the synthetic ledger. **No calculation changes.** |
| `operations/validate_odds_credential.py` (**new**) | The owner-gated validator (§7) |
| `tests/test_odds_outage.py` (**new**) | 33 offline tests (§8) |

---

## 5. Should the scheduler suppress requests? Yes, and with recovery built in

**Current behaviour** (fact #7): every scheduler invocation sends one request per sport (3 per run) indefinitely. This produces log noise and repeated failed authentication attempts against the provider. Whether a 401 costs credits is not documented in what I could retrieve, so this patch doesn't assume either way.

**The breaker in §4** limits that to **zero requests** until the documented reset (for quota exhaustion), or **one probe per 6 h** (other auth failures). Observability is kept:
- every suppressed check is printed with its reason;
- the read-only `--status` view;
- the health report already counts consecutive failures;
- the dashboard banner (§6).

**Trade-off:** with a 6-hour probe pacing, a fixed key could be picked up up to 6 h late. The operator reset marker removes that delay.

---

## 6. Stale-data inventory: what silently treats old market data as current

| Consumer | Behaviour during the outage | Evidence | Safeguard |
|---|---|---|---|
| **V3 synthetic ledger (live on the dashboard)**: `simulation/offered_line.construct_offered_line` | Uses each book's latest quote ≤ now with **no staleness check**. The ledger and "Modeled Need" keep being priced at the pre-outage lines, with `as_of = now`. | `simulation/offered_line.py:416-484` | **Patched:** the card is marked STALE and the ledger labelled. **Proposed (needs approval; changes V3 output):** drop books older than the freshness limit, or withhold the ledger when the game is STALE. |
| **Legacy inference regime** | A book is marked INSUFFICIENT when its **end-of-segment** quote is stale (the start quote is not checked). As quotes age, games fall to INSUFFICIENT_EVIDENCE. This degrades correctly but silently. | `book_need/inference.py:80-93` | The dashboard now states why |
| **Decision gate** `_promote_v3_live` | With legacy INSUFFICIENT, the V3 Need is shown but the decision is PASS / `NO_CLEAR_NEED`. **Stale data cannot produce a BOOK_NEED through this path**, but the card text says "Adequate data exists…" (an existing defect, audit C‑4). | `book_need/run_slate_book_need.py:39-78`; `web/app.py:95-96` | Stale marker; fix C‑4 later |
| **Public pressure: NFL consensus spread** | Median of each book's latest spread ≤ cutoff, **no staleness check** | `book_need/public_pressure.py:316-351` | **Proposed:** require freshness at the cutoff, else `None` |
| **Handle intensity v2 (NFL, CFB)** | Counts market activity in a window. **An outage reads as a quiet market** and distorts percentiles. | `behavioral/handle_intensity_v2.py:110-151`; `handle_intensity_cfb_v2.py` | **Proposed:** mark HI "unavailable" when the collection gap overlaps the window |
| **Signal line** | Drops stale books when `commence_time` is passed | `book_need/signal_line.py:50-55` | OK |
| **Experimental mixed signal (T‑15 min)** | Evaluated with stale books, so it usually does not qualify, and **the existing row is then DELETEd** (audit C‑5). An outage can erase a pre-outage signal. | `operations/run_collection.py:262-409` | **Proposed:** skip evaluation (no delete) when the game's data is STALE; append-only rewrite as already recommended |
| **Slate eligibility** | Games first seen after the outage have no snapshots, so they are `UNKNOWN_INSUFFICIENT_EVIDENCE` and excluded. Previously seen games stay ELIGIBLE with stale data. | `features/run_slate_eligibility.py` | The excluded list already shows the reason. Per-game freshness now distinguishes the cases. |
| **Dashboard "Data as of"** | Shows the cache computation time, not the market observation time | `web/app.py:203-205, 337-345` | **Patched:** per-game market data age and a global banner |
| **V4 (isolated dev work)** | `advance()` refuses to pass `data_complete_through` | `sportsbook_v4/engine.py` | Already safe; set `data_complete_through` = last successful poll |

---

## 7. Controlled credential-validation procedure (owner approval required)

**Gate 0: zero requests** (anyone with read access). Run Appendix A, Q1–Q5. If Q1/Q2 show credits at or near zero, hypothesis #1 is effectively confirmed and **no live request is needed**. Then go to the recovery checklist.

**Gate 1: owner reviews the provider account** (no API credit). Log in to the-odds-api.com and check:
- plan, usage this month, key status;
- any billing or deactivation email.

Also re-check on the current docs that `GET /v4/sports` does **not** count against usage credits. I believe the docs say so, but couldn't fetch them to confirm.

**Gate 2: dry run** (no request), as the service user with the production environment loaded:
```
python3 operations/validate_odds_credential.py
```
This prints presence, length, the 12-char fingerprint, the exact request it *would* make, and a same-day approval token. Compare the fingerprint with the production log lines (Q4).

**Gate 3: one live request, with explicit written owner approval naming the date and time.** Run interactively:
```
python3 operations/validate_odds_credential.py --live --approval-token <token>
```
It refuses under these conditions:
- the token is not today's;
- stdin is not a terminal (so cron/systemd can never run it);
- the phrase `I APPROVE ONE ODDS API REQUEST` is not typed.

It then sends exactly one `GET /v4/sports`, with no retries, no redirects, a 10 s timeout, and no database writes. Output is the status plus sanitized diagnostics, or `KEY_ACCEPTED` with the sport count and credit headers. A key that returns `KEY_ACCEPTED` on /sports while odds requests fail would point strongly to credit exhaustion. This inference depends on /sports being quota-exempt; confirm it at Gate 1.

---

## 8. Tests (actually run)

Run in an isolated venv with Python 3.13 and pytest:
```
python -m pytest tests/test_odds_outage.py  ->  33 passed
```
`collectors/config.py` is missing from the archive. When it cannot be imported, the tests install a clearly labelled **test-only** stand-in exposing the names `base_collector` imports. Production would use the real module.

**What the tests cover:**

| Area | Coverage |
|---|---|
| **Redaction** | Raw, URL, JSON, Authorization, Bearer, x-api-key, `token=`, short secrets, percent-encoded and re-cased echoes, and unknown long tokens. Non-secret text is preserved. |
| **Classification** | The three documented codes. 401/403 with no code → `AUTH_UNKNOWN` (**no code is ever invented from the status**). 429, 5xx, 422. |
| **Hostile responses** | A body echoing the key in the message, code, details_url and headers leaks nothing. Malformed codes and URLs are dropped. Control characters are stripped. Messages are truncated. Header allowlist enforced. Extraction never raises. |
| **Collector path** | A 401 is sent exactly once (no retry). `collection_log` gains classification, error_code and credit headers. `error_type` is unchanged. Nothing is written to `raw_responses`. **The key appears nowhere in any database table or log.** Exceptions carry sanitized diagnostics. Retries-exhausted keeps the last diagnostics. Connection-error text containing the key is scrubbed. The success path is unchanged. |
| **Breaker and recovery** | Below threshold stays closed. It opens and paces probes. Quota suppression lasts until 1 Nov 00:00 UTC. Pre-patch rows are understood. Non-auth failures do not trip it. A success closes it. Skips are ignored. The operator marker works. The scheduler checks the breaker before building a collector. |
| **Freshness** | Cadence-relative STALE/FRESH/NO_DATA, future observations ignored, the outage summary, and the **real Flask `/api/slate`** returning stale flags (slate engine stubbed). |
| **Validator** | Dry run makes no network call and leaks nothing. Each gate refuses independently. When approved, it sends exactly one GET to /v4/sports with `allow_redirects=False` and leaks nothing. |

**Mutation checks:** 7 injected defects, each caught by the intended test:
- the old message without diagnostics;
- value-based scrubbing removed;
- every header kept;
- the breaker never opening;
- freshness never going stale;
- the validator ignoring the TTY check;
- a status inventing a provider code.

**A defect the tests found in my own patch (fixed):** a secret echoed with an alternative percent-encoding (`%2D` for `-`) slipped past value-based redaction. Matching is now literal-or-%XX per character, case-insensitive.

**Not run:** the existing `tests/test_base_collector.py` and `tests/test_odds_collector.py`. They import the missing `db/` package, both before and after the patch. **Run them on a non-production checkout that has `db/` and `collectors/config.py` before deployment.**

---

## 9. Production recovery checklist

1. **[No approval needed; read-only]** Run Appendix A, Q1–Q5 and record the results.
2. **[Owner]** Check the provider dashboard and billing (Gate 1).
3. **Decide the cause:**
   - **Credits exhausted** → choose: wait for the 1 Nov 00:00 UTC reset, **or** buy credits / upgrade the plan [owner approval, spend]. Do not change the key.
   - **Deactivated** → reactivate or resubscribe [owner]. A new key may be required.
   - **Invalid key** → issue a new key in the dashboard [owner]. Update the environment file [approval A3].
   - **Still unknown** → validation Gate 3 [approval A2].
4. **[Approval A4]** Deploy the patch in this order, after running the existing collector test suites on a non-production checkout:
   1. diagnostics (`provider_errors`, `errors`, `base_collector`, `odds_collector`);
   2. the breaker (`odds_circuit_breaker` + `run_collection`);
   3. dashboard freshness (`market_freshness` + `web/*`).

   Each step is independently revertible with `git revert`. The patch has no schema changes.
5. **[Approval A5]** Restart the collection service or web service only as each step requires.
6. **Recovery verification:**
   - first successful poll appears in `collection_log` (`success=1`);
   - `x-requests-remaining` appears in the "API usage info" log line;
   - `market_snapshots` rows arrive for all three sports;
   - the dashboard banner clears;
   - the breaker reports CLOSED.
7. **Post-incident:**
   - re-run the credit budget with October's real kickoff density, plus daily `nfl_preseason` discovery polls and manual or forced runs;
   - consider removing `nfl_preseason` from `ODDS_SPORTS` outside August;
   - add a low-credit alert (`x-requests-remaining` below a threshold).

---

## 10. Approval gates (nothing below has been done)

| Gate | Action | Who |
|---|---|---|
| A1 | Read-only queries and log greps (Appendix A) | You (no approval beyond your own) |
| A2 | **One** live `/v4/sports` validation request | Owner, explicit, dated |
| A3 | Any change to the environment file or key (rotation, new key) | Owner |
| A4 | Deploying any part of this patch | Owner |
| A5 | Restarting gunicorn or the collection service | Owner |
| A6 | Buying credits or changing the plan | Owner |
| A7 | The proposed (not implemented) staleness gates that change V3 or legacy outputs (§6) | Owner |

---

## Appendix A — read-only evidence queries (no API calls)

Run against `/opt/arbitredge/db/arbitredge.db` with `sqlite3 -readonly`, and against the service logs.

```sql
-- Q2: credits used this month, as recorded by the health report's own proxy
-- (1 successful odds request = 1 raw_responses row; cost = markets x regions = 1 for spreads/us).
SELECT substr(retrieved_at,1,10) AS day, COUNT(*) FROM raw_responses
WHERE collector_name='odds_collector' AND retrieved_at >= '2026-10-01' GROUP BY day ORDER BY day;
SELECT COUNT(*) FROM raw_responses WHERE collector_name='odds_collector' AND retrieved_at >= '2026-10-01';

-- Q3: what each sport polled in October (shows nfl_preseason discovery polls and tiers)
SELECT sport, tier_label, COUNT(*) FROM cadence_decisions
WHERE checked_at >= '2026-10-01' AND poll_triggered = 1 GROUP BY sport, tier_label;

-- Q5: the failure sequence
SELECT executed_at, success, error_type, substr(error_detail,1,200) FROM collection_log
WHERE collector_name='odds_collector' AND executed_at >= '2026-10-24T20:00' ORDER BY executed_at;
```

```bash
# Q1: last credit headers seen before the outage (logged on every success)
grep -h "API usage info" <collection log files> | tail -n 20
grep -h "\[odds/" <collection stdout log> | grep -o "x-requests-remaining[^,}]*" | tail -n 20

# Q4: key fingerprint seen by the service, before vs after 2026-10-25 01:15 UTC
grep -h "THE_ODDS_API_KEY: present" <collection stdout log> | tail -n 40
```
