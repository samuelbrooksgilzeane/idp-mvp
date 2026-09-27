# Bulk intake implementation progress

Date: 2026-09-26. Branch: `feat/intake-capacity`.
Plan: `idp-bulk-intake-and-auto-parse-plan.md`, supplied by the user.
Baseline verified: `59bf4f19cad614a34a5faa07e55a746afa7d2c0f` (`perf/detail-prefetch`).

## Completed checkpoints

- `4ca26c7`: bounded document keyset pagination for SQLite and Databricks. New
  `GET /api/documents/page` returns `items` and `next_cursor`; default 50, maximum
  100. Case/status/literal filename filters are applied on the server. Cursors bind
  filters and `(uploaded_at, document_id)` ordering. Malformed/mismatched cursors
  return a resettable 422 error. The legacy array endpoint remains unchanged for
  compatibility and is not used by the updated browser list.
- `5041aa8`: browser uses server pages, URL filters/cursors, debounced search,
  abort/generation guards, bounded previous-cursor history, and explicit selection
  across pages/filters. Selection is stored in session storage by application name
  (one project app per deployment), capped at 1,000. Page selection is labelled
  explicitly; clear selection remains available. On a hard refresh at an arbitrary
  cursor, First page is available even without an in-memory previous cursor.

## Local verification

- 19 passing backend tests: `test_document_pagination.py`, `test_documents_api.py`.
- Synthetic 1,000-row SQLite metadata fixture verifies all rows can be visited,
  including identical timestamps. No PDFs uploaded and no inference used for it.
- Mocked Databricks test verifies one bounded, parameterized query per page; this
  does not prove deployed SQL behavior or timing.
- 16 passing frontend tests: App, DocumentsPage, useDocumentPage. Covers stale
  responses despite ignored abort, cursor/filter reset, selection/remount, and
  existing upload behavior.
- Ruff, focused mypy, frontend typecheck and ESLint passed.
- Vite production build passed, written to `/tmp/idp-intake-frontend-build` to leave
  the user's existing untracked `frontend/dist/` untouched.
- No migration applied. No deployment, workspace query, Job, AI function, gateway
  upload, resource provisioning, or live capacity test was run.

## Durable intake checkpoints

- `2850a81`: SQLite and Databricks upload-batch repositories, owner-authorized
  manifest/status APIs, deterministic replay identities, per-item optimistic claims,
  bounded attempts, and byte-verified recovery of stored PDFs after registry failure.
  Added `migrate_work_batches.sql` and its bootstrap task; migration has NOT run.
- `4d5937c`: individual outcome lookup, durable gateway-failure reporting, configured
  upload limits, and read-only source reconciliation. A committed registry row can
  repair an incomplete upload outcome without storing the PDF again.
- `a16f6e1`: app-owned three-transfer pool, bounded transient retries, lookup before
  retransmission, pause, per-file progress, original-file reselection after refresh,
  and paginated progress rendering. Completed files release their browser File objects.
  Registry refresh happens once after a drain rather than after each uploaded file.

Local validation for these checkpoints: 38 backend tests (upload batches, documents
API, configuration), 19 frontend tests (transfer manager, list, app and cursor hook),
Ruff, focused mypy, TypeScript, ESLint and production build passed. Upload tests use
small local PDF bytes or mocked requests. The 1,000-member test writes only a local
manifest. No workspace upload, AI call, SQL query or Job was executed.

The focused `validate_data_bootstrap()` configuration check passed. Running the full
`scripts/validate_configuration.py` still fails its pre-existing requirement for
`sync.include: ["../frontend/dist/**"]`; the baseline bundle omits that setting.
This failure predates the intake changes and was not silently treated as a pass.

## Operator handoff

Apply the additive `migrate_work_batches.sql` migration through the existing bootstrap
before deploying this frontend/API pair. It creates prefixed `upload_batches` and
`upload_items` tables with Serializable Delta isolation. No schedule, inference,
index, endpoint or other compute resource was added. Settings:

- `IDP_MAX_UPLOAD_BATCH_FILES`: 1,000 by default (public API maximum 1,000).
- `IDP_MAX_UPLOAD_ATTEMPTS`: five server claims per item by default.
- `IDP_UPLOAD_CLAIM_SECONDS`: 1,800 by default. Unfinished claims can be reclaimed
  after expiry; a known committed document is resolved before claiming/reuploading.
