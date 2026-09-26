import type { DocumentStatus } from "../types";

export function documentStatusLabel(status: DocumentStatus): string {
  switch (status) {
    case "PARSE_QUEUED": return "Preparing · Waiting";
    case "PARSING": return "Preparing · Processing";
    case "PARSED": return "Ready to extract";
    case "PARSE_FAILED": return "Preparation failed";
    default: return status;
  }
}
