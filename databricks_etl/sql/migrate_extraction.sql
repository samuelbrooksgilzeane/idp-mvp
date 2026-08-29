-- Upgrade a pre-Commit-7 extraction-run table without rewriting retained rows.
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_extraction_runs'
      AND column_name = 'job_run_id'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_extraction_runs'
    ) ADD COLUMN job_run_id BIGINT
      COMMENT 'Databricks Job run identifier used for operational polling';
  END IF;
END;