- Existing `IDP_MAX_UPLOAD_BYTES` remains authoritative and is displayed by the UI.

Global content deduplication remains unchanged. A duplicate batch member references
its existing document; it does not move that document into the new batch's case.
Original upload names are retained in batch metadata. Batch endpoints are restricted
to the authenticated requester within the deployment's project tables.

Source reconciliation is an explicit, read-only operator command:
`backend/.venv/bin/python scripts/reconcile_upload_sources.py`.
In Databricks mode it refuses to call the workspace unless
`--allow-workspace-reads` is supplied. It lists source metadata and pages registry
records; it reports missing sources and unregistered objects without deleting or
registering either. An in-flight upload can appear as an ambiguous orphan. The
command was not run against Databricks during implementation.

Browser refresh restores batch metadata from local storage in the project app's
origin, then checks the authorized API. It cannot restore File objects. Reselection
matches original filename, size and modification time and rejects ambiguous matches.
The server binds SHA-256 on the first attempt and rejects changed retry content.
“New upload batch” explicitly clears the browser's current batch pointer; durable
server outcomes remain. Closing the tab interrupts unfinished transfers; navigating
inside the app does not. This is whole-request retry, not resumable byte transfer.

## Next work and boundaries

B1's implementation is in place; deployed gateway boundaries, Delta concurrent-write
behavior and workspace recovery remain unverified and reserved for the user's own
fair-usage-compliant testing. Local SQLite/mocked SDK tests do not prove those gates.
No 1,000-file throughput or capacity claim is made.

Next is B2 automatic preparation: durable parse intents, registry reconciliation,
serialized bounded dispatch, manifests, idempotent Job submission and queue/recovery
UX. Automatic parsing is NOT enabled by this checkpoint. Durable 1,000-item
extraction, exports, viewer projections, broader navigation work and Genie also
remain pending. Selection/upload capacity alone does not raise the existing
parse/extraction API limits.

Keep later branches ordered after reviewed/merged predecessors. The user reserves
workspace testing and load testing for their own Free Edition environment; do not
execute the plan's live or paid scale-test gates automatically.

Existing untracked `frontend/dist/` and `output/` were preserved. No migration,
deployment, remote push or merge was performed. No clustering/index changes were
made without measurements.

## Automatic preparation checkpoint — 26 September 2026

Branch `feat/automatic-preparation` is stacked on `feat/intake-capacity`; neither
branch has been pushed or merged. Implementation checkpoints:

- `194c0ac`: durable parse intents, compare-and-swap claims, serialized dispatch,
  idempotent submission, bounded retry and registration reconciliation.
- `0cb29f5`: manifest loader, parser/dispatcher Job wiring, retained-result recovery,
  combined inference concurrency guard and immutable extraction parse validation.
- `020b8e8`: waiting/processing/readiness labels, background page refresh and advanced
  manual preparation controls. Queued requests no longer appear ineligible.

Automatic preparation is disabled by default. The new recovery schedule is paused.
No migration, deployment, grant, workspace SQL request, Job execution, PDF upload or
AI inference was performed. Local tests used synthetic metadata and mocked Jobs.

Before enabling in the workspace:

1. Apply the expanded additive `databricks_etl/sql/migrate_work_batches.sql`, including
   `work_items` and `work_dispatches` as well as upload tables. An earlier execution
   of the upload-only migration does not create these additional tables.
2. Review narrow table privileges for the app/Job identities and grant the app
   permission to run the dispatcher Job. The existing app resource bindings already
   use the repository's 20-resource budget; these additions do not add bindings or
   automatically grant broad schema access. The dispatcher ID environment value
   alone does not grant permission.
3. Verify bundle/runtime compatibility and drain any legacy parsing runs before
   enabling the new queue. The pre-existing missing frontend `sync.include` validator
   issue remains unresolved and must be checked before deployment.
4. Configure `auto_prepare_enabled=true` and unpause recovery deliberately after
   user-controlled smoke verification. `IDP_DISPATCH_JOB_ID` references the deployed
   dispatcher. Default parser/extractor concurrency is one each, combined budget two;
   dispatches contain at most 100 IDs. These are ceilings, not measured Free Edition
   capacity or throughput guarantees.

