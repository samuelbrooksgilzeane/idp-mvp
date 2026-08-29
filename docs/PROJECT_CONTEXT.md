# IDP MVP Project Context

Last updated: 2026-08-29

This document is a concise engineering handoff. The authoritative requirements remain unchanged under `docs/implementation/`; use the numbered commit specifications and `PROGRESS_TRACKER.md` for acceptance decisions.

## Repository state

- Local repository: `/Users/samb/Documents/coding projects/idp_databricks/idp-mvp`
- Origin: `https://github.com/samuelbrooksgilzeane/idp-mvp.git`
- Current implementation branch: `feat/13-batch-processing`
- The Parsing MVP is accepted and tagged `mvp-parsing`.
- The Extraction MVP (commits 6–8) is complete and tagged `mvp-extraction`.
- Batch processing is implemented, deployed, verified live, and committed.
- **Branches form a linear stack**: each is built on the previous one, so
  `feat/13-batch-processing` contains every change listed below. Start any new work from it.
- Work now follows [the working plan](implementation/13_PLAN_BATCH_UI_EXPORT_ASSISTANT.md),
  an agreed insertion into the numbered pack. **Section D is implemented locally and awaits dev
  deployment/verification.**
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
| Commit 8: extraction evidence UI | `feat/08-extraction-evidence-ui` | `41ee11e` | Implemented, deployed, and verified live |
| Commit 9: deterministic validation | `feat/09-deterministic-validation` | `5eec8b4` | Implemented, deployed, and verified live |
| Line-item extraction (inserted) | `feat/10-line-item-extraction` | `0dbe759` | Implemented, deployed, and verified live |
| Plan section A: multi-page UI | `feat/11-multi-page-ui` | `58cc981`, `7b37402`, `beda63b` | Implemented, deployed, and verified live |
| Plan section B: typed line candidates | `feat/12-typed-line-candidates` | `60ccb00` | Implemented, deployed, and verified live |
| Plan section C: batch processing | `feat/13-batch-processing` | `45f001a` | Implemented, deployed, and verified live |
| Plan section D: summary and XLSX export | current working tree | — | Implemented and verified locally; dev verification pending |

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
- Two governed volumes, nine Delta tables, and four latest-result/summary views.
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

### Extraction evidence UI

- Per-run result API `GET /api/documents/{document_id}/extractions/{extraction_run_id}` returning
  the same run/fields/candidate shape as the latest endpoint, with 404 for unknown or
  cross-document run identifiers.
- `ExtractionPanel` with a run selector that defaults to the latest successful run and keeps prior
  runs inspectable, plus run provenance (schema id, version, hash, status, `ai_extract 2.1`).
- A "Run extraction" trigger that resolves the production schema for the document use case,
  submits only the governed schema identity, and polls extraction history to a terminal state.
- A field table showing each field's raw extracted value, typed candidate value, model confidence
  framed as metadata, and citation status, with an explicit `No citation returned` state and
  visible per-field extraction errors.
- Citation evidence overlay reusing the parse viewer's page-image, natural-to-rendered scaling, and
  zoom/resize contract; selecting a cited field navigates to its page and draws citation boxes that
  are visually and accessibly distinct from parse-element overlays.
- All volume paths, artifact paths, and credentials remain behind the backend.
- No validation, arithmetic or required-field checks, correction, editing, approval, or export.

### Deterministic validation

Validation splits into two deliberately different mechanisms.

- **Structural validators** are generic and need no per-schema configuration, so they apply to any
  use case: `provenance`, `schema_drift` (integrity of the exact contract used),
  `schema_version_currency` (a newer contract exists), `parse_staleness`, `cast_integrity`,
  `grounding`, `citation_presence`, `citation_geometry`, `confidence_threshold`,
  `duplicate_document` and `field_coverage`.
- **Business rules are declarative**, read from the registered schema manifest as a closed set of
  parameterised types: `arithmetic_reconciliation`, `required_fields`, `allowed_values`, `range`,
  `format` and `comparison`. There is deliberately no expression language: adding a rule *instance*
  is a governed schema version bump, while adding a rule *type* is a reviewed code change. Rule
  configuration is validated at registration, so the immutable `schema_hash` only ever covers a
  known-good configuration.

