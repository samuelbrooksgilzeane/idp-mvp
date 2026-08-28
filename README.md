# IDP MVP

Incremental Intelligent Document Processing application. The current branch includes the deployable FastAPI and React foundation plus a governed, idempotent data bootstrap. Document upload and processing remain unavailable in this increment.

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

The UI is available at <http://localhost:5173>. Vite proxies the safe health endpoint at <http://localhost:5173/api/health> to FastAPI. Press `Ctrl+C` once to stop both processes.

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
| `IDP_VALIDATION_ENDPOINT` | Future validation endpoint |
| `IDP_APP_NAME` | Application display name |

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

In an authenticated environment, supply the required bundle variables through the approved deployment configuration and run:

```bash
cd databricks_etl
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev governed_data_bootstrap
databricks bundle run -t dev governed_data_bootstrap
```

Running the bootstrap twice is the live idempotency check. After both runs, inspect the configured project schema in Catalog Explorer and confirm that both volumes, all prefixed tables, and the latest-successful-run views exist with no document rows. The local `make check` validation remains credential-free and validates the reviewed bundle structure and non-destructive SQL contract.
