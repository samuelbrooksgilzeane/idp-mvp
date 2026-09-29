# Commit 6 — Schema Registry and Viewer

## Outcome

Users can see the exact, versioned extraction contract before any extraction is run.

## Recommended commit

```text
feat(idp): add versioned extraction schema registry
```

## Scope

Implement:

- Source-controlled `invoice_v1.json` manifest.
- Manifest validation and deterministic hashing.
- An idempotent deployment task that registers schema versions.
- `GET /api/schemas?status=PRODUCTION&use_case=invoice`.
- `GET /api/schemas/{schema_id}/versions/{schema_version}`.
- Read-only schema selector and field table in the document UI.

Do not call `ai_extract` in this commit.

## Schema contract

The manifest must separate:

- `ai_extract_schema`: only the field definitions passed to `ai_extract`.
- `field_policies`: required status, confidence threshold, citation requirement and risk tier.
- `document_rules`: deterministic validation rules and tolerances.
- Metadata: schema ID, integer version, display name, use case, production status and instructions.

Seed `invoice_v1` with invoice number, invoice date, seller name, subtotal, discount, tax, total and currency. Extraction instructions must say to return source-stated values and not infer or calculate missing values.

## Implementation requirements

1. Validate manifests against a source-controlled JSON Schema or typed application model.
2. Canonicalise JSON before calculating `schema_hash` so formatting changes do not create false differences.
3. Upsert a new `schema_id + schema_version` idempotently.
4. Refuse deployment if an existing version has a different hash; require a new version number.
5. Pass only server-selected, `PRODUCTION` schema metadata to the browser.
6. Never accept raw schema JSON, table names, volume paths or compute identifiers from the browser.
7. Show field label, type, description, required status, citation requirement, confidence threshold and risk tier.
8. Make clear that thresholds are initial policy settings to be calibrated with benchmark results.

## Tests

- Valid and invalid manifest fixtures.
- Stable hash under key-order and whitespace changes.
- Idempotent registration of the same version and hash.
- Rejection of a changed immutable version.
- Production/use-case API filtering.
- Missing and unknown schema responses.
- Read-only selector and field-table rendering.
- Browser requests cannot inject schema JSON.

## Demonstration

Open an invoice, select `invoice_v1` and show stakeholders every field, type and policy the later extraction will use.

## Rollback boundary

Reverting removes schema registration code, APIs and UI. The registry table created earlier remains, and any registered versions remain as inert audit records.

## Progress statement

> The extraction requirements are now transparent, versioned and controlled before any data is extracted.

## Definition of done

- `invoice_v1` is registered and visible in the UI.
- An existing version cannot be silently changed.
- No extraction code is present.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

