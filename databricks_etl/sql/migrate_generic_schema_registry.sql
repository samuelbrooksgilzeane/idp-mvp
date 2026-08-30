-- Additive migration for the user-editable schema registry (generalized IDP plan, section 2).
-- Adds the DRAFT/PUBLISHED/RETIRED lifecycle's optional description and publish timestamp to
-- an already-deployed `_schema_registry` table. Both columns are nullable, so every existing
-- governed PRODUCTION row is untouched and its schema_hash is unaffected.
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_schema_registry'
      AND column_name = 'description'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_schema_registry'
    ) ADD COLUMN description STRING
      COMMENT 'Optional human-readable summary shown in the schema editor list';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_schema_registry'
      AND column_name = 'published_at'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_schema_registry'
    ) ADD COLUMN published_at TIMESTAMP
      COMMENT 'Timestamp when a DRAFT version became an immutable PUBLISHED version';
  END IF;
END;
