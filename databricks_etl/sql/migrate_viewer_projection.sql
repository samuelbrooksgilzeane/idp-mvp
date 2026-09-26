-- Explicit opt-in migration; readiness manifest publishes only after all elements are stored.
CREATE TABLE IF NOT EXISTS ${catalog}.${project_schema}.${table_prefix}_parsed_page_manifest (
  parse_run_id STRING NOT NULL, payload STRING NOT NULL
) USING DELTA;
CREATE TABLE IF NOT EXISTS ${catalog}.${project_schema}.${table_prefix}_parsed_page_elements (
  parse_run_id STRING NOT NULL, page_id INT NOT NULL, payload STRING NOT NULL
) USING DELTA;
