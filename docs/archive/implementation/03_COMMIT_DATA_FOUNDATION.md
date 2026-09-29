# Commit 2 — Governed Data Foundation

## Outcome

The single project schema contains idempotently created, prefixed volumes, tables and latest-successful-run views. The app still cannot upload documents.

## Recommended commit

```text
feat(idp): create governed storage and audit tables
```

## Scope

Implement:

- `create_objects.sql` or equivalent migration.
- Bundle deployment task for bootstrap.
- Source and artifacts volumes.
- Tables defined in [Technical contracts](01_TECHNICAL_CONTRACTS.md).
- Latest-successful parse/extraction views.
- Validation-summary view skeleton.
- Data-object smoke tests.

## Implementation requirements

1. Use the configured existing catalog.
2. Run `CREATE SCHEMA IF NOT EXISTS <catalog>.<project_schema>` only if bootstrap owns schema creation.
3. Never run `CREATE CATALOG`.
4. Create all tables/views in the one project schema using `<table_prefix>_`.
5. Create volumes inside that same project schema.
6. Make bootstrap safe to run repeatedly.
7. Never drop or truncate objects during deployment or application startup.
8. Add table and column comments for governance.
9. Preserve fields needed for future Risk and Investigations work: `case_id`, `template_id`, immutable runs and future `RECONCILIATION` validation type.

## Tests

- Bootstrap can run twice without error or data loss.
- Every expected object resolves to the configured catalog/project schema/prefix.
- No object is created in `default` or another schema.
- Dev/prod prefixes create distinct object names when both targets are configured.
- Views return an empty but valid schema before any data exists.
- Bundle validation passes.

## Demonstration

Show the project schema and its prefixed objects in Catalog Explorer. No document data should exist.

## Rollback boundary

Reverting application code must not drop created objects. Any cleanup is a separate, explicitly approved migration.

## Progress statement

> The governed storage, audit history and environment isolation are ready; no files are being ingested yet.

## Definition of done

- Bootstrap is idempotent.
- Object ownership and required grants are documented.
- No destructive migration exists.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

