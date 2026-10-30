# NFL SIMS / Player Props on the V5 website: audit and integration plan (Phase 1, plan only)

Status: **proposal for owner review. Nothing implemented, scheduled or deployed.** Written 2026-10-30 from a read-only
clone of `nmuskat/arbitredge-sims` (master `63f931c`) and the live V5 repository (`v4-dev` `a230597`).

## 1. What SIMS has today (audit)

| Question | Finding |
|---|---|
| Where SIMS runs | No server and no scheduled GitHub workflow. The SQLite database (WAL, append-only triggers) lives in a separate Claude cloud session; work runs as command-line steps and **scheduled tasks in that session** (`docs/EDGE_SUNDAY_RUNBOOK.md`). Container loss = database loss until restored from the backup zip (`scripts/make_release.py`). |
| Production model | 1.7.2 (fingerprint `350c0c2b6858d738`); 1.8.0 is shadow-only. 10,000 simulations per game, replayable seeds. |
| Player projections | Per official forecast, per player: `play_prob`, and for each stat (from simulations where he played) mean, median, SD, p10/p25/p75/p90, min, max, plus a full value -> count tally; `anytime_td`; usage. Game: win probability (with Monte Carlo SE), score/margin/total distributions, modal and top-10 exact scores, **representative game** (a real simulated game ending on the modal score) with its exact box score. |
| Sunday Top 5 | `arbsims edge stage --stage publish` (Sunday ~9:15 CT) stores an **immutable, versioned report** (`edge_reports` + rows, `rows_sha256`): every offer for 7 allowlisted markets x 4 books, with raw simulated hit probability, break-even, raw ROI, **calibrated** probability and ROI (2025 calibration study, shrunk), conservative ROI, market no-vig probability, quality flags, odds and forecast timestamps, model version; Top 5 chosen by fixed rules (may be fewer or none). Exported today as two CSVs and a private claude.ai page. |
| Grading / accuracy | Tuesday `--stage grade`: every stored offer graded on official stats, CLV where possible, running history (`/api/edges-history`). Forecast accuracy, calibration and backtests in the app (`/api/accuracy`, `docs/ACCURACY_REPORT.md`). Forecast ledger: hash-chained `ledger/forecast_ledger.jsonl` (171 entries, committed). |
| Existing machine-readable export | JSON API (`/api/forecasts/{id}`, `/api/edges/latest`, `/api/edges-history`, `/api/accuracy`) **only while the app runs in that session**; CSVs for the Top 5. **No versioned file export a second system can read.** |
| Odds | SIMS uses **its own** The Odds API key on the free plan (~500 credits/month, about 84 per Sunday). V5 uses the separate 20,000-credit subscription. They never share a key. |
| SIMS GitHub Actions | `tests.yml` runs on every push to any branch (safety scan + unit tests): ~2 billed minutes per push, shared with the V5 repository's 2,000 free minutes. One push so far (2 minutes). |
| Sensitive files | `.gitignore` and `scripts/check_repo_safety.py` keep the database, raw data, archives and credentials out of git. |

## 2. Proposed architecture: one-way, read-only, versioned file hand-off

```
SIMS session (unchanged model, DB, Sunday tasks)
  └─ NEW read-only command  arbsims export web   (opens the DB read-only, NFL only, whitelisted fields)
       └─ uploads  sims_nfl_<kind>_<UTC>.json.gz + .sha256  to release `web-export` in arbitredge-sims
                                   (release assets: no git push, so SIMS CI is not triggered)
V5 repository
  └─ NEW workflow  sims-import.yml  (only place the new read token exists)
       downloads the newest export -> validates (hash, schema, NFL only, ranges, timestamps) ->
       copies the VALID file unchanged into release `sims-import` here (new files only, never overwritten)
  └─ site-build (existing) reads release `sims-import` with its own token -> new NFL SIMS tab
```

Why this shape:
- **No live connection, no shared database, no shared credentials.** SIMS never gets access to the V5 repository; V5
  never runs simulations or touches the SIMS database.
- **Versioned and immutable.** Every export is a new dated file with a SHA-256 and the SIMS model version, report id and
  report version inside it; nothing is overwritten on either side.
- **Secret isolation.** The only new credential is a read-only token for the SIMS repository, used by one small
  workflow. `site-build.yml`, `odds-collect.yml` and the Cloudflare/Odds API secrets are untouched (CLAUDE.md rules 2 and
  2c stay exactly as they are).
- **V5 is never blocked by SIMS.** A missing or invalid export only affects the new tab.

Alternatives considered and rejected:
- Passing a SIMS token into `site-build` (0 extra minutes, but changes the secret plumbing of the deploy workflow that
  CLAUDE.md 2c restricts to exactly two secrets).
- Committing exports to a SIMS branch (each commit runs SIMS CI, ~2 minutes).
- Giving the SIMS session write access to the V5 repository (too broad).
- Calling the SIMS app's API (it is not hosted anywhere).

