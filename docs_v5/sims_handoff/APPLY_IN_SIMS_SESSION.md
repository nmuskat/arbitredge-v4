# Applying the SIMS web-export patches in the ArbitrEdge SIMS session

Two patches, made against SIMS `master` `63f931c` and verified to apply cleanly there and to reproduce the tested
branch exactly:

| File | What it does | When |
|---|---|---|
| `0001-Read-only-web-export-command-for-the-ArbitrEdge-V5-w.patch` (sha256 `246e87b8ec7235d7ffffd845ba2522ddfc180a60315fbf4a026d16db7411953d`) | Adds `arbsims/export_web.py`, the `arbsims web-export` command and `tests/test_export_web.py`. **Inert**: nothing calls it; no forecast, report, grade, ledger, schedule or credit use changes. | Step A, any quiet time **after** the Sunday Nov 1 report is published and its CSVs sent |
| `0002-Runbook-website-export-as-the-last-step-of-the-exist.patch` (sha256 `d4b7f511d6409e5fd82678bb6bbba5f3c81cc363c47a26678af3ef7bcd604512`) | Adds the export as the last step of the Saturday, Sunday and Tuesday runbook sections, which the existing scheduled tasks follow. This is what **activates** exports. | Step C only, when the owner says "activate SIMS exports" |

Quiet times: not during a scheduled SIMS task (Saturday ~18:30-19:30 CT prep, Sunday 9:00-10:30 CT publish, Tuesday
~7:30-8:30 CT grade, and the Week 8 one-off injury/inactive tasks on Saturday 10:00 CT and Sunday/Monday evenings).

## Step A: apply and test patch 0001 (paste this into the SIMS session, with the 0001 file attached)

```
Apply the attached patch 0001 (ArbitrEdge V5 website export, owner-approved 2026-10-31). Do not change any scheduled
task, model, forecast, report, grade, the ledger, or docs/EDGE_SUNDAY_RUNBOOK.md, and spend no odds credits.
1. In /home/user/arbitredge-sims: `git status` must be clean and the Sunday Nov 1 report must already be published;
   otherwise stop and tell me.
2. `sha256sum <patch>` must equal 246e87b8ec7235d7ffffd845ba2522ddfc180a60315fbf4a026d16db7411953d; otherwise stop.
3. `git am -3 <patch>`. If it does not apply cleanly: `git am --abort`, tell me which files conflict, and stop.
4. Tests: `env -u ODDS_API_KEY .venv/bin/python -m pytest -q` (report the exact counts) and
   `python3 scripts/check_repo_safety.py`. If anything fails: `git reset --hard HEAD~1` (only this patch's commit),
   report, and stop.
5. Read-only check on the real database, nothing uploaded:
   `env -u ODDS_API_KEY .venv/bin/python -m arbsims.cli web-export projections --week 9 --out $SCRATCH/web_export_check`
   and the same with `edges` and `history`. Report each file's name and size and the "exported" line. Do not upload
   them and do not create any release.
6. Push the commit to origin master as usual (this runs the SIMS CI once, about 2 minutes). Report the commit id.
```

## Step B: nothing in SIMS

The V5 side is completed and verified separately (see `docs_v5/SIMS_PHASE2.md`, section 6).

## Step C: activate (only when the owner says so; patch 0002 attached)

```
Activate the SIMS website export (owner-approved). Apply the attached patch 0002 the same way as 0001 (clean status,
sha256 d4b7f511d6409e5fd82678bb6bbba5f3c81cc363c47a26678af3ef7bcd604512, `git am -3`, full tests, safety scan, push).
Then create the export release once:
`gh release create web-export --prerelease --title "SIMS web exports (do not delete)" --notes "Read-only NFL exports for the V5 website"`,
run the three exports once to $SCRATCH/web_export (projections for the current week, edges, history) and upload them
with `gh release upload web-export <files>`. Do not change any scheduled task: from now on the existing Saturday,
Sunday and Tuesday tasks run the export as the last step of their runbook sections.
```

Rollback in SIMS: revert the 0002 commit (exports stop; nothing else changes). Revert 0001 as well to remove the
command entirely. Exports already uploaded stay as immutable records.
