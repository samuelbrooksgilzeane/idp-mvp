# Commit 8 — Extraction Evidence UI

## Outcome

Users can inspect extracted fields and jump from any cited value to the exact supporting area of the PDF.

## Recommended commit

```text
feat(idp): link extracted fields to document evidence
```

## Scope

Implement:

- Extraction run selector and status display.
- Field table/cards with raw value, typed value, confidence and citation status.
- Field-to-page navigation.
- Citation bounding-box overlay in the existing viewer.
- Clear no-citation and extraction-error states.

Do not add validation or correction controls in this commit.

## Implementation requirements

1. Default to the latest successful extraction while allowing prior runs to be inspected.
2. Display the exact schema ID, version and hash associated with the selected run.
3. Selecting a cited field navigates to its page and highlights every associated citation box.
4. Visually and accessibly distinguish extraction citations from generic parse-element overlays.
5. Recalculate citation coordinates on zoom and resize using the same page-coordinate contract as the parse viewer.
6. Show confidence as model metadata, not as a claim that the field is correct.
7. Show a specific `No citation returned` state instead of silently omitting the evidence control.
8. Keep the source PDF and extraction side by side at normal desktop widths; support a usable stacked layout at narrow widths.
9. Keep all volume paths, signed URLs and storage credentials behind the backend.

## Tests

- Latest and historical extraction-run selection.
- Field rendering for string, date, decimal and null values.
- Schema-version provenance display.
- Citation click changes page and draws the correct box.
- Multiple citations for one field.
- Missing citation and missing page-image states.
- Overlay scaling under zoom and resize.
- Keyboard navigation and accessible non-colour status labels.
- Frontend build and existing end-to-end smoke tests.

## Demonstration

Select `total_amount`, show its value and confidence, click its evidence link, and highlight the printed total on the correct invoice page. Then show a field with no citation and its explicit warning.

## Rollback boundary

Reverting removes extraction-specific UI and citation interaction. Extraction jobs, APIs and all persisted evidence remain intact.

## Progress statement

> Users can now trace each extracted value directly back to its source in the PDF.

## Definition of done

- Evidence interaction works across representative invoice layouts.
- Historical runs remain inspectable.
- No validation or editing UI is present.
- Extraction milestone acceptance tests pass.
- Tag the verified commit `mvp-extraction`.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

