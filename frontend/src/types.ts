export type HealthResponse = {
  status: "ok";
  mode: "mock" | "databricks";
  application_name: string;
  configuration: Record<string, boolean>;
};

export type DocumentStatus =
  | "UPLOADED"
  | "PARSE_QUEUED"
  | "PARSING"
  | "PARSED"
  | "PARSE_FAILED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "EXTRACT_FAILED"
  | "VALIDATING"
  | "VALIDATED_PASS"
  | "REVIEW_REQUIRED"
  | "DELETED";

export type DocumentRecord = {
  document_id: string;
  case_id: string | null;
  template_id: string;
  use_case: string;
  file_name: string;
  file_size: number;
  content_sha256: string;
  status: DocumentStatus;
  uploaded_by: string;
  uploaded_at: string;
  updated_at: string;
};

export type ParseRun = {
  parse_run_id: string;
  document_id: string;
  parser_version: "2.0";
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
  page_count: number | null;
  parse_error: Record<string, unknown> | unknown[] | null;
  requested_by: string;
  started_at: string;
  completed_at: string | null;
};

export type ApiError = { error: { code: string; message: string } };

export type Notice = { kind: "success" | "error"; message: string } | null;

export type ExtractionRunStatus = "RUNNING" | "EXTRACTED" | "FAILED";

/** One row of the run-centric Results list (`GET /api/extractions`). */
export type ExtractionRunSummary = {
  extraction_run_id: string;
  document_id: string;
  document_name: string;
  case_id: string | null;
  schema_id: string;
  schema_version: number;
  schema_display_name: string;
  status: ExtractionRunStatus;
  started_at: string;
  completed_at: string | null;
  is_latest: boolean;
};

export type ExtractionRunPage = {
  items: ExtractionRunSummary[];
  next_cursor: string | null;
};

export type GenericExtractionRun = {
  extraction_run_id: string;
  document_id: string;
  parse_run_id: string;
  schema_id: string;
  schema_version: number;
  schema_hash: string;
  extractor_version: "2.1";
  options: Record<string, string>;
  error_message: string | null;
  status: ExtractionRunStatus;
  requested_by: string;
  job_run_id: number | null;
  started_at: string;
  completed_at: string | null;
};

/** `GET /api/extractions/{run_id}`: the hierarchical result exactly as `ai_extract` returned
 * it, for any schema shape. */
export type GenericExtractionResult = {
  run: GenericExtractionRun;
  schema_id: string;
  schema_version: number;
  root_mode: "SINGLE_RECORD" | "REPEATED_RECORDS";
  result: Record<string, unknown>;
};

export type GenericRecord = {
  record_id: string;
  parent_record_id: string | null;
  schema_path: string;
  instance_path: string;
  ordinal: number | null;
};

type GenericFieldCitationBox = { coord: number[]; page_id: number };
export type GenericFieldCitation = { id: number; bbox: GenericFieldCitationBox[] };

export type GenericField = {
  record_id: string;
  schema_path: string;
  instance_path: string;
  field_name: string;
  declared_type: string;
  value: unknown;
  value_string: string | null;
  confidence_score: number | null;
  citation_ids: number[];
  citations: GenericFieldCitation[];
  validation_status: string | null;
  validation_message: string | null;
};

/** `GET /api/extractions/{run_id}/records`: the flat record/field tables behind a review grid
 * or an export. */
export type GenericExtractionRecords = {
  run: GenericExtractionRun;
  schema_id: string;
  schema_version: number;
  root_mode: "SINGLE_RECORD" | "REPEATED_RECORDS";
  records: GenericRecord[];
  fields: GenericField[];
};

/** One read model for rendering extraction values beside the source document. */
export type ExtractionReview = {
  run: GenericExtractionRun;
  document: DocumentRecord;
  schema_id: string;
  schema_version: number;
  root_mode: "SINGLE_RECORD" | "REPEATED_RECORDS";
  result: Record<string, unknown>;
  fields: GenericField[];
  field_policies: Record<
    string,
    { confidence_threshold: number; citation_required: boolean }
  >;
};

export type AppConfig = {
  project_name: string;
  chat_enabled?: boolean;
};

export type ChatMessage = {
  seq: number;
  role: "user" | "assistant";
  text: string;
  tools?: string[];
  citations?: string[];
  elapsed_seconds?: number;
  created_at: string;
};

export type ConversationSummary = { conversation_id: string; title: string; updated_at: string };
