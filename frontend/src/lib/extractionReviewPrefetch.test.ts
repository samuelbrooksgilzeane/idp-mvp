import { afterEach, expect, it, vi } from "vitest";
import { loadExtractionReview, invalidateDocumentReviews } from "./extractionReviewPrefetch";
afterEach(() => { invalidateDocumentReviews(); vi.unstubAllGlobals(); });
it("cancelling one consumer does not cancel the shared review transport", async () => {
  let resolve!: (value: unknown) => void;
  const fetchMock = vi.fn(() => new Promise(done => { resolve = done; }));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  const first = loadExtractionReview("shared", controller.signal);
  const second = loadExtractionReview("shared");
  controller.abort();
  await expect(first).rejects.toMatchObject({ name: "AbortError" });
  resolve({ ok: true, json: async () => ({ schema_id: "test" }) });
  expect(await second).toMatchObject({ schema_id: "test" });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
