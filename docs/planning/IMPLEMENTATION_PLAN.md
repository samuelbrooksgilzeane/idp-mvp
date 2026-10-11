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
  preference for option A assumed Free Edition only). **Revised 30 September:** the user expects
  KA to work on Free Edition, so Batch 7 builds KA + Supervisor there first, behind gate **G7**
  (Batch 7). The docs checked on 30 September said Free Edition excludes KA; G7 settles it in the
  workspace. The US workspace later reuses the same provisioning script.
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
- Free Edition: KA listed as excluded in the docs on 30 September (re-check in gate G7); up to 3 apps, each stopped 24 h after start/deploy; one AI Search endpoint;
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

## Facts from the US deployment (10–11 October 2026)

Deployed by hand from `main` (`f24f4cc`) in a cluster's web terminal: catalog `idp_testing`, schema
`idp_mvp`, prefix `idp_dev`, `-t dev` with `--var` overrides, app `idp-mvp-dev`. Batches 9 and 10
exist to make the next workspace need none of the steps below.

- **Deploying needs only the Databricks CLI.** `frontend/dist` is committed and matched its source;
  the app installs its Python packages itself. Only `make deploy`'s `check` needs uv, Node and make.
- **Cluster web terminal traps:** `DATABRICKS_CLUSTER_ID` is set there, and in `mode: development`
  the CLI then moves every job onto that cluster (it refused: not all-purpose); it must be unset. uv
  fails writing its cache under `/Workspace` ("operation not permitted"). Browser login is
  unavailable, so the profile used a personal access token.
- **A Git folder has no `.git`,** so bundle sync ignored `.gitignore` and uploaded `backend/.venv`;
  the app deploy then failed: "exporting symlink ... in source format is not supported".
- **`app_access_grants` is incomplete.** On Free Edition these were granted by hand; on US the first
  upload failed (HTTP 500, `INSUFFICIENT_PRIVILEGES` on `idp_dev_upload_batches`) until the app's
  service principal got `SELECT, MODIFY` on `upload_batches`, `upload_items`, `work_batches`,
  `work_items`, `work_dispatches`, `invoice_line_candidates`, and `CAN_MANAGE_RUN` on the
  `work_dispatcher` and `results_exporter` jobs. The four non-chat views are unused by the app. The
  bootstrap does not create `export_requests`/`export_members` (`migrate_export_requests.sql` is not
  wired in).
- **Knowledge Assistant works on US and is fast;** the Supervisor with five UC functions was
  noticeably slower. The app was pointed straight at the KA by setting `chat_endpoint` to the KA's
  serving endpoint; the app's request format is accepted. `grant_app_access.py` only understands a
  Supervisor (it fails with a KA endpoint), so the KA permissions came from an earlier
  Supervisor-mode grants run. The first KA sync sat at "finalizing processing" for over 40 minutes.
- **KA citations:** each answer's message content carries `annotations` of
  `{"type": "url_citation", "url": ..., "title": <file name>}`. The URL points at the source-volume
  file `.../<catalog>/idp_mvp/idp_source/incoming/<document_id>.pdf`, then `#page=N` and the cited
  passage, URL-encoded (`%20`). Page numbers change with the passage and match the PDF. There are
  **no** `start_index`/`end_index` offsets (L7). Opening the URL sometimes shows the PDF
  at the page with the passage highlighted and sometimes downloads the file, and it needs the
  viewer's own `READ VOLUME`, so it is not reliable as the click target.
- **Folder import failed in its job** with `RuntimeError: Already running asyncio in this thread`:
  Databricks runs Python job tasks inside an event loop, and `FolderImportService.run` called
  `anyio.run`. Fixed on 11 October (runs the import in its own thread when a loop is running; test
  `test_run_works_inside_a_running_event_loop`). Needs a redeploy, then **Retry** on the failed batch.