Supporting decisions:

- `invoice_v2` is registered with explicit signed reconciliation terms and a target, plus range,
  format and comparison rules. `invoice_v1` remains registered and immutable, and its hash
  `b02b3c20…640` is unchanged and asserted by a regression test. Because v1 declares no signed
  terms, its reconciliation rule reports `SKIPPED` rather than the engine guessing the signs.
- `FieldPolicy.semantic_type` optionally declares that a string field is a date or a currency code,
  so semantic casting is driven by the registered contract rather than hardcoded per use case.
- Deterministic validation is pure computation over persisted data. It needs no Databricks Job and
  completes synchronously, so there is no `RUNNING` state to poll.
- Absent inputs never produce a pass: a calculation missing a value returns `UNCERTAIN` or
  `SKIPPED`. Low confidence is a review signal, never proof of an incorrect value.
- Validators only observe. They never edit an extracted value or approve a document, and neither
  `VALIDATED_PASS` nor `REVIEW_REQUIRED` means a person has approved anything.
- Rules are engineer-authored in source-controlled JSON. User-authored rules remain a planned
  future extension; the closed-set configuration is deliberately shaped so a constrained rule
  builder could emit it later behind schema registration and approval.
- Repeated-field rule paths (`line_items[*].amount`) already validate at registration, so the
  engine needs no redesign when nested line-item extraction lands.

### Line-item extraction

- Recursive `ExtractField` supporting `array` and `object`, matching the documented `ai_extract`
  nested-schema shape, with the documented 256-leaf and 12-level limits enforced at registration.
- `field_policies` are keyed by scalar **leaf**: a header field keeps its bare name (`total`) and a
  nested leaf uses the wildcard form (`line_items[*].amount`), so a line amount can carry its own
  risk tier and citation requirement.
- Both flatteners walk the contract recursively and emit one row per leaf at `line_items[0].amount`.
  `field_path` is already a `STRING`, so **no table change was required**. An absent or empty
  repeated field emits no rows and is never treated as an implicit zero.
- `RuleTerm.aggregate` folds a repeated field inside the existing `arithmetic_reconciliation`
  rule, so line-item totals needed **no new rule type**. An aggregate over zero returned instances
  is missing, not zero, so it can never let a calculation pass.
- `invoice_v3` adds five line leaves and two rules: `line_items_reconcile_to_total` (blocking) and
  `line_items_sum_to_subtotal`, which is declared `WARNING` because it corroborates the primary
  chain rather than gating it. v1 and v2 remain registered and immutable.
- The extraction panel groups repeated leaves into a per-line table, each cell keeping its
  confidence and its own evidence control into the page viewer.
- Line items are deliberately not projected into `invoice_candidates`; a typed line table is
  deferred.

### Multi-page workspace (plan section A)

- `react-router-dom` routes: registry at `/`, a document at `/documents/:id`, results at
  `/results`, and the extraction contract at `/schema`.
- `SinglePageStaticFiles` in `main.py` falls back to the client entry point for extension-less
  paths, so a deep link or refresh resolves; a genuinely missing asset still returns 404.
- The document page keeps the **source viewer on the left and the panel on the right**, so citing
  a value moves the highlight rather than navigating. Tabs (Extraction, Validation, History) swap
  only the right pane; the viewer stays mounted while hidden so its page image and zoom survive.
- The values table shows field, raw extracted value, model confidence and evidence. The typed
  projection column was removed on request and the confidence unit lives in the column header.
- Shared types live in `frontend/src/types.ts`; pages live in `frontend/src/pages/`.

### Typed line-item candidates (plan section B)

- Governed `invoice_line_candidates` table: `line_number`, `description`,
  `quantity DECIMAL(18,4)`, and `unit_price`, `tax`, `amount` as `DECIMAL(18,2)`.
- Written alongside the header candidate by both the Databricks task and the local executor.
- `line_number` is **one-based** for reading; the matching evidence path is
  `line_items[line_number - 1]`.
- Only lines the model returned produce rows. An invoice with no line table yields none, never a
  zero-valued row that could satisfy a later reconciliation.

