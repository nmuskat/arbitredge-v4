# NFL SIMS on the V5 website: Phase 2 status (development branches only; nothing deployed)

Date: 2026-10-30 (evening). Branches: V5 `sims-dev` (pushed; never deploys), SIMS `web-export` (committed in this session;
push to `nmuskat/arbitredge-sims` was not requested; the change is preserved as two patches in
`docs_v5/sims_handoff/`, with instructions for the SIMS session). No scheduled task, credential, workflow run or deployment was
created or changed. Live V5 (`v4-dev` `a230597`) and SIMS `master` are untouched.

## 1. What was built

SIMS (`arbsims web-export projections|edges|history`):
- opens the database **read-only** (SQLite `mode=ro`; the CLI does not even open its normal write connection);
- NFL only; whitelisted fields copied unchanged from what SIMS stored (official forecasts, the immutable edge report,
  grades, accuracy); a missing value stays null; seeds, model inputs and internal notes are never exported;
- one new file per export, `payload_sha256` over the body, never overwritten; optional `fixture` label for test data;
- runbook: the export is the **last step** of the existing Saturday, Sunday and Tuesday sections. The three existing
  scheduled tasks already say "follow docs/EDGE_SUNDAY_RUNBOOK.md section X", so they pick the step up **without new
  tasks and without editing the tasks**, once this branch is merged into SIMS `master` in the SIMS session. Their
  simulation, odds, publishing and grading steps are unchanged; a failed export changes nothing else.

V5:
- `sims_v5/schema.py`, `sims_v5/importer.py`: format, schema version, kind, NFL-only, hash, future timestamps,
  probabilities 0-1, possible American prices, Top 5 consistency, size limits; TEST FIXTURE files refused outside
  preview builds; the newest **valid** file of each kind wins; nothing is repaired; never raises.
