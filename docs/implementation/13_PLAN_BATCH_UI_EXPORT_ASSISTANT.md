# Working Plan — Batch, Multi-Page UI, Export and Conversational Layer

This plan is an **agreed insertion** into the numbered implementation pack, not a replacement for
it. Commits 1–9 of the pack are complete. This plan covers the work that turns a working pipeline
into something demonstrable, then rejoins the pack at Commit 10 (LLM validation) and Commit 11
(evaluation and demo).

Sections are lettered to avoid colliding with the pack's numbering.

| Section | Outcome | Status | Branch / commits |
|---|---|---|---|
| A | Multi-page UI, source beside values | **COMPLETE** | `feat/11-multi-page-ui` — `58cc981`, `7b37402`, `beda63b` |
| B | Typed line-item candidate table | **COMPLETE** | `feat/12-typed-line-candidates` — `60ccb00` |
| C | Batch parse and extract | **COMPLETE** | `feat/13-batch-processing` — `45f001a` |
| D | Summary view, XLSX export, case filter | **COMPLETE** | `feat/13-batch-processing` — verified live in dev |
| E | LLM validation and Knowledge Assistant | **NOT STARTED** (blocked, see below) | — |
| F | Set-based batch engine | **DEFERRED** by decision | — |

Branches are a **linear stack**: each is built on the one above it, and
`feat/13-batch-processing` contains everything. None have been merged to `main`.

## Decisions already taken

These were settled with the project owner. Do not silently revisit them.

- **Routing**: `react-router-dom` with real URLs, plus a FastAPI fallback so deep links survive a
  refresh.
- **Batch engine**: Databricks Jobs `for_each`. **It defaults to concurrency 1, i.e. sequential**,
  so concurrency is always set explicitly from the `batch_concurrency` deployment variable
  (default 16). Scoped to the working set of under ~100 files.
- **Concurrency control**: a bundle variable fixed per environment. The browser never influences
  Databricks compute load.
- **Export**: an XLSX workbook with two sheets, Summary and Line items, including the
  reconciliation delta and validation outcome. A summary alone cannot be checked; the line detail
  is what makes the result verifiable in Excel.
- **Case filtering**: included, but scoped to *filtering only*. `case_id` is already captured at
  upload and stored on `documents` and `invoice_candidates`, so this is a query parameter and a
  dropdown, not a new concept. No case CRUD.
- **Typed values in the UI**: the extraction table deliberately shows raw extracted value,
  model confidence and evidence. The typed projection column was **removed** on request; the typed
  data is still stored and queryable. Do not reintroduce the column without asking.
- **Set-based engine (F)**: deferred. At ~100 documents it saves only a few minutes for a
  substantial rewrite. Revisit when batches reach the high hundreds.

## Section D — Summary view, XLSX export, case filter (implemented locally)

- Add an `invoice_summary` view to `databricks_etl/sql/create_objects.sql` joining `documents`,
  the latest successful extraction, `invoice_candidates` and aggregated
  `invoice_line_candidates`. Expose: file name, case ID, invoice number, invoice date, seller,
  currency, `line_item_count`, `line_items_sum`, stated `total_amount`,
  **`reconciliation_delta`**, and the latest validation `document_status`. The export reads this
  view so the aggregation exists in exactly one place.
- `GET /api/exports/invoices.xlsx?case_id=…` streaming an **openpyxl** workbook:
  - *Summary* — one row per invoice, with delta and validation outcome.
  - *Line items* — one row per line, carrying document ID and invoice number so it joins back.
  - Volume paths and internal identifiers stay server-side, as everywhere else.
- Case filtering as a query parameter on the documents list, the summary and the export, with a
  dropdown of distinct case IDs plus "All cases".
- Fill in `frontend/src/pages/ResultsPage.tsx`, which is currently an intentional placeholder.

Evidence on 2026-08-29: `make check` passes with 134 backend and 32 frontend tests; API tests
open the generated workbook and verify both sheets, join keys, delta, validation outcome, case
scope and omission of trusted paths/internal run identifiers. Browser QA covered desktop and 390px
responsive layouts.

