-- Read-only UC functions for the document chat (Supervisor tools). Run after create_chat_views.
-- {object:<name>} is replaced with the backquoted catalog.schema.<prefix>_<name> by
-- databricks_etl/src/create_chat_functions.py; function bodies cannot use parameter markers.
-- Every function returns document_id and file_name so answers can name the source document.
-- Extracted values are model output, not approved truth. Deleted documents are excluded.

CREATE OR REPLACE FUNCTION {object:chat_document_fields}(
  doc_id STRING COMMENT 'Document id (UUID) or exact file name, for example invoice_17.pdf'
)
RETURNS TABLE (
  document_id STRING, file_name STRING, schema_id STRING, field_path STRING, value STRING,
  value_type STRING, confidence_score DOUBLE, validation_status STRING
)
COMMENT 'All extracted field values of one document, from its latest successful extraction per schema. Use to show or check the data points extracted from a document.'
RETURN
SELECT d.document_id, d.file_name, e.schema_id, f.field_path, f.value_string, f.field_type,
  f.confidence_score, f.validation_status
FROM {object:chat_documents} d
JOIN {object:chat_extractions} e ON e.document_id = d.document_id
JOIN {object:chat_fields} f
  ON f.extraction_run_id = e.extraction_run_id AND f.document_id = e.document_id
WHERE d.status <> 'DELETED' AND (d.document_id = doc_id OR d.file_name = doc_id)
ORDER BY e.schema_id, f.field_path;

CREATE OR REPLACE FUNCTION {object:chat_find_documents}(
  field_name STRING COMMENT 'Field name, alone or with its parents joined by _ or ., for example organization, payee_organization or payee.organization',
  value_contains STRING COMMENT 'Case-insensitive text the field value must contain'
)
RETURNS TABLE (
  document_id STRING, file_name STRING, case_id STRING, schema_id STRING, field_path STRING,
  value STRING
)
COMMENT 'Documents whose extracted field contains the given text. The field matches by its own name or by any trailing part of its path (array positions ignored). At most 200 rows.'
RETURN
SELECT d.document_id, d.file_name, d.case_id, e.schema_id, f.field_path, f.value_string
FROM {object:chat_documents} d
JOIN {object:chat_extractions} e ON e.document_id = d.document_id
JOIN {object:chat_fields} f
  ON f.extraction_run_id = e.extraction_run_id AND f.document_id = e.document_id
WHERE d.status <> 'DELETED'
  -- payee.organization and invoices[0].seller_name normalise to payee_organization and
  -- invoices_seller_name; a name matches the whole normalised path or a trailing part of it.
  AND endswith(
    concat('_', regexp_replace(lower(regexp_replace(f.field_path, '\\[[0-9]*\\]', '')), '[^a-z0-9]+', '_')),
    concat('_', regexp_replace(lower(field_name), '[^a-z0-9]+', '_'))
  )
  AND f.value_string ILIKE '%' || value_contains || '%'
ORDER BY d.file_name, f.field_path
LIMIT 200;

