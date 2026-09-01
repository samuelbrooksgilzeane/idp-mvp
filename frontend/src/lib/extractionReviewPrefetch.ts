import type { ExtractionReview } from "../types";

type ExtractionRunSummary = {
  extraction_run_id: string;
  status: "RUNNING" | "EXTRACTED" | "FAILED";
};

const reviews = new Map<string, Promise<ExtractionReview>>();
const documentRuns = new Map<string, Promise<ExtractionRunSummary[]>>();

function requestJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  return fetch(url, signal ? { signal } : undefined).then(async (response) => {
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return (await response.json()) as T;
  });
}

/**
 * The review response is immutable for a completed run, so it is safe to share a request
 * started by a link hover with the detail route reached by the subsequent click.
 */
export function loadExtractionReview(
  extractionRunId: string,
  signal?: AbortSignal,
): Promise<ExtractionReview> {
  const existing = reviews.get(extractionRunId);
  if (existing) return existing;

  const request = requestJson<ExtractionReview>(
    `/api/extractions/${extractionRunId}/review`,
    signal,
  );
  reviews.set(extractionRunId, request);
  void request.catch(() => reviews.delete(extractionRunId));
  return request;
}

export function prefetchExtractionReview(extractionRunId: string): void {
  void loadExtractionReview(extractionRunId);
}

/** Warm the latest completed extraction visible from a document-registry row. */
export function prefetchDocumentExtractionReview(documentId: string): void {
  let runs = documentRuns.get(documentId);
  if (!runs) {
    runs = requestJson<unknown>(`/api/documents/${documentId}/extraction-runs`).then((payload) =>
      Array.isArray(payload) ? payload.filter(isExtractionRunSummary) : [],
    );
    documentRuns.set(documentId, runs);
    void runs.catch(() => documentRuns.delete(documentId));
  }
  void runs
    .then((history) => {
      const latest = history.find((run) => run.status === "EXTRACTED");
      if (latest) prefetchExtractionReview(latest.extraction_run_id);
    })
    .catch(() => undefined);
}

function isExtractionRunSummary(value: unknown): value is ExtractionRunSummary {
  if (!value || typeof value !== "object") return false;
  const run = value as Partial<ExtractionRunSummary>;
  return (
    typeof run.extraction_run_id === "string" &&
    (run.status === "RUNNING" || run.status === "EXTRACTED" || run.status === "FAILED")
  );
}