### Batch processing (plan section C)

- `POST /api/batches/parse` and `POST /api/batches/extract` take a set of document IDs;
  `GET /api/batches/{kind}/{job_run_id}` aggregates member runs into batch progress.
- Both Jobs wrap their existing per-document task in a `for_each` whose inputs are a trusted job
  parameter. **Concurrency is set explicitly from `batch_concurrency` (default 16) because
  `for_each` defaults to 1 and would otherwise run sequentially**; `scripts/validate_configuration.py`
  asserts this so the default cannot creep back in.
- A batch is one job run, so members share a `job_run_id`; **no new table was needed**.
- Preconditions are evaluated per document: an ineligible document is reported against itself and
  the rest of the batch still runs.
- A single document is a batch of one, so there is one submission path rather than two.
- The API exposes documents and a batch identity but never the execution engine, so plan section F
  can replace `for_each` without touching the contract or the client.
- Registry multi-select and a batch action bar with per-batch progress.

### Summary and export (plan section D)

- Governed `invoice_summary` view selects each document's latest successful extraction, aggregates
  typed billed lines, computes the reconciliation delta once, and attaches the latest completed
  validation outcome.
- `GET /api/results/invoices` and `GET /api/exports/invoices.xlsx` share the same reporting
  repository and optional `case_id` scope; the workbook contains Summary and Line items sheets.
- `GET /api/documents?case_id=...` filters the registry and `GET /api/documents/cases` supplies
  distinct dropdown choices without adding case CRUD.
- The Results route provides scope controls, operational totals, invoice-to-document navigation,
  currency-aware amounts, delta state, validation outcome, refresh, and XLSX export.
- Public JSON and workbook output omit source paths and extraction-run identifiers.

## Local verification

The following passed on 2026-08-29:

- `make check`: 133 backend tests and 32 frontend tests, Ruff, strict mypy, ESLint,
  TypeScript checking, the frontend production build, and offline configuration/YAML validation.
- Reporting tests open the generated workbook and verify both sheets, typed number/date formats,
  latest-run selection, case scope, reconciliation behavior, and public-field confinement.
- Browser QA covered the populated Results route at desktop and 390px widths; the summary table
  remains horizontally contained on mobile and the representative delta renders as `-€37.31`.
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

## Commit 8 verification (2026-08-29)

- `make check` passed: 67 backend tests, 16 frontend tests, Ruff, mypy, ESLint, TypeScript,
  production build, and offline configuration/YAML validation.
- New frontend tests cover the citation overlay page-jump and scaled geometry, and the panel's
  field rendering, provenance, `No citation returned` state, multi-citation evidence, and
  historical run selection. New backend tests cover the per-run endpoint for a non-latest run and
  its 404s for unknown and cross-document run identifiers.
- The dev bundle was redeployed and the `idp-mvp-dev` App source was updated
  (`databricks bundle run … idp_app`); `/api/health` reports Databricks mode and the deployed root
  serves the new frontend bundle.
- Citation-coordinate alignment was verified against invoice `d0ed9896`: the page-0 image is
  1653×2336 pixels, and all six returned citations fall within those bounds and overlap the same
  page-0 parse-element boxes the viewer already renders (invoice number/date → the header element,
  seller → its element, discount → its element, total/currency → the totals line). Because the
  overlay reuses the identical `scaleBoundingBox` contract, citation boxes land on the cited
  regions.

## Commit 9 verification (2026-08-29)

- `make check` passed: 108 backend tests, 20 frontend tests, Ruff, mypy, ESLint, TypeScript, the
  production build and offline configuration/YAML validation.
- The governed `validation_runs` table was added to `create_objects.sql`; because every table uses
  `CREATE TABLE IF NOT EXISTS`, the existing bootstrap covers new and existing environments and no
  separate migration file was needed.
- Bootstrap now registers `invoice_v1` and `invoice_v2`; both are present in the dev registry.
- The deployed App was updated. The first deploy failed because the new least-privilege App binding
  referenced `idp_dev_validation_runs` before bootstrap created it; running bootstrap first and then
  redeploying resolved it.
