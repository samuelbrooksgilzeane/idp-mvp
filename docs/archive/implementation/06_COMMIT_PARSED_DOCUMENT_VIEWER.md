# Commit 5 — Parsed-Document Viewer

## Outcome

Users can inspect every rendered PDF page and overlay the text/table elements produced by parsing.

## Recommended commit

```text
feat(idp): add parsed document page and element viewer
```

## Scope

Implement:

- `GET /api/documents/{id}/pages`.
- `GET /api/documents/{id}/pages/{page}/image`.
- `GET /api/documents/{id}/elements?page_id=&type=`.
- Page selector, next/previous controls and zoom.
- Client-side bounding-box overlay.
- Element-type filters.
- Parse-error and missing-image states.

## Implementation requirements

1. Stream page-image bytes through the authenticated backend.
2. Do not expose internal storage URLs or tokens.
3. Return only data belonging to the requested registered document.
4. Render boxes from `ai_parse_document` coordinates relative to the displayed image.
5. Recalculate scale on resize and zoom.
6. Distinguish text, table, title and other element types accessibly; colour cannot be the only indicator.
7. Keep page/image loading incremental rather than returning all page bytes at once.
8. Reimplement the useful viewer patterns from the visual reference repo; do not import its monolithic page or backend.

## Tests

- Page metadata mapping.
- Image streaming and missing-image response.
- Cross-document access protection.
- Bounding-box scaling at multiple image sizes.
- Page navigation.
- Element filtering.
- Empty and partial parse states.
- Frontend build and accessibility checks used by the repository.

## Demonstration

Open a parsed invoice, navigate between pages, filter to table elements and show an element overlay matching the source content.

## Rollback boundary

Reverting removes viewer routes and UI. Parsing and all stored page images remain intact.

## Progress statement

> Users can now visually verify exactly what the parser read from each PDF page.

## Definition of done

- Viewer works for every representative invoice template.
- Coordinates remain aligned under resize/zoom.
- Parsing milestone acceptance tests pass.
- Tag the verified commit `mvp-parsing`.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

