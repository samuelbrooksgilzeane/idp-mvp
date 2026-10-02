-- Additive migration: the extraction run that owns each document's status (its EXTRACTING
-- claim, or the extraction its extracted/validated status describes). Claims, completions and
-- validation write the document only while their run owns it. The backfill names each
-- existing document's latest run, which is the one its current status came from.
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_documents'
      AND column_name = 'extraction_run_id'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_documents'
    ) ADD COLUMN extraction_run_id STRING
      COMMENT 'Extraction run that owns the status: its claim, or the extraction it describes';
  END IF;

  MERGE INTO IDENTIFIER(
    :catalog || '.' || :project_schema || '.' || :table_prefix || '_documents'
  ) AS documents
  USING (
    SELECT document_id, max_by(extraction_run_id, started_at) AS extraction_run_id
    FROM IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_extraction_runs'
    )
    GROUP BY document_id
  ) AS latest
  ON documents.document_id = latest.document_id AND documents.extraction_run_id IS NULL
  WHEN MATCHED THEN UPDATE SET documents.extraction_run_id = latest.extraction_run_id;
END;
