# IDP MVP Project Context

Last updated: 2026-08-29

This document is a concise engineering handoff. The authoritative requirements remain unchanged under `docs/implementation/`; use the numbered commit specifications and `PROGRESS_TRACKER.md` for acceptance decisions.

## Repository state

- Local repository: `/Users/samb/Documents/coding projects/idp_databricks/idp-mvp`
- Origin: `https://github.com/samuelbrooksgilzeane/idp-mvp.git`
- Current implementation branch: `feat/07-extraction-pipeline`
- The Parsing MVP is accepted and tagged `mvp-parsing`.
- Commit 7 is implemented, deployed, verified live end to end, and committed.
- `main` contains only the ordered implementation-plan commit. Feature branches have not been merged into `main`.

## Commit sequence

| Increment | Branch | Commit | Status |
|---|---|---|---|
| Documentation plan | `main` | `40981ce` | Pushed |
| Commit 1: project foundation | `feat/01-project-foundation` | `03480cf` | Pushed |
| Commit 2: data foundation | `feat/02-data-foundation` | `e75b7ee` | Pushed |
| Commit 3: upload and registry | `feat/03-upload-and-registry` | `c9043a8` | Pushed |
| Commit 4: parsing pipeline | `feat/04-parsing-pipeline` | `9ce5f16` | Pushed |
| Commit 5: parsed-document viewer | `feat/05-parsed-document-viewer` | `91e111c` | Pushed and accepted |
| Commit 6: schema registry and viewer | `feat/06-schema-registry-viewer` | `727041c` | Implemented and deployed |
| Commit 7: extraction pipeline | `feat/07-extraction-pipeline` | `72e0db3` | Implemented, deployed, and verified live |

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

### Schema registry and viewer

- Source-controlled `schemas/invoice_v1.json` with eight typed invoice fields.
- Separate extraction schema, field policies, deterministic document rules, and instructions.
- Typed manifest validation and canonical SHA-256 hashing.
- Immutable `schema_id + schema_version` registration with conflict rejection.
- Idempotent Databricks bootstrap task and local SQLite registration.
- Production/use-case schema list and version-detail APIs.
- Browser responses expose safe field and policy metadata, not raw schema JSON.
- Read-only selector, provenance, field-policy table, and calibration notice in the document UI.
- No `ai_extract`, extracted values, correction, or validation functionality yet.

### Extraction pipeline

- `POST /api/documents/{document_id}/extract` with a body restricted to governed schema identity.
- `GET /api/documents/{document_id}/extraction-runs` and
  `GET /api/documents/{document_id}/extractions/latest`.
- Preconditions: a successful parse, a `PRODUCTION` schema version, and a schema/document
  use-case match; the browser supplies only `schema_id` and `schema_version`.
- Trusted Job parameters only: `document_id`, schema identity, and authenticated requester.
- Parameterised Databricks extraction Job that independently reloads and hash-verifies the exact
  schema row and deterministically selects the latest successful parse before calling `ai_extract`.
- `ai_extract` version `2.1` in precision mode with citations and confidence scores enabled,
  passing only `ai_extract_schema_json` and the versioned server-side instructions.
- Complete raw `ai_result` persisted before any flattening; a returned error fails the run
  without logging document text.
- Generic scalar flattening driven by the registered schema, per-field confidence parsing and
  range checking, and citation-ID resolution to page and bounding-box evidence (including
  missing-citation handling).
- Explicit typed projection: `DECIMAL(18,2)` amounts and unambiguous invoice dates, with the
  original extracted value preserved alongside the typed value.
- Typed `invoice_v1` candidate projection; candidate data is explicitly not approved data.
- Immutable retries: each attempt is a new run; history is retained and the latest successful
  run is selected deterministically.
- No evidence interaction, validation, editing, approval, or export functionality yet.

## Local verification

The following passed on 2026-08-29:

- `make test`: 55 backend tests and 11 frontend tests.
- `make check`: tests, Ruff, mypy, ESLint, TypeScript checking, frontend production build, and offline configuration/YAML validation.
- `make dev-mock`: FastAPI and Vite started together and stopped cleanly.
- `GET http://localhost:5173/api/health`: returned HTTP 200 in mock mode through the Vite proxy.
- A generated PDF uploaded through the Vite proxy and reached `UPLOADED`.
- Its parse attempt moved from `RUNNING` to `SUCCESS` with one retained page.
- The parsed-page viewer rendered two labelled elements; zoom, filtering, overlay selection, and inspector content were exercised in the browser.
- The schema viewer rendered the approved `invoice_v1` contract with eight fields,
  immutable provenance, thresholds, citation requirements, risk tiers, and explicit
  loading, empty, and error states.
- Failure, retry, concurrent-state, immutable-history, raw-result, image-confinement, cross-run access, missing-image, trigger-failure, polling-failure, scaling, navigation, filtering, and partial-state paths have automated coverage.

The local servers are not intentionally left running. Start them with:

```bash
make dev-mock
```

Then use:

- UI: `http://localhost:5173`
- Health: `http://localhost:5173/api/health`

## Databricks verification

Authenticated dev-workspace verification completed through 2026-08-29 against the
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
- Governed bootstrap runs `983917610156087` and `810202042098230` both succeeded,
  proving schema registration is idempotent.
- The live production-schema endpoint returned exactly one `invoice` schema at
  version 1 with hash
  `b02b3c20d69e7f77ed76e45337107d4995aafab5ee411ab4f2e73b166876c640`.
- The live schema-detail endpoint returned eight fields and two deterministic
  rules without exposing `ai_extract_schema` JSON.
- The final deployed app root references the verified Commit 6 JavaScript and CSS assets.

Additional live hardening checks remain:

