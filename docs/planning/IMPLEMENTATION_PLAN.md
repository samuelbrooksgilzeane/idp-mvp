# Implementation plan: bulk intake and managed document chat

Created 30 September 2026 on `feat/dark-blue-ui`. One chat per batch.

## How to use this file (read first, every batch)

- Start a new chat with: `Do Batch N from docs/planning/IMPLEMENTATION_PLAN.md`.
- Read **Decisions**, **Facts** and **your batch only**. Facts below were verified on 30 September 2026;
  do not re-measure or re-research them unless the batch says so.
- Keep usage low: grep before opening large files (`styles.css`, `validate_configuration.py`,
  `document_registry.py`); run focused tests while working
  (`uv run --project backend pytest backend/tests/<file> -q`, `cd frontend && npx vitest run <file>`);
  run `make check` once at the end. Do not rebuild `frontend/dist` unless the batch deploys.
- No live workspace calls unless the batch lists them. Free Edition quota overruns stop all compute
  for the rest of the day.
- Finish by updating the **Progress** table, your batch's `Status` line, and a one-line
  `Notes for next batch` if something changed. Commit on the working branch.

## Decisions (30 September 2026)

- **Workspaces:** develop on the existing Free Edition workspace (profile `idp-mvp`). Production is a
  US paid workspace with all features and no budget cap; heavy tests happen there.
- **Genie is dropped:** Volume Content Search excludes catalogs with workspace bindings.
- **Chat:** Knowledge Assistant (KA) + Supervisor Agent + Databricks chat template
  `e2e-chatbot-app-next` with Lakebase history (option B in `DOCUMENT_CHAT_OPTIONS.md`; its
  preference for option A assumed Free Edition only). KA is unavailable on Free Edition in every
  region, so Free Edition gets the chat app and history against a foundation-model endpoint; KA and
  Supervisor are built on the US workspace.
- **Access:** everyone. Apps access data as their service principals. An all-users group gets
  CAN USE on both apps, WRITE on a new import volume `idp_import`, READ on `idp_source`. Only the app
  and jobs write `idp_source`, so nothing unregistered is indexed by KA.
- **Upload paths:** browser upload for drag-and-drop; folder import for any size (no minimum), the
  recommended path above ~100 files.
- **Capacity testing:** none on Free Edition beyond smoke tests of at most 20 files. Runs of
  100 to 1,000 files happen on the US workspace (Batch 8).

## Facts (verified 30 September 2026)

**Upload path today**
- Caps: 1,000 files per batch (frontend and `CreateUploadBatchRequest`), 25 MiB per file. Three
  parallel transfers, hard-coded in `drain()` in `frontend/src/hooks/useUploadBatch.ts`.
- About 13 SQL statements per new file in `UploadBatchService.upload`
  (`backend/src/idp_app/services/upload_batches.py`): header SELECT, item SELECT, UPLOADING
  UPDATE+SELECT, hash-bind UPDATE+SELECT **twice** (`on_content` runs before storage and again after,
  in `DocumentService.upload`), `find_by_hash`, registry MERGE + `find_by_hash`, REGISTERED
  UPDATE+SELECT. Plus Volume `create_directory` and `upload`. `compare_and_set` in
  `batch_repository.py` does UPDATE then SELECT to confirm `transition_id`.
- Dev measurement (4-file batch, 29 September): ~15–17 s per file alone, 3 files concurrently ~26 s.
  Warm point SELECT ~0.5 s; Delta UPDATE/MERGE ~1.5–2 s.
