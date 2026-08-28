# IDP MVP Project Context

Last updated: 2026-08-28

This document is a concise engineering handoff. The authoritative requirements remain unchanged under `docs/implementation/`; use the numbered commit specifications and `PROGRESS_TRACKER.md` for acceptance decisions.

## Repository state

- Local repository: `/Users/samb/Documents/coding projects/idp_databricks/idp-mvp`
- Origin: `https://github.com/samuelbrooksgilzeane/idp-mvp.git`
- Current implementation branch: `feat/04-parsing-pipeline`
- Commit 5 has not started.
- `main` contains only the ordered implementation-plan commit. Feature branches have not been merged into `main`.

## Commit sequence

| Increment | Branch | Commit | Status |
|---|---|---|---|
| Documentation plan | `main` | `40981ce` | Pushed |
| Commit 1: project foundation | `feat/01-project-foundation` | `03480cf` | Pushed |
| Commit 2: data foundation | `feat/02-data-foundation` | `e75b7ee` | Pushed |
| Commit 3: upload and registry | `feat/03-upload-and-registry` | `c9043a8` | Pushed |
| Commit 4: parsing pipeline | `feat/04-parsing-pipeline` | `9ce5f16` | Pushed |

## Implemented capabilities

### Project foundation

- FastAPI application factory and typed configuration.
- React, TypeScript, and Vite application shell.
- Vite `/api` proxy to FastAPI.
- Mock mode that requires no Databricks credentials, CLI, or runtime network access.
- `make setup`, `make dev-mock`, `make test`, and `make check` entry points.
- Asset Bundle baseline with dev and prod targets and no hardcoded workspace hostname.

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
- No parsed-page viewer, extraction schema, extraction, or validation functionality yet.

## Local verification

The following passed on 2026-08-28:

- `make test`: 38 backend tests and 4 frontend tests.
- `make check`: tests, Ruff, mypy, ESLint, TypeScript checking, frontend production build, and offline configuration/YAML validation.
- `make dev-mock`: FastAPI and Vite started together and stopped cleanly.
- `GET http://localhost:5173/api/health`: returned HTTP 200 in mock mode through the Vite proxy.
- A generated PDF uploaded through the Vite proxy and reached `UPLOADED`.
- Its parse attempt moved from `RUNNING` to `SUCCESS` with one retained page.
- Failure, retry, concurrent-state, immutable-history, raw-result, image-confinement, trigger-failure, and polling-failure paths have automated coverage.

The local servers are not intentionally left running. Start them with:

```bash
make dev-mock
```

Then use:

- UI: `http://localhost:5173`
- Health: `http://localhost:5173/api/health`

## Databricks verification gap

An official Databricks CLI v1.14.0 binary was used to load the bundle. Bundle validation reached the authentication phase and reported only that default Databricks credentials were not configured.

Without authenticated workspace access, the following have not been verified:

- Authenticated `databricks bundle validate` completion.
- Bundle deployment to the dev workspace.
- Repeated governed bootstrap and parsing-column migration runs.
- Runtime grants for the App and Job identities.
- The deployed parser Job and `ai_parse_document` execution.
- Representative invoice parsing in the dev workspace.
- Retained `VARIANT`, derived text, page count, and artifact-volume page images in Unity Catalog.
- Malformed-PDF failure behavior in the deployed environment.

Accordingly, capabilities 2, 3, and 4 remain `IN PROGRESS` in `docs/implementation/PROGRESS_TRACKER.md`. They must not be marked `COMPLETE` until their target-development-environment definitions of done are demonstrated.

## Next review boundary

1. Authenticate the Databricks CLI using the environment's normal authentication flow without storing credentials in the repository.
2. Validate and deploy the dev bundle.
3. Run the governed bootstrap twice and verify the expected Unity Catalog objects.
4. Exercise upload and parsing with representative and malformed PDFs in the dev workspace.
5. Record evidence and update capability statuses only where the definitions of done are satisfied.
6. Review the work through Commit 4 before starting `docs/implementation/06_COMMIT_PARSED_DOCUMENT_VIEWER.md`.

Do not begin Commit 5 until that review authorizes the next increment.