CREATE OR REPLACE FUNCTION {object:chat_invoices}(
  supplier STRING DEFAULT NULL COMMENT 'Case-insensitive part of the seller name; NULL for all',
  date_from DATE DEFAULT NULL COMMENT 'Earliest invoice date, inclusive; NULL for no limit',
  date_to DATE DEFAULT NULL COMMENT 'Latest invoice date, inclusive; NULL for no limit'
)
RETURNS TABLE (
  document_id STRING, file_name STRING, invoice_key STRING, invoice_number STRING,
  seller_name STRING, invoice_date DATE, invoice_date_text STRING, currency STRING,
  total DECIMAL(18, 2)
)
COMMENT 'One row per extracted invoice (a document may hold several, keyed invoices[n].). invoice_date is parsed from the extracted text and NULL when unparseable, and a date filter then excludes the invoice.'
RETURN
WITH invoice_fields AS (
  SELECT d.document_id, d.file_name, f.value_string,
    regexp_extract(f.field_path, '([A-Za-z_]+)$', 1) AS leaf,
    regexp_extract(f.field_path, '^(.*?)[A-Za-z_]+$', 1) AS invoice_key
  FROM {object:chat_documents} d
  JOIN {object:chat_extractions} e ON e.document_id = d.document_id
  JOIN {object:chat_fields} f
    ON f.extraction_run_id = e.extraction_run_id AND f.document_id = e.document_id
  WHERE d.status <> 'DELETED' AND e.schema_id = 'invoice'
    AND regexp_extract(f.field_path, '^(.*?)[A-Za-z_]+$', 1) RLIKE '^(invoices\\[[0-9]+\\]\\.)?$'
),
invoices AS (
  SELECT document_id, file_name, invoice_key,
    max(CASE WHEN leaf = 'invoice_number' THEN value_string END) AS invoice_number,
    max(CASE WHEN leaf = 'seller_name' THEN value_string END) AS seller_name,
    max(CASE WHEN leaf = 'invoice_date' THEN value_string END) AS invoice_date_text,
    max(CASE WHEN leaf = 'currency' THEN value_string END) AS currency,
    try_cast(max(CASE WHEN leaf = 'total' THEN value_string END) AS DECIMAL(18, 2)) AS total
  FROM invoice_fields
  GROUP BY document_id, file_name, invoice_key
),
dated AS (
  SELECT *, coalesce(
    try_to_date(invoice_date_text, 'd MMMM yyyy'), try_to_date(invoice_date_text, 'd-MMM-yyyy'),
    try_to_date(invoice_date_text, 'd MMM yyyy'), try_to_date(invoice_date_text, 'yyyy-MM-dd')
  ) AS invoice_date
  FROM invoices
)
SELECT document_id, file_name, invoice_key, invoice_number, seller_name, invoice_date,
  invoice_date_text, currency, total
FROM dated
WHERE (supplier IS NULL OR seller_name ILIKE '%' || supplier || '%')
  AND (date_from IS NULL OR invoice_date >= date_from)
  AND (date_to IS NULL OR invoice_date <= date_to)
ORDER BY invoice_date, file_name, invoice_key;

CREATE OR REPLACE FUNCTION {object:chat_invoice_totals}(
  supplier STRING DEFAULT NULL COMMENT 'Case-insensitive part of the seller name; NULL for all',
  date_from DATE DEFAULT NULL COMMENT 'Earliest invoice date, inclusive; NULL for no limit',
  date_to DATE DEFAULT NULL COMMENT 'Latest invoice date, inclusive; NULL for no limit'
)
RETURNS TABLE (
  seller_name STRING, currency STRING, invoice_count BIGINT, total DECIMAL(28, 2),
  missing_total_count BIGINT, document_ids ARRAY<STRING>, file_names ARRAY<STRING>
)
COMMENT 'Invoice totals summed per seller and currency (never across currencies), with the source documents. missing_total_count counts invoices whose total could not be read.'
RETURN
SELECT seller_name, currency, count(*), sum(total), count_if(total IS NULL),
  array_sort(collect_set(document_id)), array_sort(collect_set(file_name))
FROM {object:chat_invoices}(supplier, date_from, date_to)
GROUP BY seller_name, currency
ORDER BY seller_name, currency;

CREATE OR REPLACE FUNCTION {object:chat_case_documents}(
  case_ref STRING COMMENT 'Case id'
)
RETURNS TABLE (
  document_id STRING, file_name STRING, status STRING, uploaded_at TIMESTAMP
)
COMMENT 'Documents registered under one case. status is the processing state, not approval.'
RETURN
SELECT document_id, file_name, status, uploaded_at
FROM {object:chat_documents}
WHERE status <> 'DELETED' AND case_id = case_ref
ORDER BY uploaded_at;
