-- Additive upload-batch foundation. Run before deploying the durable intake API.
-- Payloads contain metadata only, never file bytes or storage credentials.
CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_upload_batches'
) (batch_id STRING NOT NULL, payload STRING NOT NULL)
USING DELTA TBLPROPERTIES ('delta.isolationLevel' = 'Serializable');

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_upload_items'
) (
  batch_id STRING NOT NULL, client_file_id STRING NOT NULL,
  ordinal INT NOT NULL, state STRING NOT NULL, revision INT NOT NULL, payload STRING NOT NULL
)
USING DELTA TBLPROPERTIES ('delta.isolationLevel' = 'Serializable');
-- Logical identities are enforced by deterministic IDs, MERGE, optimistic revisions,
-- conflict retries and duplicate detection, not informational PRIMARY KEY constraints.
-- No schedule or inference resource is created by this migration.

-- Durable preparation work and immutable bounded dispatch manifests.
CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_work_items'
) (
  work_item_id STRING NOT NULL, state STRING NOT NULL,
  next_eligible_at STRING NOT NULL, revision INT NOT NULL, payload STRING NOT NULL
)
USING DELTA TBLPROPERTIES ('delta.isolationLevel' = 'Serializable');

CREATE TABLE IF NOT EXISTS IDENTIFIER(
  :catalog || '.' || :project_schema || '.' || :table_prefix || '_work_dispatches'
) (
  dispatch_id STRING NOT NULL, state STRING NOT NULL,
  revision INT NOT NULL, payload STRING NOT NULL
)
USING DELTA TBLPROPERTIES ('delta.isolationLevel' = 'Serializable');
