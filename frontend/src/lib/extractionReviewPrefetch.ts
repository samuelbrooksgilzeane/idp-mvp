import type { ExtractionReview } from "../types";
import { RequestCache, cacheScope, canPrefetch, onScopeReset } from "./requestCache";

type Run = { extraction_run_id: string; status: string };
const reviews = new RequestCache<ExtractionReview>(20, 8_000_000, 60_000);
const documentRuns = new RequestCache<Run[]>(20, 200_000, 10_000);
onScopeReset(() => { reviews.clear(); documentRuns.clear(); });
let speculative = 0;
async function json<T>(url: string): Promise<T> {
  const response = await fetch(url, undefined);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export function loadExtractionReview(id: string, signal?: AbortSignal): Promise<ExtractionReview> {
  const shared = reviews.get(`${cacheScope()}:${id}`, () => json(`/api/extractions/${id}/review`));
  if (!signal) return shared;
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    void shared.then(value => { if (!signal.aborted) resolve(value); }, reject)
      .finally(() => signal.removeEventListener("abort", abort));
  });
}
export function invalidateDocumentReviews(documentId?: string) {
  if (documentId) documentRuns.delete(`${cacheScope()}:${documentId}`);
  else documentRuns.clear();
  reviews.clear();
}
function speculate(task: () => Promise<unknown>) {
  if (!canPrefetch() || speculative >= 2) return;
  speculative++;
  void task().catch(() => undefined).finally(() => { speculative--; });
}
export function prefetchExtractionReview(id: string) { speculate(() => loadExtractionReview(id)); }
export function prefetchDocumentExtractionReview(documentId: string) {
  speculate(async () => {
    const history = await documentRuns.get(`${cacheScope()}:${documentId}`, async () => {
      const payload = await json<unknown>(`/api/documents/${documentId}/extraction-runs`);
      return Array.isArray(payload) ? payload as Run[] : [];
    });
    const latest = history.find(run => run.status === "EXTRACTED");
    if (latest) await loadExtractionReview(latest.extraction_run_id);
  });
}