- Live on invoice `d0ed9896`, validated under `invoice_v2`: 37 observations, 32 passed, zero
  failures, and reconciliation correctly `UNCERTAIN` because that scanned invoice returns no
  subtotal or tax. `schema_drift` passes and `schema_version_currency` passes.
- Live demonstration of the required behaviour, through the deployed App:
  a balanced invoice reached `VALIDATED_PASS` with 43 of 43 checks passing and reconciliation
  `PASS`; the same invoice pushed outside tolerance reached `REVIEW_REQUIRED` with a `BLOCKING`
  reconciliation failure reporting "out by 360, beyond the configured tolerance of 0.01".
- Four immutable validation runs and their 33, 37, 43 and 43 results are retained in
  `idp_dev_validation_runs` and `idp_dev_validation_results`.

Two defects were found and fixed while verifying:

- `schema_drift` originally compared the run against the newest registered version, so a document
  extracted under v1 was wrongly reported as a hash mismatch. Contract integrity and contract
  currency are now separate observations with accurate messages.
- The Commit 8 citation overlay assumed a four-value rectangle. The local parser emits an
  eight-value polygon, which would have rendered zero-height citation boxes in mock mode. Both the
  overlay and the new geometry validator now reuse the viewer's canonical `normalise_box`
  convention, which accepts either shape.
- Re-parsing and re-extracting are now permitted from `VALIDATED_PASS` and `REVIEW_REQUIRED`, so a
  validated document can still be corrected.

## Line-item verification (2026-08-29)

- `make check` passed: 119 backend tests, 21 frontend tests, and the full lint, type, build and
  configuration gate.
- Bootstrap registered `invoice_v3` in dev alongside the immutable v1 and v2.
- **Live on the real invoice `d0ed9896`**, re-extracted under v3: all five line items were
  extracted with citations — `3 x 91.65 = 274.95`, `2 x 33.01 = 66.02`, `6 x 3.98 = 23.88`,
  `1 x 45.86 = 45.86`, `6 x 78.40 = 470.40` — summing to exactly **881.11**, matching the source
  table. 139 observations, 132 passed. `line_items_reconcile_to_total` **FAILED as BLOCKING, out
  by 37.31** (881.11 − 29.87 + 0.00 = 851.24 against a stated total of 888.55), and
  `line_items_sum_to_subtotal` returned `UNCERTAIN` at `WARNING` severity because that invoice
  states no subtotal. This is a genuine inconsistency caught in real data, not a synthetic fixture.
- A balanced synthetic invoice with two line rows reached `VALIDATED_PASS` with 85 of 85 checks
  passing and both line rules `PASS`.

One defect was found and fixed while verifying:

- The manifest hash is computed independently by the backend, the registration task and the
  extraction task. JSON does not distinguish `0` from `0.0`, but typed loading does, so a rule
  parameter written as `"minimum": 0` hashed differently in the backend (`0.0`) than in the two
  Databricks tasks (`0`). Runtime was self-consistent because the registry value is authoritative,
  but the implementations disagreed. All three now normalise integral numbers identically, which
  leaves every already-registered hash unchanged, and a test pins the three implementations
  together so they cannot drift again.

## Next review boundary

Everything through **plan section D is complete and verified live in dev**. The authoritative plan,
its decisions and the remaining sections are in
[`implementation/13_PLAN_BATCH_UI_EXPORT_ASSISTANT.md`](implementation/13_PLAN_BATCH_UI_EXPORT_ASSISTANT.md).

**Next: section E**, which is blocked on infrastructure. `validation_endpoint` is still deployed as
the literal string `unused`, so a Databricks model serving endpoint must exist before LLM validation
can be verified live. The Knowledge Assistant table requirements are recorded in the plan.

## Section D verification (2026-08-29)

Deployed and demonstrated live against the dev App
`https://idp-mvp-dev-7474660341420973.aws.databricksapps.com`.

- `make check` passes with **134 backend and 32 frontend tests** and the full lint, type, build and
  configuration gate.
- The two-pass deployment behaved exactly as predicted: the first `bundle deploy` failed with
  `Invalid UC Table resource invoice-summary-select: Table workspace.idp_mvp.idp_dev_invoice_summary
  does not exist`; governed bootstrap run `735102880444788` created the view; the second deploy
  reported `Updated apps.idp_app`; `bundle run … idp_app` republished the source.
