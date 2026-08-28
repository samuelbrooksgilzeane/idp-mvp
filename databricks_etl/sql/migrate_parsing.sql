-- Upgrade an existing Commit 2 parse-run table without rewriting retained rows.
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_parsed_documents'
      AND column_name = 'content_sha256'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_parsed_documents'
    ) ADD COLUMN content_sha256 STRING
      COMMENT 'Source content identity used with document and parser version';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_parsed_documents'
      AND column_name = 'requested_by'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_parsed_documents'
    ) ADD COLUMN requested_by STRING
      COMMENT 'Authenticated application user that requested parsing';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_parsed_documents'
      AND column_name = 'job_run_id'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_parsed_documents'
    ) ADD COLUMN job_run_id BIGINT
      COMMENT 'Databricks Job run identifier used for operational polling';
  END IF;
END;
