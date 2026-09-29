# Shareable app release checklist

Updated: 30 September 2026. This is the current release plan and status record.
Historical plans and evidence are preserved in [archive](archive/README.md).
Check a release gate only after its stated evidence is recorded; implemented code is not deployed verification.

## Release decision

**Not ready to promise reliable 1,000-file uploads.** The code accepts up to 1,000 batch members,
with three concurrent transfers, retries and durable progress. No live 1,000-file test has passed.
Uploading and parsing/extracting 1,000 documents are separate acceptance gates.

Current evidence: [30 September release record](RELEASE_EVIDENCE_2026-09-30.md),
[29 September release record](RELEASE_EVIDENCE_2026-09-29.md),
[user smoke/runbook](RELEASE_RUNBOOK.md), [capacity procedure](CAPACITY_TEST_PROCEDURE.md),
and [synthetic export benchmark](EXPORT_BENCHMARK.md).

- Branch `feat/dark-blue-ui`: `4fc7a2e` commits the 29 September deployed working tree and release docs;
  `32476d5` adds the dark blue UI and 3-way parse/extract parallelism. Details in the 30 September record.
- App is RUNNING with deployment `01f1bc5e24d710a9a997e9d6b2b5b197` (30 September); authenticated health
  passed and deployed assets match the local build. Rollback reference: `01f1bc532ec01c13a89d8c0a68ccd4c2`.
- Parser and extractor Jobs run up to 3 documents at a time each (combined budget 6).
- Additive upload/work, export and viewer migrations applied; all 27 project tables/views verified (29 September).
- Narrow app grants and dispatcher/export permissions verified; recovery schedule remains PAUSED.
- Local checks: 252 backend and 83 frontend tests, lint/type checks, production build and configuration
  validation passed. No agent PDF uploads or live inference calls.

## Path to a demo (excluding the 1,000-file test)

A demo means you can walk someone through upload → prepare → extract → review with citations →
export → ask Genie, on the live app, without surprises. Order matters: each step uses the previous one.

### Must do before a demo

1. [ ] **User — run the small live workflow (section 2) on the new UI.** Upload 3–5 representative PDFs
   (include one multi-page and one duplicate), Prepare selected, Extract selected with the invoice schema,
   open a result and click citations, export XLSX, delete one disposable file. This has not been run on
   the new UI or with 3-way parallelism. Cost: a handful of parse/extract calls.
   Agent then verifies registry, volume, provenance, citations and export against the runbook.
2. [ ] **Agent — fix the three demo papercuts found on 30 September**, then redeploy:
   the Documents list does not refresh itself while documents are extracting; the selection bar defaults
   to the alphabetically first schema (HUD voucher) instead of the invoice schema; and confirm what happens
   when two single-document runs are started at once (queueing is disabled, so the second may be skipped
   and shown as failed). Enable Job queueing if it is.
3. [ ] **User approves / agent configures — attach data sources to Genie.** The live space has no data
   sources, so it cannot answer questions yet. Attach the project's structured extraction views
   (the four generic Genie views created on 29 September) to the existing space through Databricks,
   preserving its curation, then regenerate the overlay before the next deploy. Check that the embed
   loads inside the app for your account (allowed origins) and that 2–3 prepared questions work.
4. [ ] **Agent — write the demo script and seed plan.** A short click-by-click script, 3–4 prepared
   Genie questions and the documents to use, relying on retained results so a demo rerun does not
   repeat inference. Record the rollback deployment ID (above) with it.

### Should do

5. [ ] **User decision — automatic preparation.** Turning it on (plus the hourly recovery schedule)
   makes the demo "upload, then extract" with no Prepare step. Low cost for a small demo set; needs
   your approval (section 1). If left off, the UI already explains the Prepare step.
6. [ ] **Before each demo:** start the SQL warehouse a few minutes early (it auto-stops after 10 minutes;
   cold starts make the first pages slow) and open each page once.
7. [ ] **If someone else will log in:** give them app access and run the colleague check (section 5).
   Not needed if you drive the demo from your own account.

### Known limits to mention (or avoid) during a demo

- Schema save/publish takes about 5–6 seconds (section 4); create schemas before the demo.
- PDF full-text search in Genie is not enabled; Genie answers from extracted results only.
- Capacity beyond small batches is unproven until section 3 runs.

## Responsibility and approval rules

Execution is underway; completed items require the linked evidence. Live user acceptance remains separate.

- **Agent — independent work:** already authorized; proceed without asking again when prerequisites
  are available. Includes additive migrations, narrow permissions for existing project identities,
  deployment, local/synthetic tests, schema-save and first-load/viewer measurement, fixes and remeasurement.