- `/api/health` returns `mode: databricks` with every configuration key present.
- `/api/results/invoices` and `/api/exports/invoices.xlsx` serve seven invoices from the governed
  `invoice_summary` view. The workbook has both `Summary` and `Line items` sheets, joins on document
  ID and invoice number, and exposes no volume path or internal run identifier.
- The representative invoice `d0ed9896` reports **5 lines, 881.11 against a stated 888.55, delta
  `-37.31`**, `REVIEW_REQUIRED`, unchanged by the correction below.
- Case filtering is demonstrated on real data. Two new invoices were uploaded under `CASE-ALPHA` and
  `CASE-BETA`, parsed as batch job run `227813517718902` and extracted as `907357470375810`.
  `/api/documents/cases` lists the cases, and the documents list, the results list and the export
  are all scoped by `case_id`, the export naming its workbook `invoice-results-CASE-ALPHA.xlsx` and
  scoping both sheets consistently.

### The reported delta must use the registered rule's signed terms

The blank `Validation outcome` column seen first was **not a defect**: the section C batch
re-extracted every document after the last validation, and the view reports the outcome *of the
latest extraction*, so no outcome existed. Re-validating repopulated it.

A real defect was found and fixed. The view computed `reconciliation_delta` as
`sum(amount) - discount - total`, while the registered rule `line_items_reconcile_to_total`
reconciles `sum(amount) - discount + sum(line tax)` against the total. Every invoice in dev happened
to state zero or no line tax, and every seeded line in `test_reporting_api.py` used `tax = "0"`, so
the two agreed by coincidence and no test covered the difference.

`CASE-ALPHA` was built to expose it and did: an invoice that the validator passed
(`1500 - 100 + 300 = 1700`) was reported in the export as **delta `-300.00` beside
`VALIDATED_PASS`**, and the results page painted the row as an exception and excluded it from its
"Reconciled" tile. `CASE-BETA` reported `-151.20` where the validator said "out by 45.0".

The view and the local SQLite projection now use the rule's signed terms, and an unstated line tax
is treated as missing rather than zero, exactly as the validator treats an aggregate over zero
stated instances. After the fix `CASE-ALPHA` reports `0.00` beside `VALIDATED_PASS`, `CASE-BETA`
reports `-45.00` matching its validation message, and `d0ed9896` still reports `-37.31`. A test
pins the rule and the report together so they cannot drift apart again.

### Replacing a governed view revokes the App's grant

`create_objects.sql` uses `CREATE OR REPLACE VIEW`, which recreates the securable and **drops the
Unity Catalog grant the App resource binding had applied**. Re-running the bootstrap after a deploy
left `/api/results/invoices` returning `502 REPORT_READ_FAILED` even though the view itself queried
correctly from SQL. A further `bundle deploy` reapplied the grant and the endpoint recovered.

**Whenever the bootstrap replaces a view an App binding reads, run `bundle deploy` again
afterwards.** The ordering that works is: `bundle deploy` → `bundle run … governed_data_bootstrap`
→ `bundle deploy` → `bundle run … idp_app`.

### The `frontend/dist` sync warning is a false alarm

`databricks bundle validate` reports `Pattern frontend/dist/** does not match any files` because it
tests the pattern against the gitignore-filtered file set, and `frontend/dist/` is in `.gitignore`.
The sync honours `sync.include` regardless. `databricks bundle sync -t dev --dry-run --full -o json`
lists `frontend/dist/index.html` and the current hashed assets in its `put` payload; confirm the
hashes there match the local build rather than trusting the warning. This does not block a release.

### Trusted dev variables

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

To resume:

```bash
git checkout feat/13-batch-processing
make check          # expect 134 backend and 32 frontend tests
```

Deploying to dev needs the two-step ordering whenever a release adds both a governed object and an
App binding that references it: `bundle deploy` (the App update fails), then
`bundle run … governed_data_bootstrap` to create the object, then `bundle deploy` again, then
`bundle run … idp_app` to publish the App source.
