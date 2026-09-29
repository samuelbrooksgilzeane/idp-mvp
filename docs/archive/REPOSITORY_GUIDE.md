# IDP MVP Repository Guide

This guide explains the purpose and ownership of the repository's folders. For setup and commands,
see `README.md`. For a presentation-oriented explanation of the running solution, see
`docs/SOLUTION_GUIDE.md`. For the current performance and maintainability review, see
`docs/PERFORMANCE_AND_SIMPLIFICATION_REVIEW.md`. Current implementation evidence is in
`docs/PROJECT_CONTEXT.md`; the files under `docs/implementation/` remain the authoritative
requirements.

## Repository root

The root contains project-wide configuration and developer entry points.

| Path | Purpose |
|---|---|
| `.env.example` | Placeholder-only local configuration template. Copy values into an ignored `.env`; never put credentials in this file. |
| `.gitignore` | Excludes credentials, local data, dependencies, caches, build outputs, uploads, and client documents. |
| `Makefile` | Stable commands for setup, local mock development, tests, and the complete quality gate. |
| `README.md` | Setup, configuration, API, local-development, Databricks deployment, and permission guidance. |
| `app.yaml` | Databricks App process definition. It contains no secrets. It must exist and must declare `command`: a deploy that is not driven by `databricks bundle run … idp_app` reads this file instead of the `config:` block in `databricks_etl/resources/application.app.yml`, and without it the App fails to start. Its `IDP_MODE` must stay equal to the mode that block sets. |

## `backend/`

Python 3.11+ FastAPI application, dependency definitions, and backend tests.

| Path | Purpose |
|---|---|
| `backend/pyproject.toml` | Python package metadata, pinned runtime/dev dependencies, and Ruff, mypy, and pytest configuration. |
| `backend/uv.lock` | Reproducible dependency lock managed by `uv`. |
| `backend/src/idp_app/` | Installable backend application package. |
| `backend/tests/` | Backend unit, API, configuration, and contract tests. |

### `backend/src/idp_app/`

| Path | Purpose |
|---|---|
| `main.py` | FastAPI application factory, shared error response handling, API mounting, and production frontend serving. |
| `api/` | HTTP routing, dependency construction, and public response models. It must not contain storage paths or direct data-platform logic. |
| `core/` | Trusted server configuration and governed object-name definitions. |
| `services/` | Document, registry, storage, parsing, parse-run, and Job adapter logic. |

### `backend/src/idp_app/api/`

| File | Purpose |
|---|---|
| `router.py` | Composes the API routers and exposes `/api/health`. |
| `models.py` | Typed public health, document, upload, error, and parse-run responses. |
| `dependencies.py` | Builds mock or Databricks service adapters from trusted settings. |
| `documents.py` | Upload, document-list, and document-detail endpoints. |
| `parsing.py` | Parse trigger, parse history, and run polling endpoints. |
| `viewer.py` | Page metadata, page-image streaming, and filtered element endpoints. |
| `schemas.py` | Production schema list and version-detail endpoints. |
| `extraction.py` | Extraction trigger, run history, latest and per-run result endpoints. |
| `validation.py` | Validation trigger, run history, latest, per-run and summary endpoints. |
| `batches.py` | Batch submission and batch progress. Never exposes the execution engine. |
| `results.py` | Case-filtered invoice summary and streaming two-sheet XLSX export endpoints. |

### `backend/src/idp_app/core/`

| File | Purpose |
|---|---|
| `config.py` | Pydantic settings, mock/Databricks mode selection, required configuration checks, and strict identifier validation. |
| `data_objects.py` | Trusted catalog/schema/prefix namespace construction for governed tables and views. |

### `backend/src/idp_app/services/`

