# Commit 4 — Document Parsing Pipeline

## Outcome

A registered PDF can be manually parsed into retained layout-aware `VARIANT` data and rendered page images. The UI shows processing status but not the page viewer yet.

## Recommended commit

```text
feat(idp): add idempotent document parsing workflow
```

## Scope

Implement:

- Parsing pipeline or parameterised parsing task.
- Job resource and manual trigger.
- `POST /api/documents/{id}/parse`.
- `GET /api/runs/{run_id}`.
- Parse-run history in document detail.
- Status transitions and polling.

## Processing logic

1. Validate the document is `UPLOADED` or eligible for retry.
2. Create `parse_run_id`; set document to `PARSING`.
3. Read the registered PDF only from its server-stored source path.
4. Call `ai_parse_document` pinned to version `2.0`.
5. Set `imageOutputPath` beneath the configured artifacts volume.
6. Use empty `descriptionElementTypes` for this invoice-focused MVP unless a tested requirement needs figure descriptions.
7. Persist the complete raw `parsed` result before deriving fields.
8. Derive text, page count, image references and parse error.
9. Mark the run `SUCCESS`/`FAILED` and document `PARSED`/`PARSE_FAILED`.
10. Do not delete or move the source PDF on failure.

## Idempotency

The identity key is:

```text
document_id + content_sha256 + parser_version
```

A retry creates a new run. The current parse view chooses the latest successful run deterministically.

## Tests

- Eligible-state checks.
- Successful status sequence.
- Parse failure sequence.
- Job trigger and polling adapter.
- Retry after failure.
- Retry after success does not overwrite history.
- Latest-successful view selection.
- Raw parse result is retained.
- Page images remain inside the configured artifacts volume.

## Demonstration

Trigger parsing from the document detail page and show the status moving from `UPLOADED` to `PARSING` to `PARSED`. Show the retained parse run metadata.

## Rollback boundary

Reverting removes the trigger and processing task. Existing source files, parse runs and page images remain.

## Progress statement

> We can now convert uploaded PDFs into retained, structured page and element data; visual inspection is next.

## Definition of done

- Representative invoices parse in the dev workspace.
- Failures are visible and retryable.
- No extraction schema or domain-field extraction is included.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

