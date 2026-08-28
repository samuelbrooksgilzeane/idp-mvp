# IDP MVP

Incremental Intelligent Document Processing application. The current branch includes the deployable FastAPI and React foundation, governed data bootstrap, secure PDF intake, and an idempotent document parsing workflow. Extraction is not included.

## Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)
- Node.js 20.19 or later and npm
- Databricks CLI only for an authenticated bundle validation or later deployment

## Setup

Install the pinned Python and frontend dependencies:

```bash
make setup
```

Local environment values can be based on `.env.example`. The default `IDP_MODE=mock` requires no Databricks values, credentials, CLI, or network access at runtime. Never commit `.env` files or credentials.

## Local mock development

Start FastAPI and Vite together:

```bash
make dev-mock
```

The UI is available at <http://localhost:5173>. Vite proxies API requests to FastAPI, including <http://localhost:5173/api/health> and the document registry. Press `Ctrl+C` once to stop both processes.

Mock mode stores source PDFs beneath `.local/idp/source_volume/incoming/`, page images beneath `.local/idp/artifacts_volume/page_images/`, and registry metadata in `.local/idp/registry.sqlite3`. Both source and derived data are ignored by Git. Local parsing uses PyMuPDF and requires no Databricks credentials, CLI, or runtime network access. Remove local mock data only when you intentionally want a fresh local demonstration; application startup never deletes it.

## Tests and checks

```bash
make test
make check
```

`make check` runs backend and frontend tests, Python and TypeScript linting/type checks, the frontend production build, and offline validation of `app.yaml` and `databricks_etl/databricks.yml`.

## Configuration

Server settings use the `IDP_` environment prefix:

| Setting | Purpose |
|---|---|
| `IDP_MODE` | `mock` locally or `databricks` when deployed |
| `IDP_CATALOG` | Existing permitted catalog |
| `IDP_PROJECT_SCHEMA` | Single project schema |
| `IDP_TABLE_PREFIX` | Target-specific object prefix |
| `IDP_SOURCE_VOLUME_NAME` | Source volume name |
| `IDP_ARTIFACTS_VOLUME_NAME` | Artifact volume name |
| `IDP_WAREHOUSE_ID` | SQL warehouse identifier |
| `IDP_PARSE_JOB_ID` | Deployed document-parser Job identifier |
| `IDP_VALIDATION_ENDPOINT` | Future validation endpoint |
| `IDP_APP_NAME` | Application display name |
| `IDP_LOCAL_DATA_DIR` | Ignored local mock storage root |
| `IDP_MAX_UPLOAD_BYTES` | Maximum size of each streamed PDF |
| `IDP_MAX_UPLOAD_FILES` | Maximum PDFs in one multipart request |

Databricks mode validates all required settings before application startup and does not attempt a connection when configuration is incomplete. Catalog, schema, prefix, and volume values must each be one simple identifier containing only ASCII letters, numbers, and underscores.

## Governed data bootstrap

`databricks_etl/sql/create_objects.sql` creates the configured project schema, source and artifacts volumes, seven governed Delta tables, and three views. All table and view names use the target-specific prefix. The migration uses `IF NOT EXISTS`, never creates a catalog, and contains no `DROP` or `TRUNCATE` operation. Reverting application code does not remove persisted objects.

The bundle defines dev and prod targets in the same project schema, using `idp_dev` and `idp` table prefixes respectively. It deliberately contains no workspace hostname or credentials.

The deployment identity must have:

- `USE CATALOG` on the configured existing catalog.
- `CREATE SCHEMA` on that catalog only when the project schema does not already exist.
- `USE SCHEMA`, `CREATE TABLE`, and `CREATE VOLUME` on the project schema.
- Permission to use the configured SQL warehouse and create/run the bootstrap Job.

Object ownership remains with the approved deployment identity. Application runtime grants should be limited to `USE CATALOG`, `USE SCHEMA`, volume file access, and the table operations required by the implemented capability.

For document intake, the Databricks App service principal additionally needs `READ VOLUME` and `WRITE VOLUME` on the configured source volume, `SELECT` and `MODIFY` on the prefixed documents table, and `CAN USE` on the SQL warehouse. The backend uses Databricks unified authentication, writes only under the server-owned `incoming/` directory, and records the trusted forwarded user identity. Tokens, internal credential-bearing URLs, and volume paths are never returned to the browser.

## Document API

```text
POST /api/documents
GET  /api/documents
GET  /api/documents/{document_id}
```

`POST /api/documents` accepts one or more multipart `files` fields plus optional `case_id`; the server fixes the current profile to `invoice_v1`. Files must have a `.pdf` extension, `application/pdf` media type, and `%PDF-` signature. Uploads are streamed and hashed with SHA-256. Duplicate content is rejected before another active registry row is created, even when the filename changes.

Stable errors include `UNSUPPORTED_FILE_TYPE`, `FILE_TOO_LARGE`, `DOCUMENT_DUPLICATE`, `FILE_STORAGE_FAILED`, and `REGISTRY_WRITE_FAILED`. A mixed multi-file result uses HTTP 207 and reports each rejected file explicitly. No route accepts a volume or table path.

## Parsing API

```text
POST /api/documents/{document_id}/parse
GET  /api/documents/{document_id}/parse-runs
GET  /api/runs/{parse_run_id}
```

The manual trigger accepts only a registered document identifier. Eligible documents move through `UPLOADED` or a retry state to `PARSING`, then `PARSED` or `PARSE_FAILED`. Every retry creates a new immutable parse run using the identity inputs `document_id`, source SHA-256, and parser version. The API returns status and run metadata but does not expose source paths, artifact paths, or raw parser output.

The Databricks Job reads only the server-registered source path, pins `ai_parse_document` to version `2.0`, leaves `descriptionElementTypes` empty, and renders images under the configured artifacts volume. It retains the complete `VARIANT` response before deriving text, page count, image references, and parse errors. Source PDFs are never moved or deleted. The App service principal needs `CAN MANAGE RUN` on the parser Job in addition to its intake grants. The Job run identity needs `READ VOLUME` on the source volume, `READ VOLUME` and `WRITE VOLUME` on the artifacts volume, and `SELECT` plus `MODIFY` on the documents and parsed-documents tables.

In an authenticated environment, supply the required bundle variables through the approved deployment configuration and run:

```bash
cd databricks_etl
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev governed_data_bootstrap
databricks bundle run -t dev governed_data_bootstrap
databricks bundle run -t dev idp_app
databricks bundle run -t dev document_parser \
  --params document_id=<registered-uuid>,parse_run_id=<new-uuid>,source_path=<registered-volume-path>,image_output_path=<trusted-artifacts-path>
```

Running the bootstrap twice is the live idempotency check. After both runs, inspect the configured project schema in Catalog Explorer and confirm that both volumes, all prefixed tables, and the latest-successful-run views exist. Then parse a representative registered invoice through the UI, confirm the document reaches `PARSED`, and inspect its raw `VARIANT`, derived text, page count, and artifact-volume page images. Also verify a malformed PDF reaches `PARSE_FAILED` without changing its source file. The local `make check` validation remains credential-free and validates the reviewed bundle structure, non-destructive SQL contract, and parser task configuration.

The bundle also creates the Databricks App and binds its service principal to the
configured SQL warehouse, parser Job, source/artifact volumes, documents table,
and parsed-documents table with capability-specific permissions. The deployed App
runs with `IDP_MODE=databricks`; resource IDs are injected from App resource
bindings rather than copied into browser requests or committed configuration.