The dispatcher wakes on registration (coalesced), with scheduled reconciliation as
fallback. It persists submission identity before calling Jobs, polls active runs,
and does not reclaim tasks merely because their lease expired while a Job is still
active. Each retry gets an immutable parse-run ID. Retained successful output is
recovered without inference; an interrupted inference with no persisted result can
still be repeated, so this is not an exactly-once billing guarantee. The finite
coordinator exits after idle grace or its runtime bound.

Serverless environments use version 3, consistent with the current
[ai_parse_document requirements](https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_parse_document).
The notebook loader emits bounded ID-only task values, keeping full metadata in the
repository. Legacy input mode is retained but cannot be combined with manifest mode.

Validation: 41 focused backend tests passed (queue/recovery, runtime guards, parsing,
batches and extraction); 16 focused frontend tests passed. Ruff on changed runtime
files, TypeScript, ESLint, frontend production build, Python syntax and six focused
configuration validators passed. The pinned-parse regression accepts an older
successful parse after a newer success exists and rejects source/document/status
mismatches. Full bundle validation and deployed identity/Delta/Job behavior remain
unverified. Existing untracked `frontend/dist/` and `output/` remain untouched.

Next implementation package is durable bulk extraction: persisted requests and
progress, bounded metadata resolution, extractor manifests and explicit retries.
Exports, navigation/viewer optimizations and Genie remain subsequent packages.

## Bulk extraction foundations — 26 September 2026

Branch `feat/bulk-extraction` is stacked on `feat/automatic-preparation`; no branch
was merged or pushed. Two implementation checkpoints:

- `13d47d0`: existing extraction submission now resolves the template once, and reads
  documents/successful parse references in groups of at most 100. References exclude
  retained text and parsed element payloads. Both SQLite and Databricks implementations
  use bound parameters and deterministic latest-success ordering. Source-hash mismatches
  are rejected before extraction. Local mock execution loads only its own pinned parse
  when the task starts.
- `ba8cb92`: durable extraction-request repository and service, with deterministic
  project/requester/client-request identity, replay conflict detection, owner checks,
  immutable template hash and at most 100 members resolved per validation checkpoint.
  Compare-and-swap protects saved inputs from overlapping recovery attempts. Restarting
  does not replace resolved parse IDs. Invalid members retain explicit failures.

The second checkpoint is infrastructure, not an enabled end-to-end extraction queue.
It intentionally has no public submission route until the dispatcher consumes READY
requests. `READY` means validation finished, including any rejected members; it does
not mean extraction succeeded. The existing extraction API still has its existing
200-selection limit and direct Job submission. Do not advertise 1,000-item execution
based on these metadata tests. Next work must connect request validation to work-item
claims and extractor manifests, then add table-backed paginated progress/retry APIs
and the browser's persistent batch pointer.

The additive migration now also creates the prefixed `work_batches` table. It remains
unapplied. Include its narrow app/Job grants in the later deployment review. Request
headers store bounded selections and resolved metadata only, never PDF bytes or full
parse bodies. Document readiness is evaluated when each asynchronous validation step
runs; once saved, that member's parse identity remains pinned.

Verification: 53 focused backend tests passed, including 1,000 synthetic metadata
members resolved with 20 bounded reads, request replay/conflicts, restart, competing
CAS updates, template changes and source-hash mismatch. Ruff, focused mypy and bootstrap
configuration validation passed. All work was local: no Databricks calls, PDF load test,
AI calls, migration, deployment or workspace capacity claim. The previously documented
full-bundle validator issue and workspace verification gates are unchanged. Existing
untracked `frontend/dist/` and `output/` remain untouched.

## Bulk extraction integration — 26 September 2026

The durable extraction path is now wired end to end in source on `feat/bulk-extraction`.
It supersedes the infrastructure-only limitation in the previous checkpoint.

- `f1ae574`: shared stage-isolated dispatcher, extraction claim/worker adapter, retained
  result projection recovery, bounded retries, owner-scoped request/status/member/retry
  routes, and explicit local mock drain command.
- `ae71dca`: extractor manifest loader and ID-only task, coordinator wiring for both
  stages, default-off activation settings and focused configuration checks.
