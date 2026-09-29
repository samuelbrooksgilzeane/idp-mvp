import type { DocumentStatus } from "../types";

const LABELS: Record<string, string> = {
  UPLOADED: "Uploaded",
  PARSE_QUEUED: "Queued",
  PARSING: "Preparing",
  PARSED: "Ready to extract",
  PARSE_FAILED: "Preparation failed",
  EXTRACTING: "Extracting",
  EXTRACTED: "Extracted",
  EXTRACT_FAILED: "Extraction failed",
  VALIDATING: "Validating",
  VALIDATED_PASS: "Validated",
  REVIEW_REQUIRED: "Needs review",
  DELETED: "Deleted",
};

export function documentStatusLabel(status: DocumentStatus): string {
  return LABELS[status] ?? status;
}

export type StageState = "done" | "active" | "failed" | "review" | "pending";

/** Upload → prepare → extract → validate, derived only from the document's own status. */
export function documentStages(status: DocumentStatus): StageState[] {
  switch (status) {
    case "PARSE_QUEUED":
    case "PARSING": return ["done", "active", "pending", "pending"];
    case "PARSED": return ["done", "done", "pending", "pending"];
    case "PARSE_FAILED": return ["done", "failed", "pending", "pending"];
    case "EXTRACTING": return ["done", "done", "active", "pending"];
    case "EXTRACTED": return ["done", "done", "done", "pending"];
    case "EXTRACT_FAILED": return ["done", "done", "failed", "pending"];
    case "VALIDATING": return ["done", "done", "done", "active"];
    case "VALIDATED_PASS": return ["done", "done", "done", "done"];
    case "REVIEW_REQUIRED": return ["done", "done", "done", "review"];
    default: return ["done", "pending", "pending", "pending"];
  }
}