- `upload_items` and `documents`: deletion vectors and row tracking enabled, unpartitioned, so
  row-level concurrency applies (also under `upload_items`' Serializable isolation). Different files
  do not conflict.
- A 1,000-item manifest parameter (~730 KB) is accepted; a 1,000-row SELECT returns in one chunk.
  `execute_sql` (`document_registry.py`) reads only the first result chunk — fine at this size.
- App gateway serves HTTP/2 (browser connection limit is not a constraint).
- Defaults not overridden in bundle: `upload_claim_seconds` 1800 (lease), `max_upload_attempts` 5.
- Frontend treats 409 as final; 401/403 are final per file, so an expired sign-in fails every
  remaining file. File handles are lost on refresh (user reselects; completed files are skipped).
- Items API cursor is limited to `le=999` (`api/upload_batches.py`), matching the 1,000 cap.

**Genie footprint** (`git grep -il genie`, excluding `docs/archive`, dated `RELEASE_EVIDENCE_*`,
`docs/evidence`, `output/`, `frontend/dist`): `frontend/src/pages/AskGeniePage(.test).tsx`,
`App.tsx`, `components/WorkflowHeader.tsx`, `pages/DocumentsPage.tsx`, `types.ts`, `styles.css`;
`backend/src/idp_app/api/app_config.py`, `core/config.py`, `main.py`,
`backend/tests/test_genie.py`, `test_genie_bundle.py`; `app.yaml`,
`databricks_etl/databricks.yml` (5 `genie_*` variables), `resources/application.app.yml` (5 env
vars), `resources/bootstrap.job.yml` (`create_genie_views` task), `sql/create_genie_views.sql`,
`databricks_etl/genie/`; `scripts/provision_genie.py`, `prepare_genie_bundle.py`,
`validate_configuration.py`, `workspace_smoke.py`; `.gitignore`; `README.md`,
`docs/DEPLOYMENT_NOTES.md`, `RELEASE_RUNBOOK.md`, `RELEASE_CHECKLIST.md`.

**Caution:** the live Genie space is bundle-managed through gitignored
`databricks_etl/resources/genie.generated.yml` (included by `resources/*.yml`). Deploying without it
**deletes the space**. Before any deploy after Batch 1, either unbind it
(`databricks bundle deployment unbind <resource-key>`) or delete it deliberately with the user's OK.

**Platform**
- Free Edition: no KA; up to 3 apps, each stopped 24 h after start/deploy; one AI Search endpoint;
  one Lakebase project; one 2X-Small SQL warehouse; quota overrun shuts compute down for the rest
  of the day; no commercial use. The dev app was stopped by "workspace or account status" on
  30 September.
- KA: sources are UC volume directories (pdf, docx, pptx, md, txt), tables, or AI Search indexes;
  skips files over 100 MB or 500 pages; file sources need **Sync** (incremental) after changes;
  exposes an agent endpoint.
- Supervisor Agent: combines KA endpoints, UC functions, MCP servers (external, UC-registered,
  custom), agent endpoints and web search, up to 50; SDK management is Beta; users (or the calling
  app's service principal) need CAN QUERY on the supervisor and each subagent, EXECUTE on functions.
- Chat template `e2e-chatbot-app-next` (github.com/databricks/app-templates): Next.js; works with
  KA, Supervisor, agents on Apps or Model Serving, and foundation-model chat endpoints; history in
  Lakebase (in-memory by default); renders tool calls; no image input.
- Live dev: app `idp-mvp-dev`, warehouse `647704f77f24020a`, tables `workspace.idp_mvp.idp_dev_*`,
  source volume `workspace.idp_mvp.idp_source` (PDFs stored as `incoming/<document_id>.pdf`).

## Progress

| Batch | Title | Sessions (est.) | Status |
|---|---|---|---|
| 1 | Branch and Genie removal | 1 | Done (user to run the Genie unbind locally) |
| 2 | Upload speed | 1 | Done (live DML result shape unconfirmed) |
| 3 | Upload robustness | 1 | Not started |
| 4 | Folder import | 2 | Not started |
| 5 | Chat foundation on Free Edition | 2 | Not started |
| 6 | US workspace deployment | 1 | Blocked: US workspace access |
| 7 | Managed chat on US | 2 | Blocked: Batch 6 |
| 8 | US capacity runs, access, retirement | 1 + user test time | Blocked: Batch 7 |

Batches 1–5 need only the Free Edition workspace (and mostly none). A session is sized to about one
five-hour usage window.

## Batch 1 — Branch and Genie removal

Status: Done 30 September 2026, except step 5 (user decision pending). Work stayed on
`feat/dark-blue-ui`: the session was restricted to that branch, so `feat/chat-and-bulk-intake` was not
created. `make check` passes; `frontend/dist` was not rebuilt (still contains the old Genie page).

Notes for next batch: `/api/app-config` now returns `{project_name, chat_app_url}`; the CSP is always
`frame-src 'none'`; `workspace_smoke.py` makes two checks (warehouse, apps). The bundle default for
`chat_app_url` is `" "` (Apps rejects empty env values; Settings strips it). The unbind/delete
decision (30 September): **unbind**. The user runs `databricks bundle deployment unbind project_genie -t dev
-p idp-mvp` with the old `genie_*` vars from a checkout that still has the overlay, then deletes
`genie.generated.yml`. Confirm this has been done before any deploy.

1. `git switch -c feat/chat-and-bulk-intake` from `feat/dark-blue-ui`; commit `docs/planning/`.
2. Remove Genie from every file in **Genie footprint**. Leave `docs/archive/`, dated
   `RELEASE_EVIDENCE_*`, `docs/evidence/` and `output/` untouched (historical). Keep the
   `.gitignore` entry until the live space is unbound or deleted.
3. Replace the views: move `create_genie_views.sql` to `create_chat_views.sql` producing
   `<prefix>_chat_{documents,extractions,fields,records}`; rename the bootstrap task. Batch 5 builds
   UC functions on them. Do not drop the old `_genie_*` views (Batch 8).
4. Sidebar: replace "Ask Genie" with "Ask documents", an external link from a new setting
   `IDP_CHAT_APP_URL` (`/api/app-config`), hidden when empty. Add the bundle variable `chat_app_url`
   (default empty).
5. Ask the user: unbind or delete the dev Genie space. Do not deploy in this batch otherwise.

Done when: `make check` passes; `git grep -il genie` only hits the excluded paths and `.gitignore`.

## Batch 2 — Upload speed

Status: Done 30 September 2026, except the live check in step 2. A new file now costs 5 statements
(join SELECT, UPLOADING UPDATE with hash, `find_by_hash`, registry MERGE, outcome UPDATE), asserted
in `backend/tests/test_upload_statement_budget.py` against a fake Statement Execution API.

Notes for next batch: `DatabricksDocumentRegistry.execute_dml` returns row counts by manifest
column name (`num_affected_rows`, MERGE also `num_inserted_rows`), assuming DML returns a one-row
result. If counts are missing, `compare_and_set` and `add` fall back to the old read-back, so a
wrong assumption costs speed, not correctness. **Still open:** confirm on the dev warehouse with one
UPDATE and one MERGE on a throwaway table in a scratch schema (user's OK needed; this cloud session
has no Databricks CLI or credentials). `add` skips the read-back only on `num_inserted_rows` ≥ 1,
because a revived deleted row keeps its old `document_id`. A body that fails validation (type,
size, signature) is now recorded as FAILED before any storage work. `upload_parallel_transfers`
(env `IDP_UPLOAD_PARALLEL_TRANSFERS`, bundle var, 1–8) reaches the browser through
`/upload-batches/limits` as `parallel_transfers`.

Goal: at most 5 SQL statements per new file, configurable parallel transfers.

1. Split `DocumentService.upload` into `stage(upload)` (type, size, signature, SHA-256, spooled
   file) and `store_and_register(staged, metadata, requester)`. In `UploadBatchService.upload`,
   stage first, then one UPLOADING transition that also sets `content_sha256`; delete `bind_content`
   and the `on_content` hook. Keep the existing lease check before registering (local time check).
2. Make `execute_sql` able to return DML results. `compare_and_set` uses `num_affected_rows`
   and only SELECTs back when the statement errored after submission. Confirm the result shape on
   Databricks with one UPDATE on a throwaway table in a scratch schema — ask the user first.
3. One SELECT for batch header plus item (join) replaces `authorize` + `item` in the upload path.
4. `DatabricksDocumentRegistry.add`: use MERGE `num_inserted_rows`; look up only when it is 0.
5. `IDP_UPLOAD_PARALLEL_TRANSFERS` (default 3, max 8) in `core/config.py` → `/upload-batches/limits`
   → `useUploadBatch.ts`. Add to `app.yaml`, `application.app.yml`, `databricks.yml`.
6. Log each Delta conflict retry in `DatabricksBatchRepository.write` (error code, attempt).
7. Tests: a counting fake SQL client asserts ≤5 statements for a new file and correct duplicate
   handling; frontend test for configured concurrency. Keep `test_upload_batches.py`,
   `test_documents_api.py`, `useUploadBatch.test.ts` green. SQLite path keeps working.

## Batch 3 — Upload robustness

Status: Not started

1. Default `upload_claim_seconds` 300 (lease starts after the body has arrived).
2. Frontend: 409 `UPLOAD_BUSY` is retryable with backoff (about 5 s, 15 s, 30 s).
3. Retry transient warehouse failures (not only `DELTA_CONCURRENT`) for statements safe to repeat:
   revision-checked UPDATEs and MERGEs. Bounded attempts, short backoff.
4. `UploadBatchService.upload`: any exception after the UPLOADING transition attempts a FAILED
   transition, so an item is never left UPLOADING when the request ends.
5. After `drain()`, one automatic pass over retryable FAILED items after ~30 s.
6. Default `max_upload_attempts` 10.
7. Sign-in loss: `fetch(..., { redirect: "manual" })`; on 401, 403 or an opaque redirect, pause the
   whole batch and show "Sign in again, then Resume" instead of failing remaining files. Ask the user
   to check once in a browser how the Apps gateway answers an expired session; record it here.
8. `beforeunload` warning and Screen Wake Lock (feature-detected) while transfers are active.
9. Tests for each change in `useUploadBatch.test.ts` and `test_upload_batches.py`.

## Batch 4 — Folder import (2 sessions)

Status: Not started

Design: users copy PDFs into `/Volumes/<catalog>/<schema>/idp_import/<folder>/` (Databricks CLI
`databricks fs cp -r ./invoices dbfs:/Volumes/.../idp_import/<folder>`, or Catalog Explorer upload),
then choose the folder in the app. A Databricks Job registers them; no browser needed after start.

1. Bootstrap SQL and bundle variable `import_volume_name` for volume `idp_import`; app resource
   binding (read, write); grant plan for the all-users group (WRITE VOLUME, READ VOLUME).
2. API: `GET /api/imports/folders` lists top-level folders under the server-owned root (never accept
   a path from the browser; match the chosen name against the listing). `POST /api/imports` creates an
   upload batch (header payload `source: "folder"`, `client_file_id` = hash of the relative path,
   cap 1,000 files) and starts the import job run. Progress reuses the upload-batch endpoints.
3. Job `resources/import.job.yml` + `databricks_etl/src/import_folder.py`: for each item with bounded
   thread concurrency (default 8): stream from import, hash, dedupe (`find_by_hash`), copy to
   `idp_source/incoming/<document_id>.pdf`, registry MERGE, REGISTERED or ALREADY_REGISTERED, then
   delete the import file. Failures keep the file and record the error. Re-running is safe.
   First check how existing jobs import backend code (`work_runtime.py`, `parse_document.py`) and
   reuse `DocumentService`/`UploadBatchService` rather than duplicating logic.
4. App service principal gets CAN_MANAGE_RUN on the import job (as with the dispatcher).
5. UI: "Import from folder" in `UploadPanel.tsx` (folder picker) using `UploadBatchProgress`.
6. Tests: local mock mode end-to-end with SQLite and local volume; re-run idempotency; partial
   failure; duplicate content; folder name validation.
7. Live: deploy to Free Edition dev and smoke-test with at most 20 files (ask the user first).

## Batch 5 — Chat foundation on Free Edition (2 sessions)

Status: Not started

1. UC SQL functions over the `_chat_*` views for exact questions (for example invoice totals by
   supplier and date range, find documents by field value, fields of one document). Read the view
   columns first. Grant EXECUTE to the all-users group and the chat app principal.
2. Chat app: add `e2e-chatbot-app-next` under `chat_app/` with a bundle resource
   `resources/chat.app.yml`; Lakebase (Free Edition allows one project) for history; serving
   endpoint from an env variable, set to an available foundation-model chat endpoint on Free
   Edition (check the list). Batch 7 switches it to the Supervisor.
3. Set `chat_app_url` so the IDP sidebar links to it. Two of the three Free Edition apps are used.
4. Verify: a conversation survives an app restart; history lists previous chats; a second user
   cannot see the first user's chats (use a second account if available, otherwise record as open).
5. Note for users: on Free Edition the chat cannot answer from documents yet (no KA).

## Batch 6 — US workspace deployment

Status: Blocked: US workspace access

Needs from the user: US workspace host and CLI profile, admin rights, all-users group name.

1. Bundle target `us` in `databricks.yml` (host, catalog **without** workspace binding, schema,
   prefix, warehouse, `idp_source`, artifacts volume, `idp_import`, app names).
2. Create catalog, schema and volumes; run the bootstrap job; apply grants (Decisions → Access).
3. Deploy the IDP app, chat app and jobs; choose feature flags with the user (automatic preparation,
   bulk extraction, bulk export).
4. Smoke test with a few files: browser upload, folder import, prepare, extract, chat link.

## Batch 7 — Managed chat on US (2 sessions)

Status: Blocked: Batch 6

1. Feasibility checks, stop if any fails: KA accepts `idp_source/incoming` in the new catalog with
   5 test PDFs; Sync can be triggered through the SDK or REST API (else a scheduled job or an admin
   button); a deleted document disappears after Sync; record cost per question.
2. Create the KA (source `idp_source/incoming`) and a Supervisor (KA + Batch 5 UC functions; MCP
   servers later). Put creation in an idempotent `scripts/provision_chat.py` where the SDK allows.
3. Grants: chat app principal CAN QUERY on Supervisor and KA; EXECUTE on functions.
4. Point the chat app endpoint at the Supervisor.
5. Trigger KA Sync after each completed upload batch or import run (one sync at a time, debounced).
6. Citations: tweak the template to turn `<document_id>.pdf` into links to the IDP viewer
   `/documents/<document_id>`.

## Batch 8 — US capacity runs, access, retirement

Status: Blocked: Batch 7

1. User runs `docs/CAPACITY_TEST_PROCEDURE.md` on US: browser 100 and 300 files; folder import 100,
   500 and 1,000. Record results in a new evidence doc.
2. Grant the all-users group access; write a short user guide (browser upload versus folder import,
   chat).
3. With the user's OK: delete the Free Edition Genie space and the old `_genie_*` views; remove the
   `.gitignore` entry.
