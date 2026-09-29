# Release evidence — 30 September 2026

Verifier: Claude Code (agent). No PDF uploads, real-document inference, capacity workload, indexing
or recovery-schedule activation were performed. Target unchanged from the
[29 September record](RELEASE_EVIDENCE_2026-09-29.md): profile `idp-mvp`, workspace
`https://dbc-97e4a372-40b1.cloud.databricks.com`, app `idp-mvp-dev`, `workspace.idp_mvp`, prefix `idp_dev`.

## Branch and commits

Branch `feat/dark-blue-ui`, created from `feat/project-genie-lifecycle` at `c4ea9e8`. Not pushed.

| Commit | Contents |
| --- | --- |
| `4fc7a2e` chore: snapshot working tree before dark blue UI | Commits, unchanged, the working tree that was deployed on 29 September (previously uncommitted): the backend fixes listed in that record (idempotent missing-file deletion retry, atomic mock parse completion, `work_batches` in the governed inventory, schema-save SQL reduction, viewer/extraction input changes) with their tests; frontend upload-progress and viewer fixes; the release docs (`RELEASE_CHECKLIST`, `RELEASE_EVIDENCE_2026-09-29`, `RELEASE_RUNBOOK`, `CAPACITY_TEST_PROCEDURE`, `EXPORT_BENCHMARK`, `DEPLOYMENT_NOTES`), the move of older docs into `docs/archive/`, the frontend production build, `scripts/benchmark_exports.py`, and `output/` (sample PDFs and the design review: current-app screenshots, UX questions and green/blue/navy mockups). A secret scan of the untracked files found nothing; the gitignored Genie export was not included. |
| `32476d5` feat(ui): dark theme with brand blue accents; 3-way parse/extract parallelism | Dark theme with the brand blue #0067B1 accent (#003463 in the brand mark). Sidebar navigation, breadcrumb bar and compact headings replace the light header, runtime panel and footer. Documents page: 1–4 workflow guide (step 2 wording follows the automatic-preparation flag), horizontal upload card with an indeterminate activity bar, per-row stage track derived from status, plain-language status labels ("Ready to extract", "Needs review", …) and a floating select-schema-and-extract bar. "Parse" renamed "Prepare" in the UI. Results, Ask Genie and Schemas restyled; no static or placeholder metrics were added. All stylesheet colours converted to theme tokens. Bundle defaults: `parse_concurrency` 1→3, `extraction_concurrency` 1→3, `combined_inference_concurrency` 2→6. Seven frontend tests updated for the new copy only. |

## Local validation

`make check` passed: 252 backend tests, 83 frontend tests, Python/frontend lint, mypy, TypeScript,
production build and offline configuration validation. The UI was reviewed page by page in mock
mode against the design mockups (`output/design-review/`).

## Deployment

- Genie overlay re-exported from the existing space `01f1ba8f44851508b81fcc9c9a013451` immediately before planning.
- Bundle plan: 0 add, 3 change, 0 delete, 4 unchanged. App environment values all unchanged
  (identical old/new); parser and extractor changes were only `for_each` concurrency 1→3 and the
  combined budget 2→6. No Genie change.
- Deploy: 82 files uploaded, 7 obsolete workspace files removed (source files only).
- Activation: deployment `01f1bc5e24d710a9a997e9d6b2b5b197` SUCCEEDED; app RUNNING, compute ACTIVE.
  Previous deployment `01f1bc532ec01c13a89d8c0a68ccd4c2` (29 September) is the rollback reference.
- Verified afterwards: authenticated `/api/health` 200; served `index-DiXeEfOn.js` / `index-BqzbDyHw.css`
  match the local build (CSS SHA-256 identical); parser Job `1021425465418844` and extractor Job
  `948342095836775` report concurrency 3 on both their batch and manifest tasks.
- Feature flags unchanged: automatic preparation, bulk extraction and bulk export `false`;
  viewer projections `true`; dispatcher recovery schedule PAUSED.

## Behaviour confirmed from code (not a live test)

- Parse and extraction work runs in Databricks Jobs, and each document task records its own
  terminal state. Refreshing or leaving the page does not stop it. A Job that dies before recording
  a result is reconciled to failed the next time anyone opens that document or batch.
- Parallelism applies within one Job run, i.e. to multi-document "Prepare/Extract selected".
  Each Job allows one active run with queueing disabled, so separately started single-document runs
  are not parallel. Whether a second concurrent start is rejected by Databricks is not yet verified live.
- Unfinished browser uploads stop on refresh or tab close; reselecting the files resumes and skips completed ones.

## Findings

- **The Genie space has no data sources.** The exported definition contains `"data_sources": {}`.
  The embed loads, but Genie cannot answer questions about extracted results until the project's
  structured views are attached. See the demo plan in the [checklist](RELEASE_CHECKLIST.md).
- The Documents list refreshes on its own only while documents are preparing, not while extracting.
- The selection bar defaults to the first schema alphabetically, not the invoice schema.
- The local mock SQLite registry predates a column the current code writes (`invoice_candidates.invoice_index`),
  so local mock extraction fails. The deployed app is unaffected.

## Not done

No user-run smoke test of the new UI in a browser, no colleague access test, no live upload or
inference, no 1,000-file test.
