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
| 5 | Parsed-document viewer | NOT STARTED | — | Parsed pages and overlays are inspectable | — |
| 6 | Schema registry and viewer | NOT STARTED | — | Exact extraction schema is visible | — |
| 7 | Extraction pipeline | NOT STARTED | — | Typed fields, confidence and citations exist | — |
| 8 | Extraction evidence UI | NOT STARTED | — | Fields link to supporting PDF regions | — |
| 9 | Deterministic validation | NOT STARTED | — | Technical and arithmetic exceptions are identified | — |
| 10 | LLM validation | NOT STARTED | — | Eligible ambiguities receive a grounded second check | — |
| 11 | Evaluation and demo | NOT STARTED | — | Benchmark and stakeholder demo are repeatable | — |

## Milestones

| Milestone | Required commits | Acceptance tag | Status |
|---|---|---|---|
| Parsing MVP | 1–5 | `mvp-parsing` | NOT STARTED |
| Extraction MVP | 6–8 | `mvp-extraction` | NOT STARTED |
| Validated MVP | 9–11 | `mvp-validation` | NOT STARTED |

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
