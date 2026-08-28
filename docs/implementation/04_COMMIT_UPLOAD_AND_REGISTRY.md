# Commit 3 — PDF Upload and Document Registry

## Outcome

Users can upload PDFs, see registered documents and detect duplicates. Files are not parsed yet.

## Recommended commit

```text
feat(idp): add secure pdf upload and document registry
```

## Scope

Implement:

- `POST /api/documents` using `multipart/form-data`.
- `GET /api/documents` and `GET /api/documents/{id}`.
- Service-principal Files API adapter.
- Filename sanitisation and server-generated storage name.
- File size/type validation.
- SHA-256 duplicate identity.
- Registry insert and status display.
- Upload panel and document list.

## Required behaviour

1. Accept PDF only, verified by extension and MIME/signature checks.
2. Stream uploads; do not convert PDFs to base64 in the browser.
3. Upload only beneath the configured `incoming/` directory.
4. Never accept a volume path from the request.
5. Never delete an existing file to make an upload succeed.
6. Create a stable `document_id` and record `case_id`, `template_id`, `use_case`, user and timestamps.
7. Detect duplicates before creating another active document.
8. Return stable error codes such as `UNSUPPORTED_FILE_TYPE`, `FILE_TOO_LARGE` and `DOCUMENT_DUPLICATE`.
9. Set status to `UPLOADED` only after the file and registry row are both committed successfully.
10. Handle partial failures explicitly; do not report a successful upload when registration failed.

## Tests

- Valid PDF upload.
- Multiple-file upload.
- Non-PDF rejection.
- Oversized-file rejection.
- Path traversal filename rejection/sanitisation.
- Duplicate content with the same filename.
- Duplicate content with a different filename.
- Files API failure.
- Registry write failure.
- User identity recording.
- Browser cannot select an arbitrary storage path.

## Demonstration

Upload two invoices, show them in the document list, then upload one again and show the duplicate explanation.

## Rollback boundary

Reverting removes upload routes and UI controls. It does not delete already uploaded PDFs or registry rows.

## Progress statement

> We can securely ingest, identify and track PDFs; the documents are not yet being interpreted.

## Definition of done

- Upload and list work in the development app.
- Duplicate behaviour is deterministic.
- No parse/extract code is included.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

