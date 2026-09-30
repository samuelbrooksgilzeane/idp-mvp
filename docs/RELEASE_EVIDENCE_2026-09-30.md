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

| `5244305` fix: demo papercuts | Parser and extractor Jobs now **queue** a run started while another is active instead of skipping it (Databricks skips it when `max_concurrent_runs` is 1 and queueing is off, which the app showed as a failure). Documents list keeps refreshing while documents are extracting or validating. The selection bar defaults to the schema last used in this browser, else the most recently published schema. Two regression tests added. |

| `be8df8b` fix(ui): background refresh; Genie result views | The document list no longer flashes to "Loading registry…" every 5 s: a background refetch keeps the current rows (`useCursorPage` reports `loading` only when the URL has no data yet). Same fix removes the flash on Results refresh. `prepare_genie_bundle.py` now requires `--catalog/--project-schema/--table-prefix` and adds the four `<prefix>_genie_*` views to the space definition if missing, preserving exported curation and other sources. |
| `fa24422` fix: citation spotlight; deleted-document review | Clicking an extracted value now fades element boxes, dims the rest of the page and scrolls the cited region into view (the box was drawn but lost among 24 similarly coloured element overlays). Reviewing a retained result whose document was deleted returned HTTP 500 (`DocumentResponse` rejected `DELETED`); this affected every invoice run in dev. Regression test added. |

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

## Follow-up deployment (papercut fixes)

`make check`: 252 backend and 85 frontend tests passed. Genie overlay re-exported first. Plan: only
`queue.enabled` false→true on the parser and extractor Jobs, plus app source files. Deployment
`01f1bc608256171da1298bb6378073e4` SUCCEEDED; app RUNNING; health 200; served `index-C49QaGhw.js`
matches the local build. Both Jobs read back `queue.enabled: true`, `max_concurrent_runs: 1`,
`for_each` concurrency 3. This is now the current deployment.

## Genie data sources and latest deployment

Plan for `be8df8b` showed the Genie space updated in place with only `data_sources.tables` going from
none to `workspace.idp_mvp.idp_dev_genie_{documents,extractions,fields,records}`; instructions unchanged.
Read back from the live space afterwards: four tables, instructions intact. The next plan (for `fa24422`)
showed no Genie change, confirming the views are not duplicated on re-export.

Current deployment `01f1bc638992197ab9ac800e6a7c6bd6` (includes `fa24422`): app RUNNING. Live checks:
review endpoint 200 for three invoice runs that previously returned 500 and for an SF 2823 run; a
browser session on `/results/9c2fa309…` showed the spotlighted citation after clicking a value.
Citations for tabular data cite the whole table element (model granularity), not the single cell.

## Behaviour confirmed from code (not a live test)

- Parse and extraction work runs in Databricks Jobs, and each document task records its own
  terminal state. Refreshing or leaving the page does not stop it. A Job that dies before recording
  a result is reconciled to failed the next time anyone opens that document or batch.
- Parallelism applies within one Job run, i.e. to multi-document "Prepare/Extract selected".
  Each Job allows one active run, so separately started single-document runs are not parallel.
  Since `5244305` a second start waits in the Job queue (shown as in progress) instead of being skipped.
- Unfinished browser uploads stop on refresh or tab close; reselecting the files resumes and skips completed ones.

## Findings

- Fixed in `be8df8b`: the Genie space had no data sources (`"data_sources": {}`); the four result
  views are now attached. The source volume and content search remain a manual step in the space's
  Sources tab (not exposed in the documented space definition).
- Fixed in `5244305`: the Documents list did not refresh while extracting; the selection bar defaulted
  to the first schema alphabetically; concurrent single-document runs could be skipped.
- The local mock SQLite registry predates a column the current code writes (`invoice_candidates.invoice_index`),
  so local mock extraction fails. The deployed app is unaffected.

## Not done

No user-run smoke test of the new UI in a browser, no colleague access test, no live upload or
inference, no 1,000-file test.
