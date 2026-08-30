-- Admit several invoices per document without rewriting retained candidate rows.
-- An existing row describes the only invoice its document stated, so it belongs at index 0.
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_invoice_candidates'
      AND column_name = 'invoice_index'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_candidates'
    ) ADD COLUMN invoice_index INT
      COMMENT 'Which invoice within the run; 0 when the document states one';
    UPDATE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_candidates'
    ) SET invoice_index = 0 WHERE invoice_index IS NULL;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM IDENTIFIER(:catalog || '.information_schema.columns')
    WHERE table_schema = :project_schema
      AND table_name = :table_prefix || '_invoice_line_candidates'
      AND column_name = 'invoice_index'
  ) THEN
    ALTER TABLE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_line_candidates'
    ) ADD COLUMN invoice_index INT
      COMMENT 'The invoice this line belongs to; lines number from 1 within it';
    UPDATE IDENTIFIER(
      :catalog || '.' || :project_schema || '.' || :table_prefix || '_invoice_line_candidates'
    ) SET invoice_index = 0 WHERE invoice_index IS NULL;
  END IF;
END;