- **The `work_dispatcher` job was deleted by hand on US (11 October)** to stop its hourly schedule
  overnight: bundle-managed jobs cannot be edited in the UI. The next deploy must recreate it:
  check that `plan` shows a create for `jobs.work_dispatcher` (not an error), then restart the app so
  `IDP_DISPATCH_JOB_ID` points at the new job, and give the app's service principal
  `CAN_MANAGE_RUN` on it (the hand-made grant was on the old job; Batch 9's grants job covers this).
  Until then automatic preparation cannot start parsing.
- **To verify in Batch 9:** whether `mode: development` pauses the dispatcher's schedule despite
  `recovery_schedule_status=UNPAUSED` (bundle docs say development mode pauses schedules).

## Progress

| Batch | Title | Sessions (est.) | Status |
|---|---|---|---|
| 1 | Branch and Genie removal | 1 | Done (user to run the Genie unbind locally) |
| 2 | Upload speed | 1 | Done (live DML result shape unconfirmed) |
| 3 | Upload robustness | 1 | Done (live check L3 open) |
| 4 | Folder import | 2 | Done in 1 session (live smoke L4 open) |
| 5 | Chat foundation on Free Edition | — | Merged into Batch 7 (code) and L5 (live) |
| 6 | US workspace deployment | 1 | Done by hand 10–11 Oct (see Facts from the US deployment); Batch 9 makes it repeatable |
| 7 | Managed chat on Free Edition (KA + Supervisor) | G7 + 2 code + live | G7, 7a, 7b done (1 Oct; chat built into the IDP App); L5 next |
| 8 | US capacity runs, access, retirement | 1 + user test time | Ready (US deployed) |
| 9 | Repeatable deployment: one settings file, one command | 2 | 9a done (11 Oct); 9b next |
| 10 | Knowledge Assistant chat with clickable page citations | 1–2 | Not started (after 9) |

Batches 1–5 need only the Free Edition workspace (and mostly none). A session is sized to about one
five-hour usage window.

## Live checks pending

Work that needs live workspace, warehouse or browser access and cannot be done from a cloud
session without Databricks credentials. Each needs the user's OK (quota) or the user's hands.
Tick and record the result here when done.

**Access from a cloud session** (set once by the user in the environment settings, then a new
session): network access must allow `dbc-97e4a372-40b1.cloud.databricks.com`; environment variables
`DATABRICKS_HOST=https://dbc-97e4a372-40b1.cloud.databricks.com` and `DATABRICKS_TOKEN=<personal
access token>` (never pasted into chat). `scripts/workspace_auth.py` uses them when both are set,
otherwise the CLI profile. The Python SDK is enough for L2 and smoke checks; the Databricks CLI
(bundle deploy/unbind) also needs `github.com` and `objects.githubusercontent.com` allowed plus
`curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh` in the
setup script. Budget: at most one warehouse start per check session; no jobs or AI functions
unless a batch lists them.

Commands: smoke (no SQL) `uv run --project backend python scripts/workspace_smoke.py --host
https://dbc-97e4a372-40b1.cloud.databricks.com --warehouse 647704f77f24020a`; L2 `uv run --project
backend python scripts/live_dml_check.py --host https://dbc-97e4a372-40b1.cloud.databricks.com
--warehouse 647704f77f24020a --catalog workspace --schema idp_mvp --yes` (4 statements).

| # | From | Needs | Check | Status |
|---|---|---|---|---|
| L1 | Batch 1 step 5 | User's CLI, profile `idp-mvp`, local `genie.generated.yml` | `databricks bundle deployment unbind project_genie -t dev -p idp-mvp` with the old `genie_*` vars, then delete the overlay. **Blocks every deploy.** | **Done 1 Oct:** unbound with the dev vars from DEPLOYMENT_NOTES; plan then showed no Genie change and 0 deletes. Overlay moved out of the repo (copy in the session scratchpad). |
| L2 | Batch 2 step 2 | Dev warehouse `647704f77f24020a`, scratch schema | One `UPDATE` and one `MERGE` on a throwaway table through the Statement Execution API; confirm each returns a one-row result whose manifest columns include `num_affected_rows` (MERGE also `num_inserted_rows`). If not, uploads stay correct but fall back to read-backs (slower). | **Done 1 Oct:** UPDATE returns `num_affected_rows`; MERGE returns `num_affected_rows, num_updated_rows, num_deleted_rows, num_inserted_rows` (values are strings; `execute_dml` casts with `int`). Fast path works. |
| L3 | Batch 3 step 7 | Browser signed in to the dev app | Let the session expire (or clear the app cookie), then trigger an API call with `redirect: "manual"`; record whether the Apps gateway answers 401, 403 or a redirect (opaque redirect in the browser). The frontend treats all three as sign-in loss. | **Done 1 Oct:** API calls with no session, a bogus bearer or a bogus app cookie all get **401** (no redirect). The earlier 503 was the app being stopped; `bundle run idp_app` restarted it. |
| L4 | Batch 4 step 7 | L1 done; user's CLI; user's OK (quota) | Deploy dev (`bundle deploy`, run bootstrap for the `idp_import` volume, deploy again), apply DEPLOYMENT_NOTES steps 6–7, copy ≤20 PDFs (include one non-PDF and one duplicate) to `idp_import/smoke/`, start **Import from folder**. Expect all registered or already registered, the non-PDF skipped, the folder emptied of PDFs. Record the job run time here. | Partly done 1 Oct: deployed (folder_importer created), bootstrap created `idp_import`, deployed again, READ VOLUME granted to the app SP. **User:** grant the app SP CAN_MANAGE_RUN on job 185475697453536 (DEPLOYMENT_NOTES step 6; the job-name lookup there is fixed for dev-mode prefixes), optionally step 7, then the smoke copy and **Import from folder**. |
| G7 | Batch 7 gate | Free Edition workspace UI, CLI | KA and Supervisor feasibility on Free Edition; answers go in Batch 7's G7 table. **Blocks 7a/7b.** | Done 1 Oct (see G7 table). Probe KA, Supervisor and `idp_source/ka_probe/` still exist: user to delete. |
| L5 | Batch 7 live | L1, G7, 7a, 7b | Provision, deploy, 10 set questions, restart and per-user history checks (see Batch 7). | Open |
| L6 | Batch 7 live | L5 | Automatic KA Sync after upload and delete. | Open |
| L7 | Batch 10 step 1 | US workspace, user's CLI | Save one KA reply about a public document (`databricks api post /serving-endpoints/<ka endpoint>/invocations`) and record each annotation's keys, especially `start_index`/`end_index`, and the exact URL shape after `.pdf`. Commit the reply under `docs/evidence/` only if the document is public. | Done 11 Oct: `type`, `url`, `title` only, no offsets; URL carries `#page=N` and the encoded passage |
| L8 | Batch 9 live | Batch 9 code | Deploy to a workspace with only the new settings file and one command, from a fresh Git folder; then a non-admin user uploads, parses and chats. | Open |

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

Status: Done 30 September 2026, except the browser check in step 7 (live check **L3**).

Notes for next batch: defaults are now `upload_claim_seconds` 300 and `max_upload_attempts` 10
(the Facts section above predates this). Retries live in `services/sql_retry.py`
(`run_with_retries`, 3 attempts, 0.25 s then 0.5 s): used by every `DatabricksBatchRepository` write
and by the registry MERGE, never by plain SELECTs or non-idempotent writes. A retried
`compare_and_set` that affects 0 rows after an uncertain failure reads back its `transition_id`.
`UploadBatchService` catches `BaseException` after the UPLOADING claim and writes FAILED
(retryable) inside a shielded cancel scope, so client disconnects are covered. Frontend: every
upload-path API call uses `redirect: "manual"`; 401, 403 or an opaque redirect pauses the batch
with `signInRequired` and the retry button becomes "Resume"; 409 `UPLOAD_BUSY` waits 5, 15, 30 s;
after the queue drains, retryable failures get one pass 30 s later (`retryingSoon`); a
`beforeunload` warning and a screen wake lock are held only while transfers run.
Known flaky (pre-existing, not changed here): `DocumentViewer.test.tsx` "renders labelled
overlays…" failed once in `make check` under load (element button not found), then passed 5 runs
alone and the next full run. Make that test's lookup robust if it recurs.

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

Status: Done 30 September 2026 in one session, except step 7 (live check **L4**; no Databricks
credentials in the cloud session, and L1 blocks deploys).

Notes for next batch: a folder import is an upload batch whose header carries
`source: "folder"` and `folder`; items carry `relative_path` and `client_file_id` =
sha256(relative path)[:40]. `services/folder_import.py` holds the sources (local
`<local_data_dir>/import_volume` in mock mode, `/Volumes/<catalog>/<schema>/<import volume>` on
Databricks), `FolderImportService` and the Job runner. The Job (`resources/import.job.yml`,
`src/import_folder.py`, job parameter `batch_id`) builds the same services through
`api/dependencies.build_*` and pushes each file through `UploadBatchService.upload` as an
`UploadFile`, so claims, dedupe, attempts and outcomes are the browser path's; concurrency is an
anyio limiter (bundle var `import_concurrency`, default 8). Import-only failure codes:
`IMPORT_FILE_MISSING` (retryable), `IMPORT_FILE_CHANGED` (size or content changed after start; not
retryable). Deviations: the App is at the 20-resource binding cap, so the import volume (READ only;
the App only lists it) and CAN_MANAGE_RUN on the import Job are **direct grants**
(DEPLOYMENT_NOTES steps 6–7), not bindings; the group grant is `READ VOLUME, WRITE VOLUME` on
`idp_import` plus USE CATALOG/SCHEMA. `app.yaml` (UI deploys) leaves folder import off, because the
import Job ID is only known after a bundle deploy. Mock mode always offers folder import
(`/upload-batches/limits` → `folder_import`). `import_volume_name` is now a bootstrap parameter, so
Batch 6's `us` target needs it (default `idp_import`).

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

Status: Merged into Batch 7 on 30 September. Steps 1–3 are built in Batch 7's code sessions
(7a, 7b); step 4 is live check L5; step 5 no longer applies if gate G7 passes.

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

Status: Done by hand 10–11 October (see Facts from the US deployment). Batch 9 replaces these steps.

Needs from the user: US workspace host and CLI profile, admin rights, all-users group name.

1. Bundle target `us` in `databricks.yml` (host, catalog **without** workspace binding, schema,
   prefix, warehouse, `idp_source`, artifacts volume, `idp_import`, app names).
2. Create catalog, schema and volumes; run the bootstrap job; apply grants (Decisions → Access).
3. Deploy the IDP app, chat app and jobs; choose feature flags with the user (automatic preparation,
   bulk extraction, bulk export).
4. Smoke test with a few files: browser upload, folder import, prepare, extract, chat link.

## Batch 7 — Managed chat on Free Edition: KA + Supervisor (gate + 2 code sessions + live)

Status: G7 run 1 October 2026; 7a and 7b unblocked. **Decision (user, 1 October):** build 7a/7b
as if the KA works; write provisioning and sync against the recorded API shapes; the KA is
exercised for real on the US workspace. Free Edition queries fail (row a), so L5/L6 KA checks
run on US; Free Edition can still run the Supervisor with the UC functions.

Replaces the earlier "Managed chat on US" batch and absorbs Batch 5. Work is split by who can do it:
code sessions need no workspace access (fakes and unit tests); the user runs the gate and the live
checks. Nothing in 7a/7b starts before G7 passes, because the provisioning code depends on the API
shapes G7 records.

### Gate G7 — feasibility on Free Edition (user, about 30 minutes, small quota)

Stop Batch 7 if (a) or (b) fails; record every answer in the table below.

a. **KA exists:** create a Knowledge Assistant in the Agents UI with source directory
   `/Volumes/workspace/idp_mvp/idp_source/ka_probe/` holding 5 dev PDFs (copy them; do not point
   the probe at `incoming/`). Ask 3 questions; note whether answers cite `<file>.pdf`.
b. **Catalog accepted:** the `workspace` catalog volume is accepted as a source (Genie's content
   search refused bound catalogs; KA may differ).
c. **Sync by API:** after adding a sixth PDF, trigger Sync from the CLI/REST, not the UI
   (`databricks api` against the endpoint the UI's network tab shows, or the SDK method). Record the
   exact method/path and request body. If only the UI can sync, 7a falls back to an admin button
   that opens the KA page plus a scheduled reminder.
d. **Deletion:** delete one probe PDF, Sync, confirm it is no longer cited.
e. **Supervisor exists:** create a Supervisor Agent with the probe KA as its only subagent. If
   unavailable, the chat app talks to the KA endpoint directly and UC functions wait for US.
f. **Endpoints and limits:** record the KA and Supervisor serving endpoint names, whether they
   count against a Free Edition endpoint limit, and the quota used for the probe (Usage page).
g. **API shapes for provisioning:** from the UI's network tab or `databricks api`, record the
   create/get/update calls and JSON for KA and Supervisor (name, sources, instructions, subagents).
   7a writes `scripts/provision_chat.py` against exactly these.

Run 2026-10-01 from the CLI (v1.14.1, `knowledge-assistants` / `supervisor-agents`, both Beta).
Probe KA `idp-ka-probe` (id `921c152f-f4b9-4ce6-b524-e369134e9958`), Supervisor
`idp-supervisor-probe` (id `0297dd39-6f8a-43e8-8e92-967a6f0683ba`); source
`/Volumes/workspace/idp_mvp/idp_source/ka_probe/` (4 dev PDFs + 2 sample invoices).

| Check | Result |
|---|---|
| a KA on Free Edition | **Creates and indexes, but queries fail.** Create accepted; KA `ACTIVE` and source `UPDATED` after ~20 min. Every query returns HTTP 500; the stream shows `Vector search failed for index 'ka_probe': Model is unavailable for clientId ai-builder-interactive. Failed check: EDC. Error Code: NO_AVAILABLE_PHAROS_DEPLOYMENTS` (retrieval model not served for this workspace, likely region/compliance). Non-stream responses hide it; use `"stream": true` to debug. |
| b `workspace` catalog accepted | Yes: files source on the `workspace` catalog volume created and indexed. |
| c Sync by API (method, path, body) | Yes (sync of 6 files took over 6 min): `POST /api/2.1/knowledge-assistants/{ka_id}/knowledge-sources:sync`, empty body (`databricks knowledge-assistants sync-knowledge-sources knowledge-assistants/{id}`). Source goes `UPDATING` then `UPDATED`. |
| d Deletion propagates | Not testable while queries fail. |
| e Supervisor on Free Edition | Yes: creates, endpoint `READY`, answers, and calls the KA tool (which fails as in a). **With a `uc_function` tool (`idp_dev_chat_document_fields`, tool body `{"tool_type":"uc_function","uc_function":{"name":"<catalog.schema.fn>"}}`) it answered "show the extracted data points for 50080tihd.pdf" correctly from the function (1 Oct).** |
| f Endpoint names, limits, quota used | KA `ka-921c152f-endpoint`, Supervisor `mas-0297dd39-endpoint` (task `agent/v1/responses`, request body `{"input":[{"role":"user","content":"..."}]}`; `messages` rejected). Endpoint names are generated, not chosen; the provisioning script must read `endpoint_name` back. Quota: check the Usage page. |
| g Create/get/update calls and JSON | KA: `create-knowledge-assistant NAME DESC --instructions` → `{id, name: "knowledge-assistants/{id}", endpoint_name, state}`; get `GET /api/2.1/knowledge-assistants/{id}`; update `update-knowledge-assistant NAME UPDATE_MASK DISPLAY_NAME DESCRIPTION`; list `list-knowledge-assistants`. Source: `create-knowledge-source knowledge-assistants/{id} --json '{"display_name","description","source_type":"files","files":{"path":"/Volumes/.../"}}'`. Supervisor: `create-supervisor-agent NAME --description --instructions` → `{supervisor_agent_id, endpoint_name}`; tool: `create-tool supervisor-agents/{id} TOOL_ID --json '{"tool_type":"knowledge_assistant","description","knowledge_assistant":{"knowledge_assistant_id"}}'` (also `uc_function`, `genie_space`, `app`, ...); list `GET /api/2.1/supervisor-agents/{id}/tools`. With `--json`, only the path arguments stay positional. |

Source files are stored as `<document_id>.pdf`, so KA citations name UUIDs; 7b rewrites them to
`idp_dev_documents.file_name`.

If (a) fails: Free Edition keeps the foundation-model chat (Batch 5 as first written, done in 7b),
and KA + Supervisor move back to the US workspace after Batch 6 using 7a's code unchanged except
for the recorded API shapes.

### 7a — Functions, provisioning and sync (code session, no workspace)

Status: Done 1 October 2026. Deviations: (1) the functions are a SQL template applied by a bootstrap
`spark_python_task` (`databricks_etl/src/create_chat_functions.py`), because function bodies cannot
use the SQL tasks' parameter markers; (2) `_chat_fields` gained `field_path`/`field_type` (legacy rows
have no `schema_path`); (3) functions are `chat_document_fields`, `chat_find_documents`,
`chat_invoices`, `chat_invoice_totals` (per seller and currency), `chat_case_documents`, created and
called on dev; (4) Sync is debounced (60 s quiet, at most every 10 min) on every registration and
deletion rather than counting batch completion; the import Job flushes before exiting; settings
`IDP_KA_SYNC_ENABLED`/`IDP_KA_ID` from bundle variables `ka_sync_enabled`/`ka_id`; (5) KA permission
levels are only CAN_MANAGE/CAN_QUERY, so the App needs CAN_MANAGE to sync. Grants are in
DEPLOYMENT_NOTES "Document chat". Not provisioned on dev (KA cannot answer there); the probe
Supervisor answered from a function tool.

1. **UC SQL functions** over the `_chat_*` views (read `create_chat_views.sql` columns first):
   invoice totals by supplier and date range, find documents by field value, fields of one
   document, documents by case. New `databricks_etl/sql/create_chat_functions.sql`
   (`CREATE OR REPLACE FUNCTION`, prefixed names, parameters typed, no dynamic SQL) as a bootstrap
   task after `create_chat_views`; validator and `test_data_foundation.py` checks. Each returns
   the extracted data points themselves (field name, value, confidence where stored) plus
   `document_id` and the original file name, so the chat answer shows the values directly.
2. **`scripts/provision_chat.py`**, idempotent: find-or-create the KA (source
   `idp_source/incoming`, instructions: cite file names, invoices only, say when unsure) and the
   Supervisor (KA + the functions; instructions on when to use each), then print endpoint names.
   Uses `scripts/workspace_auth.py`; `--dry-run` prints the plan; bundle-style names
   `<app_name>-<target>-documents-ka` / `-chat-supervisor`. Unit tests with a fake API client
   (create, re-run no-op, drifted instructions updated).
3. **KA Sync trigger** (`services/chat_sync.py`), off by default: settings
   `IDP_KA_SYNC_ENABLED`, `IDP_KA_ID`. After an upload batch finishes (last item terminal), a folder
   import run ends, or a document is deleted, request a sync: coalesced (one in flight, at most one
   per 10 minutes, a trailing request is kept), best-effort like `work_wakeup.py`. Import Job calls
   it at the end of `run()`. If G7c found no API, this becomes a "Sync documents" admin action
   that records the request instead. Tests with a fake clock and fake client.
4. Grants documented in `DEPLOYMENT_NOTES.md`: chat app principal CAN QUERY on KA and Supervisor,
   EXECUTE on the functions; IDP app principal permission to sync the KA (level from G7).

### 7b — Chat app (code session, no workspace)

Status: Done 1 October 2026 **as a page inside the IDP App**, not the template. The session's
permission check refused to vendor the third-party template twice, and the user's goal was to
finish the app, so the chat was built from our own code:
- `/chat` page (**Ask documents** in the sidebar, shown when `/api/app-config` returns
  `chat_enabled`), conversation list, composer, answers rendered by a small safe markdown renderer
  (tables, lists, bold, code; never HTML or links).
- API `GET /api/chat/conversations`, `GET /api/chat/conversations/{id}`, `POST /api/chat/messages`;
  `services/document_chat.py` calls the Supervisor endpoint (Responses API, non-streaming, 170 s
  timeout), keeps the final answer, tool names and plain-text citations, and rewrites
  `<document_id>.pdf` to the original file name from the registry.
- History per user in `<prefix>_chat_messages` (bootstrap `migrate_chat_history`; SQLite in mock
  mode); every read and write filters by the forwarded user. Concurrent questions in one
  conversation can collide on `seq`; the MERGE keeps the first.
- Setting `IDP_CHAT_ENDPOINT` from bundle variable `chat_endpoint` (blank hides the page); grants in
  DEPLOYMENT_NOTES "Document chat". No third app and no Lakebase.
- **Live on dev (1 October):** bootstrap created `idp_dev_chat_messages`; `provision_chat.py` was
  refused (**Free Edition allows one Supervisor Agent**, held by the G7 probe), so dev points
  `chat_endpoint` at the probe Supervisor `mas-0297dd39-endpoint`, given the five function tools and
  the production instructions. The App needed CAN_QUERY on the serving endpoints themselves (agent
  permissions do not propagate; failures logged as `PermissionDenied`). Questions answered through
  the UI in 22–90 s with tables and plain-text sources; history listed and reopened.
  `chat_find_documents` now also matches parent-qualified names (`payee_organization`).
- The design notes below were for the template route and are kept for reference only.

1. Add `e2e-chatbot-app-next` under `chat_app/` (pin the template commit), bundle resource
   `resources/chat.app.yml` (serving endpoint resource CAN_QUERY, Lakebase database resource),
   serving endpoint name from a bundle variable `chat_endpoint` (Supervisor, else KA, else a
   foundation-model endpoint per G7). Two of three Free Edition apps; one Lakebase project.
2. Citations are plain text, not links to the PDF viewer. Source PDFs are stored as
   `<document_id>.pdf`, so rewrite cited UUID file names to the document's original file name
   (lookup through a chat view or function). Extracted data points returned by functions render
   in the answer as a small table (document, field, value). Unit test the rewrite.
3. Set `chat_app_url` so the "Ask documents" link appears (bundle variable, per target).
4. `make check` covers the chat app's lint/typecheck/tests; its build stays out of `frontend/dist`.

### Live (user)

- **L5** (after L1, 7a, 7b): run bootstrap (functions), `provision_chat.py`, deploy both apps,
  apply grants; ask 10 set questions (5 document questions, 5 exact questions answered by
  functions); check citations show original file names and exact answers show the extracted
  values; a conversation survives an app restart; history
  lists earlier chats; a second user cannot see the first user's chats; record quota per question.
- **L6**: upload 3 PDFs and delete 1; confirm the automatic Sync fires once, the new documents are
  answerable and the deleted one is no longer cited; record sync latency.

### On the US workspace (after Batch 6)

Run `provision_chat.py` against the `us` target, deploy the chat app there, repeat L5/L6 briefly.
No new code expected.

## Batch 8 — US capacity runs, access, retirement

Status: Ready: the US workspace is deployed (Batch 6 done by hand)

1. User runs `docs/CAPACITY_TEST_PROCEDURE.md` on US: browser 100 and 300 files; folder import 100,
   500 and 1,000. Record results in a new evidence doc.
2. Grant the all-users group access; write a short user guide (browser upload versus folder import,
   chat).
3. With the user's OK: delete the Free Edition Genie space and the old `_genie_*` views; remove the
   `.gitignore` entry.

## Batch 9 — Repeatable deployment: one settings file, one command (2 sessions)

Status: 9a done 11 October on `feat/batch-9-repeatable-deploy` (no workspace calls). Notes for 9b:
the table `<prefix>_app_settings` (`name`, `value`, `updated_at`) holds `chat_endpoint` and
`ka_id`; the App reads it, and an `IDP_CHAT_ENDPOINT` override wins. `ka_id` is gone from the
variables; new `chat_mode` and `supervisor_functions_enabled`; `ka_sync_enabled` now defaults to
`"true"` (the dev target keeps `"false"` and its G7 Supervisor as the override, so `make deploy` on
dev needs no setup run; running `workspace_setup` there would create a KA on Free Edition).
`workspace_setup` runs the bootstrap as a nested job, which still grants at its end, then grants
again after provisioning; a bootstrap job parameter to skip the first grant was dropped because job
parameters are pushed down to its SQL file tasks (unverified effect). `scripts/provision_chat.py`
moved to `databricks_etl/src/provision_chat.py` (job task, no local entry point).
`docs/DEPLOYMENT_GUIDE.md` section 3 still shows the old provisioning commands until 9b step 8.
Export tables (`migrate_export_requests.sql`) are still not in the bootstrap.

Goal: deploying to any workspace means filling in one settings file and running one command (or
deploying and running one job from the workspace UI). No uv, Node or make on the deploying machine,
no hand-run grants, no endpoint or id lookups, no env files. Read **Facts from the US deployment**
first: every trap listed there must be impossible afterwards. `make deploy` (dev, with the full
checks) stays as it is.

Session 9a (no workspace):

1. **Sync excludes** in `databricks_etl/databricks.yml`: `**/.venv/**`, `**/node_modules/**`,
   `**/__pycache__/**`, `.cache/**`, `.mypy_cache/**`, `.pytest_cache/**`, `.ruff_cache/**`,
   `.local/**`, `**/.databricks/**`, so a copy without `.git` uploads nothing generated. Add
   `.databricks/` to `.gitignore`. Extend `scripts/validate_configuration.py` to require these.
2. **Complete grants.** `grant_app_access.py` grants the six tables in Facts (`SELECT, MODIFY`) and
   `CAN_MANAGE_RUN` on the dispatcher and exporter jobs (job ids passed as parameters, like the
   import job). Add a test that every name in `backend/src/idp_app/core/data_objects.py`
   `TABLE_NAMES` is covered by an app resource binding or a grant, so a new table cannot be missed
   again.
3. **Chat mode setting** `chat_mode`: `off` | `knowledge_assistant` (new default) | `supervisor`.
   - Provisioning becomes a bundle job task (the `provision_chat.py` logic run with the SDK, no
     local uv): KA always; Supervisor only in `supervisor` mode, functions optional.
   - The task writes the resolved serving endpoint and KA id to a small settings table
     `<prefix>_app_settings`, and the app reads them from there (cached a few minutes). This removes
     `chat_endpoint`/`ka_id` from deploy variables and the redeploy after provisioning.
   - Grants handle both modes: KA mode grants `CAN_QUERY` on the KA endpoint and `CAN_MANAGE` on the
     KA, with no Supervisor lookup.
   - **Endpoint override** `chat_endpoint` (optional, blank by default): when set, the app sends
     chat to exactly that serving endpoint (a Supervisor, another agent, or a specific KA) instead
     of the provisioned KA, and the grants job gives the app `CAN_QUERY` on it (plus the Supervisor
     and sub-agent permissions when it is a Supervisor). This keeps a one-setting route back to a
     multi-tool Supervisor.
   - Setup requests one KA sync right after provisioning, so documents uploaded before chat existed
     are indexed without a manual sync. If the Agent Bricks previews are off or unavailable, the
     task fails with a plain message naming the admin setting.
4. **One setup job** `workspace_setup`: bootstrap tasks, then chat provisioning (unless `off`),
   then app grants. It replaces separate `governed_data_bootstrap` and `app_access_grants` runs
   (keep both jobs callable for repairs).

Session 9b (no workspace, then the user's live check L8):

5. **Per-workspace settings:** one file per workspace, `databricks_etl/targets/<name>.yml`
   (included by glob), holding host, catalog, schema, prefix, warehouse, volumes, `chat_mode`,
   feature switches and concurrency. Use a non-development mode so names carry no `[dev user]`
   prefix and schedules follow `recovery_schedule_status` (check the Facts item on schedules). Add
   `targets/example.yml.template` with comments for each value.
6. **One deploy command** `scripts/deploy_workspace.py <target>`: Python standard library plus the
   Databricks CLI (on Windows, run it with `py`). It clears `DATABRICKS_CLUSTER_ID`, validates the
   target, deploys (tolerating the first run's expected app error), runs `workspace_setup`,
   deploys again, starts the app and prints its URL. Each step says what it is doing and stops with
   a plain-language message on failure. `make deploy-workspace TARGET=<name>` wraps it.
7. **Stop and start commands:** `scripts/deploy_workspace.py stop <target>` stops the app, pauses
   the dispatcher's schedule through the Jobs API and stops the warehouse; `start <target>`
   reverses it (the schedule follows the settings file). Pausing for the night must never need a
   redeploy or deleting a job (see Facts).
8. **Stale build guard:** `make check` fails when `frontend/dist` differs from a fresh build, so a
   CLI-only deploy can never ship an old frontend.
9. **Docs:** rewrite `docs/DEPLOYMENT_GUIDE.md` section 3 as: create the settings file, sign in,
   run one command. Move the Facts traps into Troubleshooting. Add a section for deploying from a
   workspace Git folder (web terminal or the bundle UI) and record what L8 finds about the UI
   Deploy button.

Done when L8 passes: a fresh Git folder plus a settings file deploys a working app (upload, auto
parse, chat with citations) with no other commands. On US, L8 also recreates the deleted
`work_dispatcher` job (see Facts).

## Batch 10 — Knowledge Assistant chat with clickable page citations (1–2 sessions)

Status: Not started. Best after Batch 9, so its live test uses the one-command deploy; it adds no
deployment steps either way. Citations need the KA endpoint: a Supervisor may drop the KA's
`url_citation` links (unconfirmed community report), in which case answers show plain file names.

Goal: every chat answer shows numbered citations `[1]`, `[2]` next to the text they support, plus a
numbered Sources list (`[1] amazon-q1-2025.pdf · page 9 — "...passage..."`). Clicking one opens a
**new tab** showing that document at the cited page with the passage highlighted. It works for every
app user with no volume grant, and does not use the Documents page.

1. **Citation shape** (L7 done): `type`, `url`, `title`, no text offsets. Ask the user for the
   exact characters after `.pdf` if unsure whether the passage uses `text=` or `:~:text=`; parse
   both. Build fixtures from that shape.
2. **Parse citations** in `backend/src/idp_app/services/document_chat.py` (`parse_response`): for
   each `url_citation`, keep the title, document id (the UUID file name under
   `/<source volume>/incoming/`), page (`page=N`), passage (URL-decoded) and which text part of
   the answer carried it (there are no offsets). Only URLs on the workspace host and inside the configured source volume become links;
   any other citation stays a plain label. Merge citations to the same document and page.
3. **History:** store citations as objects (`number`, `document_id`, `file_name`, `page`, `quote`,
   text part) in the existing chat history row. Rows that hold the old list of strings keep
   rendering as today.
4. **Source tab:** a standalone route `/sources/:documentId?page=N&q=<passage>` with no sidebar.
   It reuses `DocumentViewer` with a `CitationTarget` built by matching the passage against that
   page's parsed elements (`/api/documents/{id}/elements?page_id=`; whitespace- and
   case-insensitive, best overlap). It shows the file name, page and passage. Check KA's page base
   (1-based) against the viewer's `page_id` with a real document.
5. **Fallback for unparsed documents:** `GET /api/documents/{id}/source` streams the original PDF
   with `Content-Type: application/pdf`, `Content-Disposition: inline`, `nosniff` and private
   caching, through a new `DocumentStorage` read method (local and Databricks Files API). The
   source tab redirects to it with `#page=N` when the document has no page images. No new grants
   are needed: the app's binding already reads the source volume.
6. **Chat page:** numbered markers at the end of the text part (paragraph) that carried each
   citation; the passage on hover; the numbered Sources list. Links use `target="_blank"` and
   `rel="noopener noreferrer"`.
7. **Tests:** parsing (encoded passage, two citations, missing page, foreign host, path outside the
   volume, merged duplicates), legacy history rows, the PDF endpoint, passage matching, and
   rendering of markers and the list.

Live (user): three questions, one answered deep in a document; click each citation as an admin and
as a non-admin user; confirm the page and highlight. Users need no `READ VOLUME`.
