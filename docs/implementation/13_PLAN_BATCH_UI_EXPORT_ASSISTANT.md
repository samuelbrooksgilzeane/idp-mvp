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
| G | Repeated entities: nested schema v4 | **COMPLETE** | `feat/13-batch-processing` — verified live end to end |
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
- **Arithmetic reconciliation is not a contract**: the `arithmetic_reconciliation` and
  `comparison` rules were removed from the contract at v4. The arithmetic convention varies
  between invoice formats, and encoding one made the governed result fragile. The reported
  `reconciliation_delta` and the billed lines are still exported, so a reviewer performs the
  check in Excel where the convention can vary per invoice.
- **Users do not author schemas**: schemas stay source-controlled and allow-listed, so a
  registered hash keeps meaning something. Reviewed and rejected as more chaos than value.
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

## Section G — Repeated entities per document (schema v4)

One PDF may contain several invoices. Under a flat schema the model returns one of them and the
others are silently absent from the export, which is worse than an error: the result validates
cleanly while under-reporting. `invoice_v4` states `invoices[*]`, each with its own
`line_items[*]`, so the count of invoices is data rather than an assumption.

**Verified in dev on 2026-08-30.** A two-page document holding three invoices — two stacked on
page 1, one on page 2, from three sellers in three currencies — extracted under v4 as three
separate instances, each with the right lines (2, 1 and 3) and every field correct at confidence
1.0. Citations resolved to the correct regions, including the two invoices sharing page 1, which
were cited at distinct vertical positions on that page. Validation returned 229 observations, all
passing, and each declared document rule produced one observation per invoice.

Three changes were needed beyond the manifest:

- **Rule paths expand over instances.** `required_fields`, `range`, `format` and `allowed_values`
  resolved exact paths only, so a rule naming `invoices[*].currency` would have reported every
  invoice's value missing and failed wrongly. They now expand a wildcard into every extracted
  instance; a wildcard matching nothing counts as missing rather than vacuously satisfied.
  `comparison` refuses wildcards outright, because pairing an instance with its own sibling is
  not something that rule type can express.
- **Required-ness is owned by the rule, not the policy.** `FieldPolicy.required` is not read by
  any per-leaf validator, so dropping the `required_fields` rule would silently remove the check.
  Worth revisiting: the policy is the more natural owner now that policies are per-instance.
- **The typed projection refuses shapes it cannot describe.** `build_candidate` reads top-level
  leaves and writes one row per document. Under v4 it would have written a row of nulls that the
  summary would show as a blank invoice, so it now returns nothing and records the extracted
  fields alone.

**The typed projection now describes repeated invoices.** Both candidate tables carry an
`invoice_index`, added by a guarded migration that places every retained row at index 0, which is
what it has always described. The Databricks and mock projections were rewritten together, and the
summary view, both reporting paths and the export join lines to their own invoice.

Verified live on 2026-08-30: the three-invoice document reports three rows — `INV-A-9001` GBP with
2 lines, `INV-B-4402` EUR with 1, `INV-C-7783` USD with 3 — each reconciling to zero, all
`VALIDATED_PASS`, with six line rows in the workbook naming the invoice each belongs to. The ten
existing documents project unchanged, `d0ed9896` still reporting `-37.31`.

Three traps were closed on the way, each of which would have been silently wrong rather than
broken:

- **Fan-out.** Joining lines on the extraction run alone gives every invoice in a document every
  other invoice's lines, inflating each sum without raising anything. The view, both reporting
  paths and the export now key on `(extraction_run_id, invoice_index)`, and a test asserts the
  per-invoice counts and sums directly.
- **Silent truncation.** `get_candidate` selected `LIMIT 1`, so validation's duplicate check would
  only ever have considered the first invoice's identity. It is now `list_candidates`, and every
  invoice's identity is checked.
- **Ordering.** Views were created in the same script as the tables, before the migration that adds
  the column they project, so the first bootstrap failed. The four views now live in
  `create_views.sql` and run as the last bootstrap task, because a view can only project columns
  the retained tables already carry.

**Still not solved by this**: a second document type needs its own projection or a manifest-driven
one, and workflow state remains per document, so individual invoices cannot be approved separately.

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