## 3. What the export contains (NFL only; whitelisted fields)

`sims_nfl_projections_<UTC>.json.gz` (after Saturday's official forecasts; also after any official rerun):
- `format`, `schema_version`, `exported_at`, `sims_git_commit`, `model_version`, `model_fingerprint`, `season`, `week`.
- Per game: game id, teams, kickoff, official forecast id, revision, `created_at`, `n_sims`, ledger entry hash; win
  probability (+ MC SE); mean / median / p10-p90 of each team's score, margin and total; modal score and its frequency;
  **representative game** final score and box score lines.
- Per player: name, team, opponent, position, `play_prob`, `anytime_td`, and for each projected stat: mean, median, SD,
  p10, p25, p75, p90, min, max, valid simulations, plus a compact distribution (20-bin histogram or the exact tally when
  small).
- Not exported: seeds, inputs blobs, per-simulation archives, internal paths, source URLs of private notes.

`sims_nfl_edges_<slate>_v<k>_<UTC>.json.gz` (after Sunday publish, and after any revision):
- Report meta as stored (slate, kind, version, status, `rows_sha256`, built_at, thresholds, books, markets, odds window,
  warnings, excluded games, credits used, disclaimer).
- Top 5 rows **and** all analysed offers: player, team, opponent, market, side, line, sportsbook, American odds,
  `odds_at`, mean/median/p10/p90, raw simulated probability, break-even, raw ROI, calibrated probability, calibrated
  ROI, conservative ROI, market no-vig probability, quality, flags, forecast id/time, model version, rank.

`sims_nfl_history_<UTC>.json.gz` (after Tuesday grading): graded Top 5 and all-offer record by week (W-L-P, realized
ROI at the recorded price, CLV where available), plus forecast accuracy summary (coverage, Brier, calibration table) with
sample sizes and the model version each figure belongs to.

## 4. The website section (Phase 2)

New tab **"NFL SIMS"** on the existing site (NFL only; hidden on the CFB switch; no college props ever):
1. **Sunday Top 5 Prop Edges**: rank, player, team, opponent, market, side, line, book, odds, odds age, raw simulated %,
   calibrated %, break-even, raw ROI, calibrated ROI, conservative ROI, quality flags; report version and status.
   - Calibrated ROI is labelled "model expectation after calibration, not a realized return" and shown next to the
     **forward record** (n picks, W-L, realized ROI) with a sample-size warning. If the report has no calibration value
     for a row, it shows "—" and "not calibrated", never a derived number.
   - Zero picks is shown as "No offer met the rules this week", not hidden.
2. **Player projections** by game: mean, median, 10-90% range, play probability, anytime TD, small distribution bars;
   game card with win probability, mean/median score and the representative game's final score.
3. **Track record**: weekly graded results and the accuracy summary exactly as SIMS reports them, with dates, sample
   sizes and model versions.
4. Every value carries its SIMS provenance line: model version, forecast time, export time, report id/version, hash.

Separation from the sportsbook: separate module (`web_v4/sims_section.py`), separate snapshot key (`sims_nfl`), no
import of `sportsbook_v4` (and vice versa), tested like the existing isolation tests. Book Need, sharp vs public,
cohorts, settlement and the ledger are not read or changed.

## 5. Freshness, stale odds and failure handling

How often it needs updating: three moments a week. Saturday evening (projections), Sunday ~9:30 CT (Top 5), Tuesday
morning (grades). Plus any SIMS official rerun (late scratch). The website already rebuilds every 45 minutes in the 6 hours
before NFL kickoffs, so no extra builds are needed.

| Situation | What the page shows |
|---|---|
| Odds older than 90 minutes (the SIMS rule) at the viewer's clock | "ODDS STALE: as of 9:21 CT, prices may have moved", pick greyed |
| Game kicked off | "STARTED: no longer actionable", kept for the record |
| Projection superseded by a newer official forecast | only the newest is shown, with its time; older stays in the archive |
| Export missing or late | "No SIMS export for this slate yet (last export: ...)"; rest of the site normal |
| Export invalid (hash, schema, CFB rows, probability outside 0-1, future timestamps, unknown model version) | rejected by `sims-import`, not copied; tab keeps the last valid export with a red "latest SIMS export rejected: reason" |
| SIMS session lost / database missing | no new exports; tab ages out with the warnings above; nothing reconstructed |
| Sunday report revised | newest version shown, labelled "revision k"; v1 kept |

## 6. Infrastructure impact

| Item | Estimate |
|---|---|
| Odds API credits (V5 subscription) | **0**: V5 makes no prop requests |
| Odds API credits (SIMS free key) | **0 extra**: the export reads what SIMS already collected |
| `sims-import` runs | 5 scheduled runs a week (Sat 20:15 CT, Sun 9:45 and 10:30 CT, Tue 9:00 CT, Wed 9:00 CT catch-up), ~15 s each, 1 billed minute each = **~22 minutes/month** |
| `site-build` | one extra small download per build (~1-2 s); builds already bill 2 minutes; risk only if a build already near 120 s crosses it. To be measured on real V5 builds before Phase 2 ships |
| SIMS repository | no git pushes for exports (release assets do not trigger CI) |
| Monthly worst case | 1,853 (current) + 22 = **1,875**, plus SIMS pushes (~2 min each). Realistic ~1,650-1,750. |
| Cloudflare | 0 extra deployments (content rides on existing builds) |
| Storage | ~100-300 KB per export compressed, ~20 files a month |

Remaining budget risk: the 2,000 minutes are shared by both private repositories. Heavy SIMS development pushes (2 minutes
each) are now the main unbudgeted item; optional mitigation for the owner: skip SIMS CI on documentation- and ledger-only
pushes (a SIMS change, separate approval).

## 7. Files and workflows that would change

SIMS repository (separate approval; the model, forecasts, database and existing Sunday tasks unchanged):
- `arbsims/export_web.py` (new): read-only export builder; `arbsims/cli.py`: `arbsims export web --kind projections|edges|history`.
- `tests/test_export_web.py` (new): whitelisted fields only, NFL only, sha256, immutability, round-trip against the stored report.
- Three **new** scheduled tasks in the SIMS session, each ~20 minutes after the existing ones (Sat ~19:10 CT, Sun ~9:35
  CT, Tue ~8:10 CT): run the export and upload it. They never edit the existing Saturday/Sunday/Tuesday tasks and spend 0 credits.
- Owner: create release `web-export` once (or the first export creates it).

V5 repository:
- `.github/workflows/sims-import.yml` (new): schedule above + manual; permissions `contents: write` (own repo) only;
  the read token is passed to one download step only; no third-party actions; never `pull_request`.
- `.github/scripts/sims_import.sh` and `sims_v5/importer.py` (new): download, verify, copy valid files to release `sims-import`.
- `sims_v5/schema.py` (new): the export schema and validators.
- `web_v4/sims_section.py` (new) and `web_v4/live_site.py` (tab, rendering, freshness badges; one load call).
- `.github/workflows/site-build.yml`: one step downloading release `sims-import` with the existing `GITHUB_TOKEN` (no new secret).
- `tests_web/test_sims_section.py`, `tests_v4/test_isolation.py` additions; `tools_v5/release_check.py`: new check
  "sims_isolated" (Need, sharp vs public and the ledger byte-identical with and without a SIMS export).
- `CLAUDE.md`: a rule for the new token (like 2b/2c): only `sims-import.yml` may use it; read-only; never printed.

Owner actions (browser only, no coding):
- Create a **fine-grained personal access token**: repository `arbitredge-sims` only, permission Contents: Read-only,
  expiry 1 year; save it as repository secret `SIMS_EXPORT_READ_TOKEN` in `arbitredge-v4`. Calendar a renewal.
- Approve the new SIMS command and the three SIMS export tasks.

## 8. Testing without touching V5 production

1. SIMS: unit tests on fixtures and a copy of the restored backup database; export validated against the stored
   report's `rows_sha256`.
2. First real exports go to a **test release** (`web-export-test`), not `web-export`.
3. V5: importer and validators tested on fixtures (valid, missing, tampered hash, CFB row, out-of-range probability,
   future timestamp, wrong model version, oversized file).
4. Local site builds (as in the V5 release checks) with and without an export; browser checks on desktop and phone;
   `release_check` all 10 checks, including the new isolation check.
5. `sims-import` run manually against the test release, writing to a test release here (`sims-import-test`) that the
   site does not read.
6. Then switch both to the real releases on a branch merge, with the owner's approval.

Rollback: the tab disappears if `sims-import` is empty or the section is reverted (one commit); disabling
`sims-import.yml` stops imports; nothing in the sportsbook depends on it. The V5 m4 rollback path is unchanged.

## 9. Step-by-step implementation (Phase 2, after approval)

1. SIMS: `arbsims export web` + tests (no model change). Export a real projections file from the restored backup to
   the test release.
2. V5: schema, importer, `sims-import.yml` (manual trigger only at first), tests.
3. V5: section rendering and freshness logic, browser-tested locally; isolation check in `release_check`.
4. Owner creates the read token; manual end-to-end run on test releases; review the tab locally.
5. Owner approval -> switch to real releases, enable the `sims-import` schedule, create the three SIMS export tasks.
6. First live Saturday/Sunday: watch the export, import, build and page; report.

Open decisions for the owner:
- Approve the token-based pull (recommended) vs any alternative in section 2.
- Show all analysed offers or only the Top 5 (recommended: Top 5 prominent, full table collapsed).
- Whether to include SIMS game forecasts (win probability, scores) or player props only.
