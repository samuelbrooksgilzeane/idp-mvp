-- Run after generic extraction migrations. No inference or raw PDF exposure.
-- Read-only sources for the document chat's UC functions. Older views from the retired chat space stay until Batch 8.
CREATE OR REPLACE VIEW IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_documents')
COMMENT 'One row per registered document; workflow status is not approval'
AS SELECT document_id, case_id, file_name, file_size, status, uploaded_at
FROM IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_documents');

CREATE OR REPLACE VIEW IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_extractions')
COMMENT 'Latest successful extraction per document and schema, across versions; retained model output is not approved truth'
AS SELECT extraction_run_id, document_id, parse_run_id, schema_id, schema_version, completed_at
FROM IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_extraction_runs')
WHERE status = 'EXTRACTED'
QUALIFY ROW_NUMBER() OVER (
 PARTITION BY document_id, schema_id ORDER BY completed_at DESC, extraction_run_id DESC
) = 1;

CREATE OR REPLACE VIEW IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_records')
COMMENT 'Record tree for latest successful extractions; historical runs without generic projections may be absent'
AS SELECT r.* FROM IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_extracted_records') r
JOIN IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_extractions') e
ON r.run_id = e.extraction_run_id AND r.document_id = e.document_id;

CREATE OR REPLACE VIEW IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_fields')
COMMENT 'Extracted field instances, not documents; missing legacy projections are not zero values'
AS SELECT f.extraction_run_id, f.document_id, f.record_id, f.schema_path, f.instance_path,
 f.declared_type, f.value_string, f.confidence_score, f.validation_status, f.validation_message,
 f.citation_ids, f.extraction_error,
 -- Concrete path and type, set on every row; schema_path/instance_path are NULL for legacy rows.
 f.field_path, f.field_type
FROM IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_extracted_fields') f
JOIN IDENTIFIER(:catalog || '.' || :project_schema || '.' || :table_prefix || '_chat_extractions') e
ON f.extraction_run_id = e.extraction_run_id AND f.document_id = e.document_id;
