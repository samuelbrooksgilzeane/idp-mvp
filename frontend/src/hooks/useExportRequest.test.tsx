import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useExportRequest } from "./useExportRequest";

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
it("restores an ambiguous submission and replays the same request identity", async () => {
  const bodies: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      bodies.push(String(init.body));
      if (bodies.length === 1) throw new Error("network");
    }
    return { ok: true, json: async () => ({ export_id: "one", state: "SUCCEEDED", selected_count: 1, runs_processed: 1,
      download_url: "/api/export-requests/one/download" }) };
  }));
  const first = renderHook(() => useExportRequest("test"));
  await act(() => first.result.current.start(["run"]));
  first.unmount();
  const restored = renderHook(() => useExportRequest("test"));
  expect(restored.result.current.pending).toBeTruthy();
  await act(() => restored.result.current.retry());
  await waitFor(() => expect(restored.result.current.status?.state).toBe("SUCCEEDED"));
  expect(bodies[1]).toBe(bodies[0]);
});
