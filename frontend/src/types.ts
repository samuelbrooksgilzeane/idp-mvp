export type HealthResponse = {
  status: "ok";
  mode: "mock" | "databricks";
  application_name: string;
  configuration: Record<string, boolean>;
};

export type DocumentStatus =
  | "UPLOADED"
  | "PARSING"
  | "PARSED"
  | "PARSE_FAILED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "EXTRACT_FAILED"
  | "VALIDATING"
  | "VALIDATED_PASS"
  | "REVIEW_REQUIRED";

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
  status: "RUNNING" | "SUCCESS" | "FAILED";
  page_count: number | null;
  parse_error: Record<string, unknown> | unknown[] | null;
  requested_by: string;
  started_at: string;
  completed_at: string | null;
};

export type ApiError = { error: { code: string; message: string } };

export type Notice = { kind: "success" | "error"; message: string } | null;