- `web_v4/sims_section.py`: the page block (whitelisted pass-through; compact table encoding; distributions regrouped
  to at most 20 bars from SIMS's own counts, totals preserved). No sportsbook import (tested both ways).
- NFL SIMS tab (replaces the "NOT CONNECTED" placeholder): Top 5 cards (raw simulated probability, calibrated
  probability, break-even, raw / calibrated / conservative ROI each labelled as a model expectation, books' no-vig
  probability, simulated mean/median/10-90%), forward record with SIMS's sample-size warning, collapsible table of all
  analysed offers (default: passed every SIMS check) with exclusion reasons, separate game-forecast section (win
  probability, mean and median score, total and margin ranges, representative game) with player projections. College
  shows "NFL only". Odds older than 90 minutes show ODDS STALE and started games show STARTED, judged by the viewer's
  clock when the page is opened.
- Import inside the **existing** dashboard build (no new workflow): one step, `continue-on-error`, the read-only token
  in that step only, used only for `nmuskat/arbitredge-sims`; valid new files are copied to release `sims-import`
  (new files only). Without the token the build uses already imported files.
- `tools_v5/release_check.py` check 10, `sims_isolated`: a build that also reads a SIMS export is identical outside the
  SIMS section (only wall-clock fields of the build itself are normalized).

## 2. Test results (all run)

| Suite | Result |
|---|---|
| SIMS full suite (with nflverse data, odds key removed) | 144 passed, 4 warnings |
| SIMS export tests (`tests/test_export_web.py`) | 5 passed |
| SIMS repository safety scan | passed |
| V5 full suite | 536 passed, 22 skipped |
| V5 SIMS section tests (`tests_web/test_sims_section.py`) | 21 passed |
| V5 release checks on real data with the SIMS fixture | all 10 pass, `sims_isolated` differences: none |
| Browser (desktop 1280 px, phone 390 px) | no errors, no horizontal scroll; 5 picks, 652/1,332 offers, 4 official games + 11 listed without a forecast; 3.5 h later all 5 picks ODDS STALE; after kickoff all 5 STARTED |

The fixture: real SIMS 1.7.2 forecasts (5,000 simulations, 4 Week 9 games) with SYNTHETIC sportsbook prices fed through
SIMS's own fake-provider test path (no API call, no credits); every page built from it shows a TEST FIXTURE banner and
production imports refuse it.

## 3. GitHub Actions impact (measured 2026-10-31; no Actions job was run for benchmarking)

Real V5 dashboard builds on GitHub (job logs; billing rounds each job up to whole minutes):

| Build | Job total | Build-site step | Billed |
|---|---|---|---|
| 2026-10-30 20:00Z, first V5 build (opened the week mid-week, reconstructed Tue-Fri, full replay check) | 118 s | 82 s | 2 min |
| 2026-10-30 22:22Z, warm (sensitivity cache valid) | 61 s | 27 s | 2 min |

Non-build steps (checkout, data download, saves, publish, Cloudflare deploy) take about 34 s.

Local timing on the same live inputs (37 shards, 49 ledger files), with and without the SIMS section:

| Build type (how often) | Local, no SIMS | Local, with SIMS | Estimated GitHub job with SIMS* | Billed |
|---|---|---|---|---|
| Warm: cache valid (near-kickoff builds every 45 min) | 54.3 / 49.7 s | 49.4 / 49.5 s | ~63 s | 2 min |
| Sensitivity cache expired (most quiet-period builds) | 76.1 s | 77.4 s | ~78 s | 2 min |
| Cache expired + 12-hourly full replay check | 82.8 s | 85.0 s | ~82 s | 2 min |
| Weekly run opening (Tuesday; first build of the week) | not reproducible now | | well under the 118 s mid-week case | 2 min |

*34 s of non-build steps + the build step scaled by the measured GitHub/local ratio (27 s vs ~50 s warm) + the
import step. The SIMS section itself adds no measurable build time (differences are within run-to-run noise of
±5 s); page size grows ~0.2 MB (fixture) to ~0.8 MB (full slate).

Import step cost (each GitHub API call measured at 0.34-0.50 s from this session): two release listings plus the
newest imported file of each kind (at most 3 small downloads) = about 2 s per build; about 3-4 s in the few builds
(about three a week) right after SIMS publishes. SIMS is contacted only to list exports, and files are downloaded
only when new. Every network call is limited to 20 s and the step to 2 minutes, and it is continue-on-error.

Expected monthly effect: **0 additional billed minutes** (every build type stays under 120 s with 35-55 s to spare).
Worst case: a mid-week reconstruction like the deployment build (118 s) would cross 120 s and bill 3 minutes; that only
happens after a redeploy or rollback, so about +1 minute when it does. Monthly worst case stays about 1,853-1,858 of
2,000. SIMS side: one SIMS CI run per pushed patch (about 2 minutes each, 2 patches); release uploads run no CI.
No new workflow and no new scheduled task. Billing page: owner check required (section 4, step 1).

Failure safety (tested): missing SIMS release, expired or revoked token (HTTP 401), failed download, a hung call
(timed out), corrupt or invalid files, or no token at all: the step exits 0, nothing is uploaded, and the page keeps
the last valid export (aged by the viewer's clock) or shows "no export yet". The site build itself never imports SIMS
code paths that can raise (release check 10 confirms the rest of the site is byte-identical).

## 4. Manual setup required (at deployment time, owner only)

1. **Check real billing headroom**: github.com -> your profile picture -> Settings -> Billing and plans -> Usage ->
   Actions (this month's minutes across all private repositories). Do not change any budget or overage setting.
2. **Create the read-only token** (browser only; never paste it in chat): github.com -> Settings -> Developer settings
   -> Personal access tokens -> Fine-grained tokens -> Generate new token. Name `v5-sims-export-read`; expiration 1 year
   (calendar a renewal); Repository access: Only select repositories -> `nmuskat/arbitredge-sims`; Permissions ->
   Repository permissions -> Contents: Read-only (nothing else). Generate and copy it.
3. **Save it as a secret**: github.com/nmuskat/arbitredge-v4 -> Settings -> Secrets and variables -> Actions -> New
   repository secret -> Name `SIMS_EXPORT_READ_TOKEN` -> paste -> Add secret.
4. **SIMS**: in the SIMS session, follow `docs_v5/sims_handoff/APPLY_IN_SIMS_SESSION.md` (patch 0001 after Sunday's
   report; patch 0002 and the `web-export` release only at activation).
5. Approve the V5 merge (`sims-dev` -> `v4-dev`) including CLAUDE.md rule 2d.

## 5. Rollback

Remove the secret (imports stop; the tab keeps the last valid export and ages it out), or revert the merge commit.
Nothing in the sportsbook, Need, sharp vs public, ledger or settlements reads SIMS data (release check 10). The SIMS
runbook step can be removed without touching any other step.

## 6. Shortest safe sequence to finish (nothing here before the Sunday Nov 1 report is out)

1. SIMS session, after the Sunday Nov 1 report: apply and test patch 0001 (inert), push (Step A of the handoff).
2. Owner: check Actions usage on the billing page (no setting changes).
3. Owner: create the read-only token and save it as `SIMS_EXPORT_READ_TOKEN` (section 4, steps 2-3).
4. Owner: approve deploying `sims-dev` (rerun the full suite and the 10 release checks first; merge as a fast-forward
   onto `v4-dev`). The NFL SIMS tab shows "no export yet" until step 5.
5. Owner: "activate SIMS exports" -> SIMS session Step C (patch 0002, create the release, first exports). The next
   dashboard build imports them; verify the tab on the live site.
