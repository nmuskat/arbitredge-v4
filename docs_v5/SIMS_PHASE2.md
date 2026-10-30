# NFL SIMS on the V5 website: Phase 2 status (development branches only; nothing deployed)

Date: 2026-10-30 (evening). Branches: V5 `sims-dev` (pushed; never deploys), SIMS `web-export` (committed in this session;
push to `nmuskat/arbitredge-sims` was not permitted, so the change is preserved as
`docs_v5/sims_handoff/0001-arbsims-web-export.patch`). No scheduled task, credential, workflow run or deployment was
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

## 3. GitHub Actions impact (measured, with the open item stated)

- New recurring workflows: **none**. New scheduled tasks: **none**. SIMS exports are release uploads (no SIMS CI run).
- Per dashboard build: one release listing and, about three times a week, a download of new files (~50-300 KB);
  validation and the page block take 0.06 s; the page grows by about 0.2 MB (fixture, 4 games) to an estimated
  0.7-0.8 MB for a full 14-game slate. Expected cost: about 2-4 seconds per build.
- Billing is per job rounded up to the minute. The only V5 build measured so far (the first, cold, 4:00 pm ET) took
  **118 s**, just under the 120 s line. If builds that close cross 120 s they bill 3 minutes instead of 2: at the
  300-build cap that would be up to +300 minutes, which the 2,000-minute allowance cannot absorb on top of the 1,853
  worst case. **Before the deployment request, the next several warm V5 builds must be measured**; if they are within
  ~10 s of 120 s, the SIMS import moves to a separate job only when new files exist, or the build is trimmed first.
- Billing headroom: GitHub's billing page cannot be read from this session. My job-level count for this repository
  since Oct 26 is about 200 billed minutes, plus 2 for SIMS. The owner check below is required before enabling.

## 4. Manual setup required (at deployment time, owner only)

1. **Check real billing headroom**: github.com -> your profile picture -> Settings -> Billing and plans -> Usage ->
   Actions (this month's minutes across all private repositories). Do not change any budget or overage setting.
2. **Create the read-only token** (browser only; never paste it in chat): github.com -> Settings -> Developer settings
   -> Personal access tokens -> Fine-grained tokens -> Generate new token. Name `v5-sims-export-read`; expiration 1 year
   (calendar a renewal); Repository access: Only select repositories -> `nmuskat/arbitredge-sims`; Permissions ->
   Repository permissions -> Contents: Read-only (nothing else). Generate and copy it.
3. **Save it as a secret**: github.com/nmuskat/arbitredge-v4 -> Settings -> Secrets and variables -> Actions -> New
   repository secret -> Name `SIMS_EXPORT_READ_TOKEN` -> paste -> Add secret.
4. **SIMS**: allow the `web-export` change into SIMS `master` (push access for this session, or apply the patch in the
   SIMS session with `git am docs_v5/sims_handoff/0001-arbsims-web-export.patch`), and create the `web-export` release
   once (the runbook gives the command).
5. Approve the V5 merge (`sims-dev` -> `v4-dev`) including CLAUDE.md rule 2d.

## 5. Rollback

Remove the secret (imports stop; the tab keeps the last valid export and ages it out), or revert the merge commit.
Nothing in the sportsbook, Need, sharp vs public, ledger or settlements reads SIMS data (release check 10). The SIMS
runbook step can be removed without touching any other step.
