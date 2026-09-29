# Commit 7 — Extraction Pipeline

## Outcome

A parsed invoice can produce a typed extraction candidate with per-field confidence and source citations.

## Recommended commit

```text
feat(idp): extract versioned invoice fields with evidence
```

## Scope

Implement:

- `POST /api/documents/{document_id}/extract`.
- A parameterised Databricks extraction job.
- Raw extraction-run persistence.
- Generic field flattening from the registered schema.
- Citation resolution and confidence persistence.
- Typed `invoice_v1` candidate projection.
- `GET /api/documents/{document_id}/extraction-runs`.
- `GET /api/documents/{document_id}/extractions/latest`.

Do not implement field-evidence interaction or validation in this commit.

## Trigger contract

The API body contains only:

```json
{
  "schema_id": "invoice",
  "schema_version": 1
}
```

The backend must confirm that the document has a successful parse, the schema version exists and is `PRODUCTION`, and the schema use case matches the document use case. It then submits only trusted parameters: `document_id`, schema identity and authenticated requester identity.

## Job requirements

1. Load the exact schema registry row and verify its hash.
2. Select the latest successful parse run deterministically.
3. Call `ai_extract` version `2.1` in precision mode with citations and confidence scores enabled.
4. Pass only `ai_extract_schema_json` plus the versioned server-side instructions.
5. Persist the complete raw result before attempting to flatten it.
6. Treat a returned error as a failed run and retain the diagnostic without document text in application logs.
7. Flatten fields by walking the registered schema; do not duplicate a hard-coded list in the generic runner.
8. Resolve each returned citation ID against extraction metadata and persist page and bounding-box evidence.
9. Convert invoice dates and `DECIMAL(18,2)` amounts explicitly; preserve the original extracted value alongside the typed value.
10. Make the idempotency key `document_id + parse_run_id + schema_id + schema_version + extractor_version`.
11. A retry creates a new immutable run; it never overwrites an earlier result.
12. Generate SQL expressions only from validated registry content, using bound parameters or DataFrame literals where supported.

## Tests

- Preconditions for parse status, schema status and use-case match.
- Job submission with trusted parameters only.
- Representative `ai_extract` 2.1 result fixtures.
- Generic scalar-field flattening.
- Citation ID resolution, including missing citations.
- Confidence parsing and null handling.
- Decimal/date casts and preservation of raw values.
- Failed-run and retry history.
- Deterministic latest-successful view.
- SQL-injection and untrusted-identifier rejection.

## Demonstration

Run extraction for a parsed invoice, then show the completed run, typed values, confidence values and source-page references through the API or a minimal results panel.

## Rollback boundary

Reverting removes extraction triggers, job code and result APIs. Registered documents, parses and schemas remain usable; existing extraction rows are retained for audit.

## Progress statement

> We can now extract typed invoice data with confidence and source evidence, while retaining every raw result.

## Definition of done

- One representative invoice completes successfully end to end.
- Raw, generic and typed outputs reconcile to the same run.
- A failed extraction is visible and retryable.
- No validation decision is produced.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