- The following UI checkpoint adds a persistent batch pointer and pending submission
  identity, 50-member pages, failed-member retry, hidden-tab pause and terminal stop.
  Legacy batch polling also backs off to five seconds and cleans up on navigation.

### Behavior and recovery

`POST /api/extraction-batches` accepts at most 1,000 UUID selections, persists a request
identity and immutable template version/hash, and returns 202 before document validation.
The coordinator resolves at most 100 documents per step, then publishes bounded work
items. All member inputs are resolved before that request becomes dispatchable. It runs
one active dispatch per stage, with no duplicate document within an extraction dispatch.
The API does not execute inference or poll Jobs on progress reads.

The loader checks work kind, manifest membership and task-value size. The worker consumes
one live claim and verifies document/hash, pinned successful parse, requester, template
version/hash and reconstructed template content. A later parse does not replace the
pinned input. Projection uses retained output; incomplete projection rows for that
unfinished run are rebuilt before terminal success. Successful immutable runs are not
cleared. The Job identity needs narrowly scoped MODIFY rights on its projection tables
for this recovery, in addition to existing read/write requirements.

Task failures remain nonterminal in work progress until the central reconciler decides
whether to retry. Infrastructure failures get at most three attempts with backoff and
new immutable run IDs; running Jobs are never reclaimed solely on lease expiry. A raw
result saved before a crash is projected without repeating inference. As before, an
inference response lost before durable retention can still lead to another call.

Explicit retry creates an idempotent child request containing only failed members.
Members with valid saved inputs retain their original parse and template pins; failures
that never resolved a parse undergo validation again. Successful members and previous
attempts remain intact. A deliberate new extraction selection resolves current inputs.

The browser stores its current batch ID and any unconfirmed submission identity. Refresh
restores owner-authorized status, not automatic resubmission. “Retry submission” replays
the saved identity after an ambiguous response. Member status pages are bounded to 50;
polling pauses in hidden tabs and stops at terminal state. Dismissing progress does not
cancel work. Batch history remains durable even when the browser pointer is replaced.

### Activation and deferred checks

`IDP_BULK_EXTRACTION_ENABLED` / bundle `bulk_extraction_enabled` defaults to false.
It is independent of automatic preparation. When enabled, the Documents page routes
bulk selections through the durable API. Existing legacy APIs remain compatible and
retain their 200-document limit; they are not the 1,000-member path. Drain legacy runs
before enabling this coordinator. Legacy and manifest execution share each stage's
max-one Job and concurrency ceiling.

Apply the latest additive work-batch migration (including `work_batches`), review table
and dispatcher grants, verify the bundle/runtime in the workspace, then deliberately
enable processing and unpause hourly recovery. No grants, migrations, schedules or
feature flags were activated during implementation. No extra app resource binding was
added. The known full-bundle `sync.include` validator failure remains deferred.

For an explicitly enabled local mock app, run
`backend/.venv/bin/python scripts/drain_local_extractions.py` after submitting work.
It drains saved requests for up to one hour, uses one mock task at a time, and refuses
Databricks mode. Local execution is not launched by GET status requests. This command
was not run against an existing user dataset during implementation.

Validation: 79 focused backend tests and 17 frontend tests passed, including bounded
metadata-only 1,000-member validation, submission ambiguity, live-Job lease safety,
retained-result recovery, retry budgets, failed-only retry, owner checks, browser
refresh, hidden polling and terminal stop. Focused Ruff/mypy, TypeScript, ESLint, six
configuration validators, runtime syntax and production build passed. These results
are local/mocked evidence, not proof of Delta races, gateway behavior, deployed identity
or AI throughput. No workspace calls or AI inference ran. Existing untracked
`frontend/dist/` and `output/` were preserved; no branch was merged or pushed.

Next work package: durable export requests/artifacts and bounded workbook generation.
Navigation/viewer improvements, Genie and user-operated release measurements remain.

## Stage 4 — durable export checkpoint

Branch `feat/export-capacity` adds a shared disk-spooled writer for the existing small export
route and the new durable request worker. Headers are discovered before rows are written;
workbooks use write-only sheets, explicit text cells, collision-safe names, relational keys,
row splitting and an export manifest. CSV and mixed-schema archives copy files/entries from disk.
Source reads are paged at 25 retained results. The temporary spool has a configurable byte budget.

