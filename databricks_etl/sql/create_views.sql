-- Derived views over the governed tables.
-- These run after the column migrations, because a view can only project columns that
-- the retained tables already carry.

CREATE OR REPLACE VIEW IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_latest_successful_parses'
)
COMMENT 'Latest successful immutable parse attempt for each document'
AS
SELECT
  parse_run_id,
  document_id,
  content_sha256,
  parser_version,
  parsed,
  document_text,
  page_count,
  page_image_root,
  parse_error,
  status,
  requested_by,
  job_run_id,
  started_at,
  completed_at
FROM IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_parsed_documents'
)
WHERE status = 'SUCCESS'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY document_id ORDER BY completed_at DESC, parse_run_id DESC
) = 1;

CREATE OR REPLACE VIEW IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_latest_successful_extractions'
)
COMMENT 'Latest successful immutable extraction attempt for each document'
AS
SELECT
  extraction_run_id,
  document_id,
  parse_run_id,
  schema_id,
  schema_version,
  schema_hash,
  extractor_version,
  options,
  ai_result,
  error_message,
  status,
  requested_by,
  started_at,
  completed_at
FROM IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_extraction_runs'
)
WHERE status = 'EXTRACTED'
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY document_id ORDER BY completed_at DESC, extraction_run_id DESC
) = 1;

CREATE OR REPLACE VIEW IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_validation_summary'
)
COMMENT 'Validation result counts by immutable validation run and document'
AS
SELECT
  validation_run_id,
  extraction_run_id,
  document_id,
  COUNT(*) AS result_count,
  COUNT_IF(status = 'PASS') AS pass_count,
  COUNT_IF(status <> 'PASS') AS exception_count,
  MAX(created_at) AS completed_at
FROM IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_validation_results'
)
GROUP BY validation_run_id, extraction_run_id, document_id;

CREATE OR REPLACE VIEW IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_summary'
)
COMMENT 'Latest invoice candidate, billed-line reconciliation and validation outcome per document'
AS
WITH line_totals AS (
  SELECT
    extraction_run_id,
    COALESCE(invoice_index, 0) AS invoice_index,
    COUNT(line_number) AS line_item_count,
    SUM(amount) AS line_items_sum,
    SUM(tax) AS line_items_tax_sum
  FROM IDENTIFIER(
    :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_line_candidates'
  )
  GROUP BY extraction_run_id, COALESCE(invoice_index, 0)
),
latest_validations AS (
  SELECT extraction_run_id, document_status
  FROM IDENTIFIER(
    :catalog || '.' || :project_schema || '.' || :table_prefix || '_validation_runs'
  )
  WHERE status = 'COMPLETED'
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY extraction_run_id
    ORDER BY completed_at DESC, validation_run_id DESC
  ) = 1
)
SELECT
  documents.document_id,
  documents.file_name,
  documents.case_id,
  COALESCE(candidates.invoice_index, 0) AS invoice_index,
  candidates.invoice_number,
  candidates.invoice_date,
  candidates.seller_name,
  candidates.currency,
  COALESCE(line_totals.line_item_count, 0) AS line_item_count,
  line_totals.line_items_sum,
  candidates.total_amount,
  -- The same signed terms as the registered line_items_reconcile_to_total rule, so the
  -- reported delta cannot contradict the validation outcome beside it. A missing signed
  -- term is missing, never zero, so the delta is unknown rather than wrong.
  CASE
    WHEN line_totals.line_items_sum IS NULL
      OR line_totals.line_items_tax_sum IS NULL
      OR candidates.discount_amount IS NULL
      OR candidates.total_amount IS NULL THEN NULL
    ELSE line_totals.line_items_sum
         + line_totals.line_items_tax_sum
         - candidates.discount_amount
         - candidates.total_amount
  END AS reconciliation_delta,
  latest_validations.document_status,
  extractions.extraction_run_id
FROM IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_documents'
) AS documents
JOIN IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_latest_successful_extractions'
) AS extractions
  ON extractions.document_id = documents.document_id
JOIN IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_candidates'
) AS candidates
  ON candidates.extraction_run_id = extractions.extraction_run_id
LEFT JOIN line_totals
  ON line_totals.extraction_run_id = extractions.extraction_run_id
 -- Keyed by invoice as well as run, so one invoice never inherits another's lines.
 AND line_totals.invoice_index = COALESCE(candidates.invoice_index, 0)
LEFT JOIN latest_validations
  ON latest_validations.extraction_run_id = extractions.extraction_run_id;
