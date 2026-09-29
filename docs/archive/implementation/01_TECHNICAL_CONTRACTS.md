# Shared Technical Contracts

These decisions apply to every implementation commit. Change them only through an explicit architecture decision record.

## Environment boundary

The workspace provides:

- One existing permitted Unity Catalog catalog.
- Permission to create one project Unity Catalog schema inside it.
- Permission to create multiple volumes inside that schema.
- Permission to create Jobs.
- A SQL warehouse.
- Serverless compute and/or Lakeflow Spark Declarative Pipelines.

Never create a catalog. Create the configured project schema only if it does not exist.

All tables and views use:

```text
<catalog>.<project_schema>.<table_prefix>_<object_name>
```

Recommended prefixes:

```text
dev  = idp_dev
prod = idp
```

“Project schema” means the single Unity Catalog database namespace. “Extraction schema” means versioned JSON passed to `ai_extract`; it is not a Unity Catalog schema.

## Volumes

Preferred layout:

```text
/Volumes/<catalog>/<project_schema>/<source_volume>/incoming/
/Volumes/<catalog>/<project_schema>/<source_volume>/quarantine/
/Volumes/<catalog>/<project_schema>/<artifacts_volume>/page_images/
```

If only one volume is permitted, preserve the same directory boundaries inside it.

## Trusted configuration

Asset Bundle variables:

- `catalog`
- `project_schema`
- `table_prefix`
- `source_volume_name`
- `artifacts_volume_name`
- `warehouse_id`
- `validation_endpoint`
- `evaluation_experiment`
- `app_name`

The browser must never provide catalog, project schema, table, volume, warehouse, endpoint or arbitrary SQL identifiers.

## Authentication

- Use the Databricks App service principal for backend Files, Jobs, SQL and model calls in the MVP.
- Record the authenticated application user as `uploaded_by` and `requested_by` from trusted forwarded identity.
- Never expose tokens or internal credential-bearing URLs.
- Do not log raw document text, full prompts or extracted sensitive values by default.

## Data objects

All objects live in `<catalog>.<project_schema>` and begin with `<table_prefix>_`.

### `documents`

Minimum columns:

```text
document_id STRING
case_id STRING
template_id STRING
use_case STRING
source_path STRING
file_name STRING
file_size BIGINT
content_sha256 STRING
selected_schema_id STRING
selected_schema_version INT
status STRING
uploaded_by STRING
uploaded_at TIMESTAMP
updated_at TIMESTAMP
```

### `parsed_documents`

```text
parse_run_id STRING
document_id STRING
parser_version STRING
parsed VARIANT
document_text STRING
page_count INT
page_image_root STRING
parse_error VARIANT
status STRING
started_at TIMESTAMP
completed_at TIMESTAMP
```

### `schema_registry`

```text
schema_id STRING
schema_version INT
display_name STRING
use_case STRING
ai_extract_schema_json STRING
instructions STRING
field_policy_json STRING
document_rule_json STRING
schema_hash STRING
status STRING
created_by STRING
created_at TIMESTAMP
```

### `extraction_runs`

```text
extraction_run_id STRING
document_id STRING
parse_run_id STRING
schema_id STRING
schema_version INT
schema_hash STRING
extractor_version STRING
options MAP<STRING, STRING>
ai_result VARIANT
error_message STRING
status STRING
requested_by STRING
started_at TIMESTAMP
completed_at TIMESTAMP
```

### `extracted_fields`

```text
extraction_run_id STRING
document_id STRING
field_path STRING
field_type STRING
value VARIANT
value_string STRING
confidence_score DOUBLE
citation_ids ARRAY<INT>
citations VARIANT
extraction_error STRING
```

### `invoice_candidates`

```text
case_id STRING
document_id STRING
source_path STRING
template_id STRING
invoice_number STRING
invoice_date DATE
seller_name STRING
subtotal DECIMAL(18,2)
discount_amount DECIMAL(18,2)
tax_amount DECIMAL(18,2)
total_amount DECIMAL(18,2)
currency STRING
extraction_run_id STRING
schema_version INT
```

Candidate data is not approved data.

### `validation_results`

```text
validation_run_id STRING
extraction_run_id STRING
document_id STRING
rule_id STRING
field_path STRING
validator_type STRING
severity STRING
status STRING
message STRING
actual_value STRING
expected_value STRING
suggested_value STRING
evidence STRING
validator_version STRING
prompt_hash STRING
created_at TIMESTAMP
```

Reserve `RECONCILIATION` as a future validator type for invoice-to-GL matching.

## Workflow states

```text
UPLOADED
PARSING
PARSED
PARSE_FAILED
EXTRACTING
EXTRACTED
EXTRACT_FAILED
VALIDATING
VALIDATED_PASS
REVIEW_REQUIRED
```

Transitions must be validated server-side. Each retry creates a new run record. Views expose the latest successful run; history remains immutable.

## Core API surface

```text
POST /api/documents
GET  /api/documents
GET  /api/documents/{id}
POST /api/documents/{id}/parse
GET  /api/documents/{id}/pages
GET  /api/documents/{id}/pages/{page}/image
GET  /api/documents/{id}/elements
GET  /api/schemas
GET  /api/schemas/{id}/versions/{version}
POST /api/documents/{id}/extract
GET  /api/documents/{id}/extractions/latest
POST /api/documents/{id}/validate
GET  /api/documents/{id}/validations/latest
GET  /api/documents/{id}/validation-summary
GET  /api/runs/{run_id}
```

Use Pydantic request/response models and stable application error codes.

## Version contracts

- `ai_parse_document`: pin output version `2.0`.
- `ai_extract`: use `2.1`.
- Extraction scalar values are read from `response.<field>.value`.
- Preserve `citation_ids`, `confidence_score` and `metadata.citations`.
- Financial validation uses `Decimal`, never binary floating point.
- LLM validation cannot mutate extracted values.

## Reference-code boundary

Use the Databricks Intelligent Document Processing accelerator as the processing/deployment reference. Reimplement the second repository's page viewer, navigation and bounding-box interaction patterns. Do not adopt its destructive cleanup, runtime configuration APIs, wildcard CORS or monolithic backend.

Confirm organisational permission before copying either repository. Otherwise implement the described patterns cleanly.

