# IDP MVP

Project-foundation increment for an Intelligent Document Processing application. The current repository provides a deployable FastAPI and React shell, strict trusted configuration, and Databricks Asset Bundle metadata. It does not create storage or connect to Databricks.

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

## Databricks handoff

`app.yaml` contains the application entry point and no secrets. This foundation increment deploys in mock mode and makes no Databricks connections. `databricks_etl/databricks.yml` defines dev and prod targets in the same configured project schema, using `idp_dev` and `idp` table prefixes respectively. It deliberately contains no workspace hostname.

In an authenticated environment, supply the required bundle variables through the approved deployment configuration and run:

```bash
cd databricks_etl
databricks bundle validate -t dev
```

The local `make check` validation is intentionally credential-free. No catalog, schema, volume, table, Job, serving endpoint, or external connection is created in this increment.