| File | Purpose |
|---|---|
| `document_models.py` | Internal immutable document, upload metadata, and parse-run records. |
| `document_storage.py` | Local mock-volume and Databricks Files API storage adapters. |
| `document_registry.py` | SQLite and Databricks SQL document registries and guarded status transitions. |
| `documents.py` | Streamed PDF validation, hashing, deduplication, storage, and registration workflow. |
| `parse_runs.py` | SQLite and Databricks SQL repositories for immutable parsing attempts and latest-successful selection. |
| `parse_jobs.py` | Local parsing executor and Databricks Jobs trigger/poll adapter. |
| `parsing.py` | Parsing state machine, eligibility, retry handling, artifact-root construction, and polling orchestration. |
| `health.py` | Safe health response with configuration-presence booleans only. |
| `reporting.py` | Shared SQLite/Databricks summary queries and styled XLSX workbook generation. |

### `backend/tests/`

| File | Coverage |
|---|---|
| `test_config.py` | Mode requirements and strict identifier rejection. |
| `test_health.py` | Safe mock health response. |
| `test_data_foundation.py` | Governed names, non-destructive SQL, views, migrations, and trusted bundle parameters. |
| `test_documents_api.py` | PDF validation, uploads, deduplication, identity, storage, registry, and failure handling. |
| `test_parsing_api.py` | Parse status sequences, failure, retries, history, retained raw output, image confinement, and Job failures. |
| `test_reporting_api.py` | Summary scope and reconciliation, case filtering, public-field confinement, and workbook contents. |

## `frontend/`

React, TypeScript, and Vite user interface.

| Path | Purpose |
|---|---|
| `frontend/package.json` | Frontend scripts and pinned runtime/dev dependencies. |
| `frontend/package-lock.json` | Reproducible npm dependency lock. |
| `frontend/vite.config.ts` | Vite build settings and `/api` proxy to local FastAPI. |
| `frontend/eslint.config.js` | TypeScript and React lint configuration. |
| `frontend/tsconfig.json` | TypeScript compiler configuration. |
| `frontend/index.html` | Vite HTML entry point. |
| `frontend/src/` | React application, components, styles, and tests. |

### `frontend/src/`

| File or folder | Purpose |
|---|---|
| `main.tsx` | Browser entry point that mounts the router and application. |
| `App.tsx` | Application shell: runtime health, document loading, and route definitions. |
| `types.ts` | Shared public API types used by pages and components. |
| `pages/DocumentsPage.tsx` | Registry, upload, multi-select, and batch actions. |
| `pages/DocumentDetailPage.tsx` | One document: source viewer beside tabbed extraction, validation and history. |
| `pages/ResultsPage.tsx` | Case-filtered cross-document invoice metrics, summary table, and XLSX export. |
| `pages/ResultsPage.test.tsx` | Summary rendering plus filter/export scope parity. |
| `pages/SchemaPage.tsx` | The extraction contract, which is per use case rather than per document. |
| `App.test.tsx` | Frontend workflow tests using mocked API responses. |
| `styles.css` | Application-wide responsive visual system. |
| `components/WorkflowHeader.tsx` | Section navigation with the active route highlighted. |
| `components/UploadPanel.tsx` | PDF selection, case metadata, and upload controls. |
| `components/DocumentList.tsx` | Registry table, refresh, status, and document selection. |
| `components/DocumentViewer.tsx` | Page image, element overlays, zoom, and citation evidence highlighting. |
| `components/ExtractionPanel.tsx` | Extraction runs, provenance, field values with confidence and evidence. |
| `components/ValidationPanel.tsx` | Validation runs, outcome summary, and filterable exceptions. |
| `components/SchemaViewer.tsx` | Read-only registered extraction contract. |
| `components/BatchActions.tsx` | Batch parse and extract for the current selection, with progress. |
| `components/viewerGeometry.ts` | Bounding-box scaling shared by element and citation overlays. |
| `test/setup.ts` | Vitest and DOM test setup. |

Generated frontend dependencies and production output live in `frontend/node_modules/` and `frontend/dist/`. Both are ignored and must not be committed.

## `databricks_etl/`

Databricks Asset Bundle resources and governed processing tasks. Browser input must never determine identifiers, table names, source paths, or artifact roots used here.

| Path | Purpose |
|---|---|
| `databricks.yml` | Bundle name, trusted variables, resource includes, and dev/prod targets. |
| `resources/` | Declarative Databricks Job resources. |
| `sql/` | Reviewed, parameterized governed-object creation and schema migrations. |
| `src/` | Databricks runtime processing code. |