- Duplicate re-upload messaging in the deployed UI.
- Direct Catalog Explorer inspection of retained `VARIANT`, derived text, and
  artifact-volume page images.
- Malformed-PDF failure behavior in the deployed environment.

Capabilities 2 through 6 are `COMPLETE`. The Parsing MVP is tagged and the first
Extraction MVP capability is deployed. Extraction execution has not started.

## Commit 7 continuation handoff

Work began from `43c26b0` on branch `feat/07-extraction-pipeline`. The extraction pipeline is
now implemented, deployed, verified live end to end, and committed. Existing untracked `output/`
files remain present and were neither removed nor added to Git.

Implemented locally:

- `POST /api/documents/{document_id}/extract` with a body restricted to governed schema identity.
- Immutable extraction history and deterministic latest-successful result APIs.
- Exact production-schema, successful-parse, and use-case preconditions.
- Fixed `ai_extract` 2.1 precision, citation, and confidence options plus the source
  idempotency key `document_id + parse_run_id + schema_id + schema_version + extractor_version`.
- SQLite and Databricks run repositories, complete raw-result retention, generic scalar
  flattening, citation resolution, typed invoice projection, and visible immutable retries.
- Parameterized Databricks extraction Job with an independent registry hash check and
  deterministic latest-successful-parse check before calling `ai_extract`.
- Guarded extraction-run `job_run_id` migration and least-privilege App bindings.
- No evidence interaction, validation, editing, approval, or export functionality.

Local evidence:

- `make check` passed with 65 backend tests and 11 frontend tests, followed by Ruff,
  mypy, ESLint, TypeScript checking, the production build, and offline bundle validation.
- The Vite-to-FastAPI path completed extraction run
  `beb9f4f9-71e9-4de0-85b1-7ddbee3d5890` for local document
  `c7b2d26c-cefd-50b6-a6c0-b58af1de44ea`.
- That run returned all eight schema fields with confidence and resolved page-zero boxes,
  and projected invoice `INV-LOCAL-7`, date `2026-08-29`, seller
  `Acme Supplies Ltd`, and `100.00 - 5.00 + 19.00 = 114.00 GBP` into typed values.
- The preserved scanned invoice also reached `EXTRACTED` in mock mode with null values,
  which is expected because the local PyMuPDF parser does not perform OCR.

Databricks live verification (completed 2026-08-29):

- Databricks CLI `1.14.1` (official Homebrew tap) with refreshed OAuth profile `idp-mvp` for
  `https://dbc-97e4a372-40b1.cloud.databricks.com`.
- Bootstrap Job `297705320672479` run `1031773756031830` succeeded, including
  `create_governed_objects`, `migrate_parsing_columns`, and `migrate_extraction_columns`.
- The bundle-managed `idp-mvp-dev` App deployment reached `SUCCEEDED`; `/api/health` reports
  `mode=databricks` with `parse_job_id` and `extraction_job_id` present.
- `POST /api/documents/d0ed9896-.../extract` with schema `invoice` v1 was triggered twice
  through the authenticated App, producing extraction Job runs `222795856966902` and
  `817490446741663`. Both reached `TERMINATED/SUCCESS`; both immutable extraction runs
  (`19978032-845f-4831-a968-8dffcb54cef0` and `13e7ac76-093f-481d-8360-42375bc8bda8`) are
  `EXTRACTED` and remain visible, with the latest deterministically the newest.
- Latest run `13e7ac76-093f-481d-8360-42375bc8bda8` reconciles across the governed tables to
  schema hash `b02b3c20d69e7f77ed76e45337107d4995aafab5ee411ab4f2e73b166876c640`:
  raw `ai_result` retained; 8 `idp_dev_extracted_fields` rows; one `idp_dev_invoice_candidates`
  row. Extracted fields include confidence `1.0` and resolved page-0 citation boxes
  (for example `invoice_date` cites bbox `[3,112,417,304]` on page 0, `total` cites
  `[893,1542,1222,1579]`).
- Typed projection: `invoice_number=INV/06-92/543`, `seller_name=Mclean-Cochran`,
  `discount_amount=29.87`, `total_amount=888.55`, `currency=EUR`, and `invoice_date=2011-07-28`.
- Between the two runs the date typing was hardened: `parse_date` (Databricks Job) and
  `_date_value` (local backend) now accept unambiguous four-digit-year named-month formats in
  addition to ISO, so `28-Jul-2011` types to `2011-07-28` while the raw value stays preserved
  in `extracted_fields`. Ambiguous numeric forms such as `dd/mm/yyyy` intentionally remain null.
  The first run predates this fix and correctly shows a null typed `invoice_date` with the raw
  value retained; the second run shows the typed date. `make check` (66 backend, 11 frontend
  tests) was re-run and passed after the fix.

Use these trusted dev values for subsequent bundle commands:

```text
catalog=workspace
project_schema=idp_mvp
source_volume_name=idp_source
artifacts_volume_name=idp_artifacts
warehouse_id=647704f77f24020a
validation_endpoint=unused
evaluation_experiment=unused
app_name=idp-mvp
```

All six continuation steps are complete: the App deployment was confirmed and health-checked,
extraction was triggered and polled to `EXTRACTED`, the raw/generic/typed outputs were
reconciled against the governed tables, `make check` was re-run after the date-typing fix, this
context and the progress tracker were updated, and the branch was committed and pushed. Capability
7 is `COMPLETE`.

## Next review boundary

Commit 7 is complete and pushed on `feat/07-extraction-pipeline`. The next increment is
Commit 8 (extraction evidence UI), which links typed fields to their supporting PDF regions.
Evidence interaction, validation, editing, approval, and export remain out of scope until then.
