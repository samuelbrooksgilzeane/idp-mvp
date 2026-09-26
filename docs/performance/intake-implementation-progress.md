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

## Next work and boundaries

Continue B1 on this branch with durable upload batch repositories/migration and
API, then the three-transfer manager above route unmounting, replay/reselection,
per-item claims/retries and reconciliation. Keep SQLite and Databricks contracts
aligned. Preserve global hash deduplication; ask before changing cross-case
ownership semantics.

The current upload path is still sequential and is not durable across browser
refresh. Automatic preparation, manifest Jobs, durable 1,000-item extraction,
exports, viewer projections, broader navigation work and Genie remain pending.
Selection capacity alone does not raise the existing parse/extraction API limits.
Do not present this checkpoint as complete 1,000-file ingestion support.

No architecture/product decision was changed. Keep the plan's subsequent branches
ordered after reviewed/merged predecessors. The user reserves deployed testing
and load testing for their own workspace under Free Edition fair usage limits;
do not execute the plan's paid or live scale-test gates automatically.

Existing untracked `frontend/dist/` and `output/` were preserved. No remote push
or merge was performed. No clustering/index changes were made without measurements.