New owner-scoped request/status/download routes persist pinned selections and idempotency keys;
separate export_members are repaired from the immutable request after interrupted initialization.
The browser retains ambiguous submissions for explicit replay and downloads artifacts through
normal links. The dedicated export Job queues requests with one concurrent worker and one retry.
Interrupted RUNNING exports regenerate from pinned inputs; artifacts publish before success metadata.
Expiry is enforced on every status/download, with worker-side deletion when an expired request is run.
There is no scheduled artifact cleanup: an operator must remove expired retained files separately.

Activation remains deferred: apply `migrate_export_requests.sql`, grant the existing app identity
narrow table/artifact access and CAN_MANAGE_RUN on the export Job, then enable
`bulk_export_enabled`. No extra app resource binding was added. No migration or Job was executed.
Local verification: 22 backend tests and 8 frontend tests passed, including a synthetic 1,000-member
restart/paged-read test (40 reads), writer limits, replay and authorization. TypeScript and ESLint
passed. Workspace downloads, runtime packaging, memory/CPU benchmarking and the 100,000-child-row
capacity evidence remain release verification work; no uploaded documents or AI functions were used.

## Stages 4–5 — navigation/viewer completion checkpoint

The stage-4 branch ends at `79a1467`, stacked on bulk extraction. Stage 5 is on
`perf/navigation-viewer`, stacked on stage 4, with these reviewable checkpoints:

- `b9a1ba3`: immutable viewer URLs, page projections, bounded retained-parse fallback and backfill.
- `29e6788`: shared cursor pages, URL filters, scoped selection, bounded review cache, review navigation.
- `c187687`: export replay/confirmation and artifact recovery hardening found during integration.
- `20a6f18`: authenticated viewer access, strict types and cache invalidation after processing.

Results now renders one server page of 50, rather than appending 50-row responses and slicing them
into local pages of ten. Search is debounced; case/template/status/latest filters and cursor live
in the URL. Selection survives page/filter changes. Previous/next review preserves that ordered
scope, including across cursor boundaries, and never substitutes latest for a historical run.
Deep links do not invent neighbors. Back links restore list context and saved scroll. A refreshed
cursor can reconstruct its page; if previous-page history is unavailable, Reset list remains available.
Facets come from the schema and case registries. Shared page requests reject stale responses;
review-prefetch transports survive individual consumer cancellation. Reviews are limited to 20
entries/8 MB with a 60-second TTL; latest-document lookup TTL is 10 seconds. Scope changes clear
these caches. Speculation is capped at two requests and suppressed when hidden/on constrained
connections. Heavy detail/schema routes are lazy; nested result arrays render 50 children at a time.

`/documents/{id}/viewer` returns the resolved successful parse ID and pinned image URLs. Element
requests use that ID. Historical extraction evidence now carries its parse provenance into the
viewer. Missing overlays do not hide loaded images; obsolete image callbacks are ignored. Only
the current page is rendered, with a three-page/2 MB element cache and next-page prefetch after
readiness. Original image dimensions still drive coordinate transforms.

Projection manifest + page-element tables publish readiness only after all page writes succeed.
Multi-page elements retain their identities with page-specific boxes. Successful parses can build
projections without more inference; projection failure is logged for explicit rebuild, never reparsed.
Old runs use a read-only, single-pass fallback map (four runs/16 MB serialized input, 60-second TTL).
`backfill_viewer_projection.py` accepts at most 25 explicit successful parse IDs per invocation.
No backfill has been run. `viewer_projection_enabled` defaults false until its migration and grants
are verified. The legacy pages endpoint remains available but now emits pinned image URLs too.

Additional export hardening: cross-page historical duplicates require server-confirmed inclusion;
replaying a batch-based request retains the original completed-only snapshot even if the batch has
since progressed. Export artifact replay checks checksum/size; missing or corrupt output rebuilds
from the same pins. Excel XML spools live inside the request temporary directory and participate
in cleanup/disk accounting. The writer uses a small adapter for pinned openpyxl 3.1.5 internals,
covered by writer tests. `scripts/run_local_export.py` is an explicit one-request mock-only driver.
No worker was run against the user's stored documents.

