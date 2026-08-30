-- Additive migration for the generic (schema-agnostic) extraction result (generalized IDP
-- plan, section 4). Extends `_extracted_fields` with the record/path columns a recursive
-- result needs. Every new column is nullable so already-written invoice-projection rows
-- are untouched.
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_extracted_fields'
      AND column_name = 'record_id'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_extracted_fields'
    ) ADD COLUMNS (
      record_id STRING COMMENT 'The extracted_records row this field belongs to',
      schema_path STRING COMMENT 'Wildcard schema path (generic replacement for field_path)',
      instance_path STRING COMMENT 'Concrete instance path (generic replacement for field_path)',
      declared_type STRING COMMENT 'Declared schema type (generic replacement for field_type)',
      validation_status STRING COMMENT 'Optional deterministic validation outcome for this field',
      validation_message STRING COMMENT 'Optional human-readable validation explanation'
    );
  END IF;
END;