**Verified live in dev.** Bootstrap run `735102880444788` created the view through the documented
two-pass deploy; the deployed App serves the filtered results and a real two-sheet workbook; the
representative invoice reports its `-37.31` delta; and case filtering was demonstrated on two newly
uploaded invoices under `CASE-ALPHA` and `CASE-BETA` (batch parse `227813517718902`, batch extract
`907357470375810`).

One defect was found and fixed while verifying: the view computed the delta as
`sum(amount) - discount - total`, omitting the `sum(line tax)` term that the registered
`line_items_reconcile_to_total` rule includes. Every invoice in dev stated zero or no line tax, so
the two agreed by coincidence. On an invoice carrying line tax the export reported `-300.00` beside
`VALIDATED_PASS`. The report now uses the rule's signed terms, treats an unstated line tax as
missing rather than zero, and a test pins the two together. Full detail, plus the finding that
replacing a governed view revokes the App's Unity Catalog grant, is in `docs/PROJECT_CONTEXT.md`.

Section B already makes the aggregation a plain `GROUP BY`. This query is verified working
against dev today:

```sql
SELECT c.invoice_number,
       COUNT(l.line_number)                                        AS line_items,
       SUM(l.amount)                                               AS line_items_sum,
       c.total_amount                                              AS stated_total,
       SUM(l.amount) - c.discount_amount - c.total_amount          AS reconciliation_delta
FROM   workspace.idp_mvp.idp_dev_invoice_candidates c
JOIN   workspace.idp_mvp.idp_dev_invoice_line_candidates l
  ON   l.extraction_run_id = c.extraction_run_id
GROUP BY c.invoice_number, c.total_amount, c.discount_amount, c.extraction_run_id
```

It returns `INV/06-92/543 | 5 | 881.11 | 888.55 | -37.31` for the representative invoice.

## Section E — LLM validation and the conversational layer

Two separable pieces. **Both are blocked on infrastructure the project owner must provide.**

- **Commit 10 of the pack (source-grounded LLM validation)** exactly as
  `11_COMMIT_LLM_VALIDATION.md` specifies. `validation_results` already reserves `prompt_hash`
  and `validator_type`, so **no table change**. `FieldPolicy` needs an optional `llm_validate`
  flag — optional keeps registered schema hashes stable.
  **Blocker: `validation_endpoint` is still deployed as the literal string `unused`. A Databricks
  model serving endpoint must exist before this can be verified live.**
- **Knowledge Assistant source table.** Requirements confirmed from Databricks documentation: a
  `content` column of `BINARY` or `STRING`; a `metadata` or `_metadata` column of `STRUCT` type
  containing exactly `file_path` (string), `file_name` (string, with extension), `file_size`
  (long) and `file_modification_time` (timestamp); and the table must be a **streaming table or
  have Change Data Feed enabled**. Up to 10 knowledge sources; files over 100 MB or 500 pages are
  skipped.

  Add a derived `knowledge_documents` table rather than altering `parsed_documents`.
  `parsed_documents` holds **one row per parse attempt**, so pointing the assistant at it directly
  would index superseded parses alongside current ones, and the file metadata lives on
  `documents`, not on the parse row. A derived table keeps the immutable audit trail intact and
  gives the assistant exactly the shape it requires.

## Section F — Set-based batch engine (deferred, recorded)

Replace N task iterations with one statement over many rows, letting Spark parallelise the model
calls itself:

```sql
SELECT document_id, TO_JSON(ai_extract(parsed, :schema_json, options => map(...)))
FROM   parsed_documents
WHERE  parse_run_id IN (<the batch>)
```

Per-document errors stop being task failures and become **queryable data**, since `ai_extract`
returns an `error_message` per row. The work is real: the single-document guard queries in
`extract_document.py` (run lookup, schema-hash verification, document state, latest-parse
selection) all become joins over the batch, and the Python flattening must handle N results.

Trigger for doing it: batches consistently in the high hundreds, where the gap widens to roughly
80 minutes versus 20–30 at 1000 documents. Section C deliberately keeps the API and UI
engine-agnostic, so this is a contained backend change.

## Out of scope

S3/volume path ingestion (revisit alongside F), case management beyond filtering, correction and
approval workflows, and user-authored validation rules.