- **User — action or approval:** user-operated uploads, workspace/account decisions, real-document
  inference budgets, indexing/scheduled workload activation, colleague testing and release acceptance.
  An unchecked item here is not permission to execute it automatically.
- A dependency on a user task is not a new approval requirement: continue other independent work
  while waiting. Report platform/permission blockers with the exact action needed.
- Preserve existing data and Genie curation. Destructive changes to retained data or expanding access
  beyond the intended project identities need separate approval. Use disposable agent-created schemas
  for save/publish measurements; do not modify users' schema definitions for benchmarks.

## 1. Restore and prepare the target workspace — release blocker

### Agent — independent work

- [x] Record workspace/profile, release commit or snapshot, catalog/schema, volume names and resource IDs.
- [x] Verify bulk-upload tables and apply the additive `migrate_work_batches.sql` migration.
      Verify all upload/work tables, not only the two upload tables. No further approval is needed.
- [x] Apply/verify export and viewer migrations; verify generic projections/views.
- [x] Apply and verify narrow table/volume and dispatcher/export Job permissions for existing project
      identities. Job IDs in environment variables do not grant access; check the App resource budget.
- [x] Refresh the existing Genie definition before bundle planning; preserve user curation and never
      deploy the stale first-create generated overlay.
- [x] Build, run release checks, inspect deployment changes, deploy and verify authenticated health,
      actual feature flags, current frontend assets and Genie URL. Record the deployment ID.
- [x] Enable viewer projections and verify their prerequisites. Prepare queue/extraction/export settings
      and explain which settings start background processing before the user-run smoke test.

### User — action or approval

- [x] Resolve workspace/account availability or select an eligible replacement workspace.
      Existing workspace accepted app start on 29 September; app is now RUNNING. No replacement needed.
- [ ] Approve activation of automatic preparation, durable processing and scheduled recovery with the
      intended workload/budget. Agent configures them after approval and prerequisite verification;
      scheduled recovery remains paused until the small workflow passes.

## 2. Verify a small complete workflow — release blocker

### Agent — independent work

- [x] Prepare a short test script and expected outcomes for the user, including a multi-page PDF and duplicate.
- [ ] After the user uploads, confirm each accepted source exists in the volume with its registry entry.
- [ ] Inspect the resulting parse/extraction provenance, citations and exports for consistency.
- [ ] Test recovery and failed-only retry locally with synthetic fixtures; inspect live outcomes after
      user-run interruption/retry checks. Successful members must not be processed again unnecessarily.
      Local synthetic checks passed; live interruption/retry inspection awaits the user.
- [x] Verify historical runs retain their original parse/schema identity after newer runs exist.
- [ ] Verify idle app stop/start preserves durable state without redeployment; coordinate timing so it
      does not interrupt the user's test. Independent Jobs/schedules are separate from app stop.
- [ ] Test deletion/partial failure locally; verify live volume/registry outcomes after the user deletes
      their disposable fixture. File removal and registry update are not atomic; retained results,
      page images and exports are not a full purge.
      Local partial-failure regression passed and missing-file retry was fixed; live deletion awaits the user.

### User — action or approval

- [ ] Upload the small representative set through the UI.
- [ ] Run parsing/extraction with a published schema within the agreed test budget, review evidence
      and download the export. Agent can inspect outcomes without requesting approval again.
- [ ] Exercise refresh, file reselection and retries through the UI using the prepared test script.
- [ ] Delete a designated disposable test document through the UI and confirm the expected experience.

## 3. Prove 1,000-file upload readiness — required release gate

### Agent — independent work

- [x] Prepare local synthetic capacity/recovery tests and a staged live test procedure; do not upload
      real files automatically. Verify configured limits and three-transfer concurrency.
- [x] Prepare outcome reconciliation and measurements for duration, bytes, file sizes/pages,
      failures/retries, gateway errors and browser/API memory.
- [ ] After user-run batches, account for all 1,000 outcomes: accepted, duplicate, explicitly rejected
      or failed. No silent drops; every unique accepted source must have consistent registry metadata.
- [ ] Verify responsive progress, bounded memory, pagination and selection; inspect retry/ambiguous
      response behavior for duplicate documents. Fix issues and rerun independent tests.
- [ ] Inspect durable queue recovery, progress and inference concurrency from the separately authorized
      processing test. Record upload and processing results separately.

### User — action or approval

- [ ] Select the representative PDF set, size/page distribution and total bytes, eligible workspace,
      test budget and acceptable completion time. Agree pass/fail thresholds before the live test.
