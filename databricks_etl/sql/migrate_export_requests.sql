-- Apply explicitly with trusted catalog/schema/prefix substitutions before enabling exports.
CREATE TABLE IF NOT EXISTS ${catalog}.${project_schema}.${table_prefix}_export_requests (
  export_id STRING NOT NULL, payload STRING NOT NULL
) USING DELTA;
CREATE TABLE IF NOT EXISTS ${catalog}.${project_schema}.${table_prefix}_export_members (
  export_id STRING NOT NULL, ordinal INT NOT NULL, extraction_run_id STRING NOT NULL
) USING DELTA;