Local verification: all 73 frontend tests passed, TypeScript/ESLint and the production build passed,
and strict mypy passed for nine changed backend modules. Six focused configuration checks, the
new export Job's one-worker/one-retry settings, and runtime syntax checks passed. Backend regression
results are recorded in the final checkpoint below. Existing PyMuPDF deprecation warnings and the
jsdom normal-download navigation warning are non-failing test-environment messages.

Outstanding activation/release checks remain explicit: apply export/viewer migrations and narrow
permissions, verify packaging/Jobs/download behavior in the workspace, and collect workload-specific
memory/disk/warm-navigation measurements. The 100,000-child-row benchmark and live load testing remain
user-owned release evidence. Expired downloads are denied immediately; bulk storage cleanup is an
operator task (no scheduled cleanup workload was added). The previously documented bundle-validator
sync.include issue is still deferred. No Databricks calls, AI calls, deployment, push or merge occurred.
Stage 6 (Genie) was not started in this request.

Final local backend checkpoint: **109 tests passed** across export, viewer, intake, preparation,
bulk extraction, configuration and persistence suites. The tracked working tree is clean after
committing this handoff; pre-existing untracked `frontend/dist/` and `output/` were preserved.

## Stage 6 — structured Genie implementation (2026-09-27)

Added four curated views, a versioned Genie definition and explicit management-API provisioning.
Views follow generic column migrations; provisioning defaults to offline rendering. Retained
workspace/warehouse state, exclusive locking, durable create intent and reviewed remote hashes/ETags
protect against duplicate spaces and overwriting SQL-team edits. No deployment engine change.
The `/ask-genie` route provides coverage text, user-authenticated iframe and permanent external
fallback. Public configuration includes no credentials; origin/space validation and CSP restrict
embedding. All settings default disabled. See `docs/GENIE_SETUP.md` for activation and limitations.

Verified locally: 23 focused backend tests, all 77 frontend tests, strict mypy on the three changed
backend modules, frontend TypeScript/ESLint, changed Python lint and production build. Genie YAML
wiring and standalone App settings pass. Full configuration validation still fails on the existing
missing `sync.include` frontend packaging contract; deployment remains gated on its resolution.
Curated SQL, actual Genie definition acceptance, user grants and embedding remain workspace checks.
No migration, space creation, SQL query, upload, indexing or AI inference was run for this stage.
Earlier implementation branches were pushed to origin in this session; stage 6 is being pushed
at its logical checkpoints. Code completion does not imply workspace activation.

## Stage 7 — initial release tooling and authenticated smoke

Added a three-read, fail-fast metadata smoke script and release-evidence ledger. Two local
smoke-tool tests pass. After user-requested OAuth refresh, all three workspace metadata reads
passed: warehouse STOPPED, first page lists two Genie spaces and one App. Zero SQL statements,
Job submissions or AI requests. This confirms authentication/resource visibility only, not Genie
volume preview availability, view SQL acceptance or deployed feature behavior. Stage 6 and initial
stage 7 checkpoints are pushed; scale measurements and workspace activation remain outstanding.

## Review app deployment — 2026-09-27

User authorized deployment for feedback. Fixed the missing frontend sync include and added the
Genie view task to the bootstrap validator's expected order; full configuration validation passes.
Production frontend build passes. Packaging commit: `2b74914` on `fix/app-review-deployment`.
Uploaded tracked backend/schema sources, standalone app.yaml and the freshly built frontend to
`/Workspace/Users/luke7777.lb@gmail.com/idp-review-deployment`, excluding local data and credentials.
Started the existing stopped app once. Databricks first restored the prior deployment, then accepted
review snapshot `01f1ba8acdb11677aef66d60f18e946a`, which reports SUCCEEDED.

App URL: https://idp-mvp-dev-7474660341420973.aws.databricksapps.com
Authenticated checks: `/api/health` 200 in Databricks mode, `/api/app-config` 200 with Genie disabled,
`/ask-genie` 200 with the current frontend asset hash. No document SQL, processing jobs or AI requests
were issued. This is an app-only deployment: migrations, new Job definitions and feature activation
remain pending. Automatic preparation, bulk extraction/export, viewer projections and Genie remain
off. Bulk intake still requires its prepared table migration/grants before upload testing.
