# Commit 1 — Project Foundation

## Outcome

A minimal Databricks App and Asset Bundle build successfully, validate their configuration and expose a health endpoint. No data objects or processing logic exist yet.

## Recommended commit

```text
chore(idp): scaffold deployable app and asset bundle
```

## Scope

Create or adapt:

- `databricks_etl/databricks.yml`
- Empty `resources/` includes.
- Backend application factory and `/api/health`.
- Frontend shell with workflow header and an “MVP not configured” state.
- Typed server-side configuration.
- Dev/prod targets using prefixes inside the same project schema.
- Baseline Python and frontend test commands.
- Root README containing prerequisites and commands.

Required configuration is defined in [Technical contracts](01_TECHNICAL_CONTRACTS.md).

## Implementation requirements

1. Validate catalog, project schema, table prefix and volume names against a strict identifier pattern.
2. Do not create any catalog or schema in this commit.
3. Do not add document APIs, tables, parsing libraries or model calls.
4. Do not hardcode a workspace hostname.
5. Keep backend routers, configuration and services in separate modules.
6. Ensure the frontend can build before any backend data is available.
7. Return safe configuration-presence checks from `/api/health`; never return credentials or secrets.

## Tests

- Configuration accepts valid identifiers.
- Configuration rejects identifiers containing dots, quotes, slashes or SQL syntax where a simple name is expected.
- Missing required configuration fails at startup with a clear message.
- `/api/health` returns success from a test client.
- Frontend lint, type-check and production build pass.
- YAML parses.
- `databricks bundle validate -t dev` passes where credentials permit.

## Demonstration

Open the app and show the stable shell and health status. Explain that no storage or model processing has been introduced yet.

## Rollback boundary

Reverting this commit removes only the new project scaffold. No persistent data exists.

## Progress statement

> The deployable application foundation is in place; document storage and processing have not been introduced yet.

## Definition of done

- All repository baseline checks pass.
- App and bundle configuration are documented.
- No later-stage code is present.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

