# MVP Progress Tracker

Update this file in the same Git commit as each capability. It is the single concise source for implementation status; detailed requirements remain in the numbered commit specifications.

## Status rules

Use only:

- `NOT STARTED`
- `IN PROGRESS`
- `BLOCKED`
- `COMPLETE`

Mark a capability `COMPLETE` only when its definition of done, automated checks and demonstration have passed in the target development environment.

## Capability status

| Order | Capability | Status | Git SHA | Demonstrable result | Evidence / blocker |
|---:|---|---|---|---|---|
| 1 | Project foundation | COMPLETE | — | App and bundle build and validate | `chore(idp): scaffold deployable app and asset bundle`; `make check`; mock UI and proxied health verified locally |
| 2 | Data foundation | COMPLETE | `e75b7ee` + parsing migration in `9ce5f16` | Prefixed volumes, tables and views exist | Dev bundle validation passed; bootstrap runs `885944253718670` and `571208438976540` succeeded; `workspace.idp_mvp` contains two volumes, seven managed tables and three views |
| 3 | Upload and registry | COMPLETE | `c9043a8` + App deployment in `551bd1b` | PDFs can be uploaded and tracked | Two representative invoices uploaded through the deployed App and appeared in the SQL-backed registry; duplicate, identity and failure-path tests pass |
| 4 | Parsing pipeline | COMPLETE | `9ce5f16` + runtime fix in `627625c` | PDFs become page and element data | Dev Job run `585474568087236` succeeded with `ai_parse_document` 2.0; the invoice reached `PARSED` with one page after a visible, retryable failure; parsing tests pass |
| 5 | Parsed-document viewer | COMPLETE | `91e111c` | Parsed pages and labelled overlays are inspectable | `make check` passes with 44 backend and 8 frontend tests; local browser QA covered zoom, filtering and element inspection; the deployed API returned one page, 11 elements and a 204,525-byte JPEG; stakeholder visual acceptance completed on 2026-08-28. |
| 6 | Schema registry and viewer | COMPLETE | `727041c` | Exact extraction schema is visible | `invoice_v1` registered once after bootstrap runs `983917610156087` and `810202042098230`; live APIs returned 8 governed fields and 2 rules without raw schema JSON; local browser QA and `make check` pass. |
| 7 | Extraction pipeline | COMPLETE | `72e0db3` | Typed fields, confidence and citations exist | `make check` passes with 66 backend and 11 frontend tests; dev bundle deployed; document `d0ed9896` extracted end to end via extraction Job runs `222795856966902` and `817490446741663`; both immutable runs are `EXTRACTED` and visible; the latest run `13e7ac76-093f-481d-8360-42375bc8bda8` reconciles raw `ai_result`, 8 generic field rows, and the typed candidate to schema hash `b02b3c20…640`, with confidence `1.0`, resolved page-0 citation boxes, typed decimals `29.87`/`888.55 EUR`, and typed `invoice_date 2011-07-28` from preserved raw `28-Jul-2011` |
| 8 | Extraction evidence UI | COMPLETE | `41ee11e` | Fields link to supporting PDF regions | `make check` passes with 67 backend and 16 frontend tests; deployed to Databricks dev and verified against invoice `d0ed9896`: run selector defaults to the latest successful run and inspects prior runs; each field shows raw + typed value, model confidence, and citation status; a new per-run API `GET /documents/{id}/extractions/{run_id}` serves historical runs; all six returned citations are within the 1653×2336 page-image pixel bounds and co-locate with the same parse-element boxes the viewer already renders, confirming citation-overlay alignment |
| 9 | Deterministic validation | COMPLETE | `5eec8b4` | Technical and arithmetic exceptions are identified | `make check` passes with 108 backend and 20 frontend tests; deployed to Databricks dev and demonstrated live: a balanced invoice reached `VALIDATED_PASS` with 43/43 checks passing, and the same invoice pushed out of tolerance reached `REVIEW_REQUIRED` with a BLOCKING reconciliation failure ("out by 360, beyond the configured tolerance of 0.01"). Structural validators are generic; business rules are declarative closed-set config in the registered schema. `invoice_v2` registered with explicit signed terms; `invoice_v1` hash `b02b3c20…640` unchanged. No LLM is called. |
| 9a | Line-item extraction | COMPLETE | `0dbe759` | Repeated line items are extracted and reconciled | Agreed insertion into the pack. `make check` passes with 119 backend and 21 frontend tests. `ai_extract` nested arrays extract all five line items from the real invoice `d0ed9896` with citations, summing to 881.11; `line_items_reconcile_to_total` FAILS as BLOCKING out by 37.31 (851.24 computed against a stated 888.55) and `line_items_sum_to_subtotal` degrades to WARNING/UNCERTAIN because that invoice states no subtotal. A balanced synthetic invoice reached `VALIDATED_PASS` with 85/85 checks. `invoice_v3` registered; v1 and v2 unchanged. |
| 10 | LLM validation | NOT STARTED | — | Eligible ambiguities receive a grounded second check | — |
| 11 | Evaluation and demo | NOT STARTED | — | Benchmark and stakeholder demo are repeatable | — |

## Milestones

| Milestone | Required commits | Acceptance tag | Status |
|---|---|---|---|
| Parsing MVP | 1–5 | `mvp-parsing` | COMPLETE |
| Extraction MVP | 6–8 | `mvp-extraction` | COMPLETE |
| Validated MVP | 9–11 | `mvp-validation` | IN PROGRESS |

Do not mark a milestone complete until every required capability is complete and the tagged commit has passed the milestone acceptance checks.

## Progress update template

Use this for stakeholder updates:

```text
Current milestone: <Parsing / Extraction / Validation>
Completed: <capabilities completed since the last update>
Can demonstrate now: <observable user journey>
In progress: <one capability only where practical>
Next: <next numbered capability>
Blocker or decision needed: <none, or a specific owner and decision>
Evidence: <environment, Git SHA, test/job run, screenshot or MLflow run>
```

## Rollback log

Record actual rollbacks; do not rewrite history in this file.

| Date | Reverted SHA | Capability | Reason | Data retained | Follow-up |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

## Example stakeholder statement

> We have completed Commit 5, so the Parsing MVP is demonstrable: a user can upload a PDF, run parsing and inspect every page and detected element. Extraction has not started. The next increment makes the extraction schema visible before any fields are produced.
