import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExtractionBatch } from "./useExtractionBatch";

const status = { batch_id: "batch", state: "ENQUEUED", total: 2, validated: 2, counts: { RUNNING: 2 }, terminal: false };
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
const saved = () => localStorage.setItem("idp:extraction-batch:project", JSON.stringify({ batchId: "batch", pending: null }));
beforeEach(() => { localStorage.clear(); vi.spyOn(document, "hidden", "get").mockReturnValue(false); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("durable extraction progress", () => {
  it("restores progress and fetches only one member page", async () => {
    saved();
    const fetcher = vi.fn(async (url: string) => url.includes("/items")
      ? json({ items: [{ document_id: "doc", ordinal: 0, state: "RUNNING" }], next_cursor: 49 })
      : json(status));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useExtractionBatch(true, "project", vi.fn()));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    expect(fetcher).toHaveBeenCalledWith("/api/extraction-batches/batch/items?after=-1&limit=50", expect.anything());
    act(() => result.current.next());
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/extraction-batches/batch/items?after=49&limit=50", expect.anything()));
  });

  it("replays the same request identity after a lost response and refresh", async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("response lost"));
    vi.stubGlobal("fetch", fetcher);
    const first = renderHook(() => useExtractionBatch(true, "project", vi.fn()));
    await act(() => first.result.current.start(["doc"], "invoice", 3));
    const original = JSON.parse(fetcher.mock.calls[0][1].body);
    first.unmount();
    fetcher.mockImplementation(async (_url: string, options?: RequestInit) => options?.method === "POST"
      ? json(status) : json({ ...status, items: [], next_cursor: null }));
    const second = renderHook(() => useExtractionBatch(true, "project", vi.fn()));
    expect(second.result.current.pending).not.toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1); // Refresh does not silently resubmit work.
    await act(() => second.result.current.retrySubmission());
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(original);
    expect(second.result.current.batchId).toBe("batch");
  });

  it("pauses hidden polling and stops after terminal status", async () => {
    vi.useFakeTimers(); saved();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const completed = vi.fn();
    const fetcher = vi.fn(async (url: string) => url.includes("/items")
      ? json({ items: [], next_cursor: null })
      : json({ ...status, terminal: true, state: "COMPLETE", counts: { SUCCEEDED: 2 } }));
    vi.stubGlobal("fetch", fetcher);
    renderHook(() => useExtractionBatch(true, "project", completed));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(fetcher).not.toHaveBeenCalled();
    hidden.mockReturnValue(false);
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(completed).toHaveBeenCalledTimes(1);
    const calls = fetcher.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(fetcher).toHaveBeenCalledTimes(calls);
  });
});
