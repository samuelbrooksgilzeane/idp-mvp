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
