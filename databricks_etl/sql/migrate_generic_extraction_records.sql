-- Additive migration for the generic (schema-agnostic) extraction result (generalized IDP
-- plan, section 4). Introduces `extracted_records` -- one row per document root, singleton
-- nested object, or repeated-array item, generalizing the invoice-only candidate tables.
--
-- NOTE ON THE CURRENT APPLICATION: as of this change, the running app computes the generic
-- record/field tree on demand from the already-retained `ai_result` and schema rather than
-- writing through to these tables (see `idp_app.services.generic_results`), so this migration
-- is schema scaffolding for the governed warehouse rather than a live write path yet. Wiring
-- `ExtractionJobRunner` to also populate these tables at completion time is the natural next
-- step and is called out in the handoff notes; nothing here removes or alters existing rows.

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_extracted_records'
) (
  run_id STRING COMMENT 'Extraction attempt that produced this record',
  document_id STRING COMMENT 'Registered source document identifier',
  record_id STRING COMMENT 'Deterministic identifier derived from run_id + instance_path',
  parent_record_id STRING COMMENT 'The containing record, or NULL for the document root',
  schema_path STRING COMMENT 'Wildcard schema path, e.g. invoices[].line_items[]',
  instance_path STRING COMMENT 'Concrete instance path, e.g. invoices[0].line_items[2]',
  ordinal INT COMMENT 'Position within a repeated array, or NULL for a singleton record'
)
USING DELTA
COMMENT 'Generic recursive extraction record tree: the document root, every singleton nested
object, and every repeated array item, for any extraction schema shape';
