# IDP MVP Project Context

Last updated: 2026-08-28

This document is a concise engineering handoff. The authoritative requirements remain unchanged under `docs/implementation/`; use the numbered commit specifications and `PROGRESS_TRACKER.md` for acceptance decisions.

## Repository state

- Local repository: `/Users/samb/Documents/coding projects/idp_databricks/idp-mvp`
- Origin: `https://github.com/samuelbrooksgilzeane/idp-mvp.git`
- Current implementation branch: `feat/05-parsed-document-viewer`
- Commit 5 is implemented and deployed for verification, but is not yet committed or tagged.
- `main` contains only the ordered implementation-plan commit. Feature branches have not been merged into `main`.

## Commit sequence

| Increment | Branch | Commit | Status |
|---|---|---|---|
| Documentation plan | `main` | `40981ce` | Pushed |
| Commit 1: project foundation | `feat/01-project-foundation` | `03480cf` | Pushed |
| Commit 2: data foundation | `feat/02-data-foundation` | `e75b7ee` | Pushed |
| Commit 3: upload and registry | `feat/03-upload-and-registry` | `c9043a8` | Pushed |
| Commit 4: parsing pipeline | `feat/04-parsing-pipeline` | `9ce5f16` | Pushed |
| Commit 5: parsed-document viewer | `feat/05-parsed-document-viewer` | — | In progress; implementation and dev deployment verified |

## Implemented capabilities

### Project foundation

- FastAPI application factory and typed configuration.
- React, TypeScript, and Vite application shell.
- Vite `/api` proxy to FastAPI.
- Mock mode that requires no Databricks credentials, CLI, or runtime network access.
- `make setup`, `make dev-mock`, `make test`, and `make check` entry points.
- Asset Bundle baseline with dev and prod targets and no hardcoded workspace hostname.
- Bundle-managed Databricks App resource with trusted Databricks-mode configuration
  and least-privilege Job, warehouse, volume, and table bindings.

### Governed data foundation

- Parameterized schema, volume, table, and view creation.
- Two governed volumes, seven Delta tables, and three latest-result/summary views.
- Distinct `idp_dev` and `idp` table prefixes.
- Non-destructive, repeatable bootstrap contract.
- Idempotent parsing-column migration for environments that previously ran Commit 2.

### Upload and registry

- Secure multipart PDF upload API and document registry.
- Extension, MIME type, PDF signature, count, and size validation.
- Streamed SHA-256 hashing and deterministic duplicate detection.
- Sanitized filenames and server-owned source paths.
- Local mock volume and SQLite registry adapters.
- Databricks Files API and SQL Statement Execution adapters.
- Document intake UI with explicit partial-batch and duplicate feedback.

### Parsing pipeline

- `POST /api/documents/{document_id}/parse`.
- `GET /api/documents/{document_id}/parse-runs`.
- `GET /api/runs/{parse_run_id}`.
- Immutable parse attempts, retries, status transitions, and polling.
- Local PyMuPDF parser retaining layout-aware raw results and page images.
- Databricks Job task pinned to `ai_parse_document` version `2.0`.
- Empty `descriptionElementTypes` and artifacts-volume `imageOutputPath`.
- Complete raw `VARIANT` persistence before derived text, page count, and errors.
- Source-path and artifact-path confinement checks.
- Source PDFs are not moved or deleted after success or failure.
- React document detail, parse/retry controls, status polling, and run history.

### Parsed-document viewer

- Authenticated page metadata, page-image streaming, and filtered element APIs.
- Latest-successful-parse selection and document/run/artifact path confinement.
- Incremental loading of the selected page image rather than returning all image bytes.
- Page navigation, zoom, reset, element-type filtering, and labelled overlays.
- Bounding boxes rescale against the rendered image after resize and zoom.
- Element inspector with type, confidence, region count, and extracted content.
- Explicit unparsed, missing-image, loading, and API-error states.
- No extraction schema, extraction, or validation functionality yet.

## Local verification

The following passed on 2026-08-28:

- `make test`: 44 backend tests and 8 frontend tests.
- `make check`: tests, Ruff, mypy, ESLint, TypeScript checking, frontend production build, and offline configuration/YAML validation.
- `make dev-mock`: FastAPI and Vite started together and stopped cleanly.
- `GET http://localhost:5173/api/health`: returned HTTP 200 in mock mode through the Vite proxy.
- A generated PDF uploaded through the Vite proxy and reached `UPLOADED`.
- Its parse attempt moved from `RUNNING` to `SUCCESS` with one retained page.
- The parsed-page viewer rendered two labelled elements; zoom, filtering, overlay selection, and inspector content were exercised in the browser.
- Failure, retry, concurrent-state, immutable-history, raw-result, image-confinement, cross-run access, missing-image, trigger-failure, polling-failure, scaling, navigation, filtering, and partial-state paths have automated coverage.

The local servers are not intentionally left running. Start them with:

```bash
make dev-mock
```

Then use:

- UI: `http://localhost:5173`
- Health: `http://localhost:5173/api/health`

## Databricks verification

Authenticated dev-workspace verification completed on 2026-08-28 against the
`workspace` catalog and serverless SQL warehouse `647704f77f24020a`:

- `databricks bundle validate -t dev` passed.
- The bundle deployed the governed bootstrap and document-parser Jobs.
- Bootstrap runs `885944253718670` and `571208438976540` both succeeded.
- `workspace.idp_mvp` contains the two configured managed volumes, seven
  `idp_dev_*` managed Delta tables, and three `idp_dev_*` views.
- The parsed-documents table contains the guarded `content_sha256`,
  `requested_by`, and `job_run_id` migration columns.
- The bundle-managed `idp-mvp-dev` Databricks App deployed and started.
- The deployed App serves the production React build at `/`.
- `/api/health` returned `status=ok`, `mode=databricks`, and confirmed every
  required runtime setting is present.
- Two representative invoice PDFs uploaded through the deployed App, were
  written beneath the governed source volume, and appeared in the SQL-backed
  document registry.
- A parse failure was surfaced in the registry and successfully retried after
  correcting the serverless environment and deterministic raw-result write.
- Parser Job run `585474568087236` succeeded with `ai_parse_document` version
  `2.0`; the invoice reached `PARSED` with a retained page count of one and no
  parse error.
- The Commit 5 application source was deployed and the `idp-mvp-dev` App restarted successfully.
- The authenticated viewer API returned one page and 11 elements spanning
  `text`, `table`, `figure`, and `footnote` types for document
  `d0ed9896-da45-560a-8ddb-5b88d20dea1e`.
- The authenticated page-image endpoint returned HTTP 200, `image/jpeg`, and
  204,525 streamed bytes without exposing its internal volume path.

Additional live hardening checks remain:

- Duplicate re-upload messaging in the deployed UI.
- Direct Catalog Explorer inspection of retained `VARIANT`, derived text, and
  artifact-volume page images.
- Malformed-PDF failure behavior in the deployed environment.

Capabilities 2, 3, and 4 are `COMPLETE`. Capability 5 is `IN PROGRESS`: its
implementation, automated checks, local visual interaction, and live backend
deployment pass, while representative-template visual acceptance, the Git
commit, and the `mvp-parsing` tag remain.

## Next review boundary

1. Open the deployed App while signed in and visually accept the overlay alignment on each representative invoice template.
2. Commit Commit 5, update its tracker SHA, and tag the verified commit `mvp-parsing` once that acceptance passes.
3. Perform the additional live hardening checks above when practical.
4. Start `docs/implementation/07_COMMIT_SCHEMA_REGISTRY_AND_VIEWER.md` only after the Parsing MVP review.