- [ ] Run progressively larger uploads after the small smoke passes, ending with **one 1,000-file batch**.
      Keep configured limits and stop on quota/service errors.
- [ ] Exercise pause/resume, refresh with reselection and interrupted-transfer recovery through the UI.
- [ ] Authorize and run the separate real-document parsing/extraction capacity test within budget.
      Upload success alone does not prove 1,000-document processing capacity.
- [ ] Accept the recorded 1,000-file results only after remaining failures are explained or fixed.

## 4. Verify first-load and save performance — release gate

### Agent — independent work

- [x] Implement local schema-save reduction: remove one redundant SQL read and post-mutation list
      reload; update the published editor state immediately.
- [x] Focused checks passed: 43 backend tests, 3 schema-editor tests, Python/frontend lint and type
      checks, production build and configuration validation.
- [x] Deploy and measure schema create/save/publish with disposable test schemas, including first and
      repeated requests. These performance tests need no further approval.
- [ ] Measure document/results/schema page and viewer first loads with cold and warm warehouse separately;
      record p50/p95, Server-Timing SQL count/duration, payload size, image transfer/decode and render.
      Use retained results; do not repeat AI inference for navigation benchmarks.
- [x] Verify viewer projections and backfill a small retained-result set without repeating inference.
- [ ] Improve measured schema save/publish latency (5.35–6.28 s save; 4.81–6.17 s publish,
      already-running warehouse), currently above the proposed 3-second target. See HTTP evidence.
- [ ] Reduce measured repeated metadata work/sequential requests; preserve deletion checks, historical
      provenance and cache invalidation. Rerun measurements and regression checks after fixes.
- [x] Propose measurable latency targets and report against them; historical timings are not current measurements.
      Preliminary current samples and proposed targets are in the release evidence; user acceptance remains open.
- [ ] Benchmark large exports with synthetic retained results; record duration, peak memory and temp disk.
      Verify deployed download and expiry with disposable artifacts; document cleanup ownership.
      Local 1,000-document CSV/XLSX benchmark completed; deployed expiry/download remains open.

### User — action or approval

- [ ] Confirm the proposed latency targets meet expectations and review the improved UI when ready.
      This acceptance does not block agent measurement, fixes or remeasurement.

## 5. Verify colleague access and release operation — release gate

### Agent — independent work

- [ ] Test batch/export ownership restrictions locally and inspect intended Genie/source permissions.
- [x] Document start/stop, retry/recovery, support, known limitations and artifact retention/cleanup.
- [ ] Prepare the repeatable demo, regression evidence, deployment ID and rollback procedure.
      Deployment and rollback IDs recorded 30 September; demo script is item 4 of "Path to a demo".
- [ ] Investigate and fix issues reported from colleague testing; do not impersonate the colleague.

### User — action or approval

- [ ] Test with an ordinary colleague account: app, documents, viewer, schemas, exports and Genie.
- [ ] Confirm project-shared visibility is intended; uploader identity is not per-user isolation.
- [ ] Confirm support ownership/retention policy and sign off the shareable release.

## 6. Genie/content search — separate eligible-workspace gate

May be deferred for a pilot only if users are clearly told document content search is unavailable.

### Agent — independent work

- [x] Preserve the existing Genie space and prepare source-volume/structured-view setup instructions.
      The source volume exposes original files, not our extracted-result tables.
- [ ] Verify the embed URL and inspect allowed-origin configuration; fix app integration as needed.
- [ ] Attach the structured extraction views to the existing space; the 30 September export shows
      `data_sources: {}`, so Genie cannot answer result questions yet. Needs user approval of the sources.
- [ ] Prepare upload → sync → search and deletion → sync → search checks; inspect results after the
      user-operated tests. Do not assume immediate index removal or continuous synchronization.
- [x] Document sync ownership and how index freshness is communicated.

### User — action or approval

- [ ] Select the eligible workspace and configure/approve the source volume and optional structured
      extraction views in the Genie space after creation.
- [ ] Approve preview/content-search activation and indexing budget; enable/sync through supported controls.
- [ ] Run upload/deletion/search checks with disposable fixtures and verify colleague access.
- [ ] Confirm ongoing sync ownership, or explicitly defer content search for the pilot.

## Deferred unless explicitly added to release scope

Lakebase migration, large codebase reorganization, set-based inference rewrite and LLM validation.
Keep Delta for retained results and volumes for files; revisit operational storage after measurement.

## Evidence to record for every completed gate

Date, workspace, release snapshot, workload, feature flags, test/run IDs, measured result, limitations
and verifier. Never include credentials or client document contents. No 1,000-file capacity claim
is valid until section 3 has recorded evidence.