### `databricks_etl/resources/`

| File | Purpose |
|---|---|
| `bootstrap.job.yml` | Runs governed object creation followed by the guarded parsing-column migration. |
| `parsing.job.yml` | Serverless document-parser Job. Runs its per-document task through a `for_each` at the trusted `batch_concurrency`. |
| `extraction.job.yml` | Serverless document-extractor Job, batched the same way. |
| `application.app.yml` | Creates the Databricks App and binds trusted runtime configuration plus least-privilege Job, warehouse, volume, and table resources. |

### `databricks_etl/sql/`

| File | Purpose |
|---|---|
| `create_objects.sql` | Creates the project schema, volumes, nine Delta tables, and four views using trusted parameters. |
| `migrate_parsing.sql` | Idempotently adds Commit 4 audit and polling columns to an existing parse-run table. |
| `migrate_extraction.sql` | Idempotently adds the Commit 7 extraction-run polling column. |

### `databricks_etl/src/`

| File | Purpose |
|---|---|
| `parse_document.py` | Validates Job parameters, calls `ai_parse_document` version `2.0`, retains raw `VARIANT`, derives parse fields, and updates terminal states. |
| `extract_document.py` | Re-verifies the registered schema hash, selects the latest successful parse, calls `ai_extract` `2.1`, retains the raw result first, then flattens and projects typed candidates. |
| `register_schemas.py` | Independently re-validates and idempotently registers a source-controlled schema manifest. |

## `docs/`

Project documentation and engineering handoff material.

| Path | Purpose |
|---|---|
| `PROJECT_CONTEXT.md` | Current branches, commits, implemented capabilities, verification evidence, blockers, and next review boundary. |
| `REPOSITORY_GUIDE.md` | This folder and ownership guide. |
| `implementation/` | Complete authoritative implementation pack. Do not merge, rewrite, or consolidate these specification files. |

### `docs/implementation/`

- `00_START_HERE.md` defines how to use the ordered pack.
- `01_TECHNICAL_CONTRACTS.md` defines cross-increment technical contracts.
- `02_...` through `12_...` define each ordered implementation increment.
- `13_PLAN_BATCH_UI_EXPORT_ASSISTANT.md` is the current working plan, an agreed insertion
  covering batch processing, the multi-page UI, export, and the conversational layer.
- `PROGRESS_TRACKER.md` is the authoritative concise capability-status record.
- `FIRST_CODEX_SESSION.md` retains the original first-session execution instructions.

Only update `PROGRESS_TRACKER.md` according to its status rules. A local implementation is not `COMPLETE` when its definition of done requires evidence from the target Databricks development environment.

## `fixtures/`

Reserved for safe, synthetic, non-client test and demonstration inputs. It is currently empty. Do not place credentials, uploads, production exports, or client documents here.

## `scripts/`

Repository-level development and validation utilities.

| File | Purpose |
|---|---|
| `dev_mock.py` | Starts FastAPI and Vite together and shuts both down from one interrupt. |
| `validate_configuration.py` | Credential-free validation of app configuration, bundle variables/resources, SQL safety, and parser-task contracts. |

## Ignored local directories

These directories may appear after setup, tests, builds, or local development and are not source code:

| Path | Contents |
|---|---|
| `.local/` | Mock source volume, page-image artifacts, and SQLite registry. |
| `.cache/` | Local `uv` cache. |
| `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/` | Tool caches. |
| `backend/.venv/` | Python virtual environment managed by `uv`. |
| `frontend/node_modules/` | Installed npm dependencies. |
| `frontend/dist/` | Frontend production build. |

Do not use ignored local data as deployment evidence or commit it to Git.

## Runtime flow

1. Vite serves the React UI and proxies `/api` to FastAPI during mock development.
2. FastAPI validates public input and authenticated identity, then calls service-layer workflows.
3. Mock mode uses local files, SQLite, and PyMuPDF.
4. Databricks mode uses unified authentication, the Files API, SQL Statement Execution, Unity Catalog objects, and the configured parsing Job.
5. Public API models omit source paths, artifact paths, raw parser output, credentials, and internal connection details.
