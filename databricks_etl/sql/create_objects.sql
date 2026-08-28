-- Trusted parameters are supplied only by the Asset Bundle deployment configuration.
-- IDENTIFIER keeps object names parameterized without accepting arbitrary browser input.

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:catalog || '.' || :project_schema)
COMMENT 'Governed schema for immutable IDP workflow records and project volumes';

CREATE VOLUME IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :source_volume_name
)
COMMENT 'Source PDF intake and quarantine storage for the IDP application';

CREATE VOLUME IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :artifacts_volume_name
)
COMMENT 'Derived page images and retained processing artifacts for the IDP application';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_documents'
) (
  document_id STRING COMMENT 'Stable server-generated document identifier',
  case_id STRING COMMENT 'Optional external case identifier retained for future workflows',
  template_id STRING COMMENT 'Document template selected at ingestion',
  use_case STRING COMMENT 'Configured document-processing use case',
  source_path STRING COMMENT 'Trusted Unity Catalog volume path for the source PDF',
  file_name STRING COMMENT 'Sanitized original file name for display',
  file_size BIGINT COMMENT 'Source file size in bytes',
  content_sha256 STRING COMMENT 'SHA-256 digest used for duplicate detection',
  selected_schema_id STRING COMMENT 'Extraction schema identifier selected for this document',
  selected_schema_version INT COMMENT 'Extraction schema version selected for this document',
  status STRING COMMENT 'Server-validated document workflow state',
  uploaded_by STRING COMMENT 'Authenticated application user that uploaded the document',
  uploaded_at TIMESTAMP COMMENT 'Timestamp when file storage and registration completed',
  updated_at TIMESTAMP COMMENT 'Timestamp of the latest document state transition'
)
USING DELTA
COMMENT 'Document registry and current server-validated workflow state';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_parsed_documents'
) (
  parse_run_id STRING COMMENT 'Immutable parse attempt identifier',
  document_id STRING COMMENT 'Registered source document identifier',
  parser_version STRING COMMENT 'Pinned parser output contract version',
  parsed VARIANT COMMENT 'Retained layout-aware parser response',
  document_text STRING COMMENT 'Retained parser text used by downstream extraction',
  page_count INT COMMENT 'Number of pages reported by the parser',
  page_image_root STRING COMMENT 'Trusted artifacts-volume root for rendered page images',
  parse_error VARIANT COMMENT 'Structured failure details when parsing fails',
  status STRING COMMENT 'Immutable parse-run terminal or active state',
  started_at TIMESTAMP COMMENT 'Timestamp when the parse attempt started',
  completed_at TIMESTAMP COMMENT 'Timestamp when the parse attempt reached a terminal state'
)
USING DELTA
COMMENT 'Immutable parse attempts with retained parser output and errors';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_schema_registry'
) (
  schema_id STRING COMMENT 'Stable extraction schema identifier',
  schema_version INT COMMENT 'Monotonically versioned extraction contract',
  display_name STRING COMMENT 'Human-readable schema name',
  use_case STRING COMMENT 'Use case that may select this extraction schema',
  ai_extract_schema_json STRING COMMENT 'Exact JSON contract passed to ai_extract',
  instructions STRING COMMENT 'Versioned extraction instructions',
  field_policy_json STRING COMMENT 'Versioned field-level policy JSON',
  document_rule_json STRING COMMENT 'Versioned document-level rule JSON',
  schema_hash STRING COMMENT 'Content hash covering the complete extraction contract',
  status STRING COMMENT 'Schema lifecycle state',
  created_by STRING COMMENT 'Authenticated identity that registered the schema version',
  created_at TIMESTAMP COMMENT 'Timestamp when the immutable schema version was registered'
)
USING DELTA
COMMENT 'Versioned and auditable extraction schemas';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_extraction_runs'
) (
  extraction_run_id STRING COMMENT 'Immutable extraction attempt identifier',
  document_id STRING COMMENT 'Registered source document identifier',
  parse_run_id STRING COMMENT 'Successful parse attempt used by extraction',
  schema_id STRING COMMENT 'Extraction schema identifier used by this attempt',
  schema_version INT COMMENT 'Extraction schema version used by this attempt',
  schema_hash STRING COMMENT 'Hash of the exact extraction contract used',
  extractor_version STRING COMMENT 'Pinned extraction implementation version',
  options MAP<STRING, STRING> COMMENT 'Trusted extraction options supplied by the server',
  ai_result VARIANT COMMENT 'Retained ai_extract response',
  error_message STRING COMMENT 'Sanitized extraction failure detail',
  status STRING COMMENT 'Immutable extraction-run terminal or active state',
  requested_by STRING COMMENT 'Authenticated application user that requested extraction',
  started_at TIMESTAMP COMMENT 'Timestamp when the extraction attempt started',
  completed_at TIMESTAMP COMMENT 'Timestamp when the extraction attempt reached a terminal state'
)
USING DELTA
COMMENT 'Immutable extraction attempts and retained model responses';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_extracted_fields'
) (
  extraction_run_id STRING COMMENT 'Immutable extraction attempt identifier',
  document_id STRING COMMENT 'Registered source document identifier',
  field_path STRING COMMENT 'Canonical path in the extraction schema',
  field_type STRING COMMENT 'Declared type from the extraction schema',
  value VARIANT COMMENT 'Typed extracted value retained without approval semantics',
  value_string STRING COMMENT 'Display-safe string representation of the value',
  confidence_score DOUBLE COMMENT 'Model-provided confidence score when available',
  citation_ids ARRAY<INT> COMMENT 'Identifiers of supporting citations',
  citations VARIANT COMMENT 'Retained source-grounding citation metadata',
  extraction_error STRING COMMENT 'Field-level extraction error when no value was produced'
)
USING DELTA
COMMENT 'Flattened extracted values with confidence and source evidence';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_candidates'
) (
  case_id STRING COMMENT 'Optional external case identifier',
  document_id STRING COMMENT 'Registered source document identifier',
  source_path STRING COMMENT 'Trusted source PDF volume path',
  template_id STRING COMMENT 'Document template selected at ingestion',
  invoice_number STRING COMMENT 'Candidate invoice number, not an approved value',
  invoice_date DATE COMMENT 'Candidate invoice date, not an approved value',
  seller_name STRING COMMENT 'Candidate seller name, not an approved value',
  subtotal DECIMAL(18,2) COMMENT 'Candidate invoice subtotal',
  discount_amount DECIMAL(18,2) COMMENT 'Candidate discount amount',
  tax_amount DECIMAL(18,2) COMMENT 'Candidate tax amount',
  total_amount DECIMAL(18,2) COMMENT 'Candidate invoice total',
  currency STRING COMMENT 'Candidate ISO currency code',
  extraction_run_id STRING COMMENT 'Immutable extraction attempt that produced the candidate',
  schema_version INT COMMENT 'Extraction schema version used for the candidate'
)
USING DELTA
COMMENT 'Invoice-shaped candidate data that has not received human approval';

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_validation_results'
) (
  validation_run_id STRING COMMENT 'Immutable validation attempt identifier',
  extraction_run_id STRING COMMENT 'Extraction attempt evaluated by validation',
  document_id STRING COMMENT 'Registered source document identifier',
  rule_id STRING COMMENT 'Stable validation rule identifier',
  field_path STRING COMMENT 'Canonical extracted field path evaluated by the rule',
  validator_type STRING COMMENT 'Validator category, including reserved future RECONCILIATION',
  severity STRING COMMENT 'Configured validation severity',
  status STRING COMMENT 'Validation outcome for this rule result',
  message STRING COMMENT 'Human-readable validation explanation',
  actual_value STRING COMMENT 'Observed value rendered for audit',
  expected_value STRING COMMENT 'Expected value rendered for audit',
  suggested_value STRING COMMENT 'Non-mutating suggested value when applicable',
  evidence STRING COMMENT 'Grounded evidence used by the validator',
  validator_version STRING COMMENT 'Version of the deterministic or model validator',
  prompt_hash STRING COMMENT 'Hash of the prompt contract for model validation',
  created_at TIMESTAMP COMMENT 'Timestamp when the immutable validation result was created'
)
USING DELTA
COMMENT 'Immutable technical, arithmetic, model, and future reconciliation results';

CREATE OR REPLACE VIEW IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_latest_successful_parses'
)
COMMENT 'Latest successful immutable parse attempt for each document'
AS
SELECT
  parse_run_id,
  document_id,
  parser_version,
  parsed,
  document_text,
  page_count,
  page_image_root,
  parse_error,
  status,
  started_at,
  completed_at
FROM IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_parsed_documents'
)
WHERE status = 'PARSED'
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
