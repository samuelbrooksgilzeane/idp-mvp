import { afterEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { UploadTransferManager, type UploadItem } from "./useUploadBatch";

function file(name: string) { return new File(["%PDF-small"], name, { type: "application/pdf", lastModified: 123 }); }
function reply(payload: unknown, status = 200) { return { ok: status < 400, status, json: async () => payload } as Response; }
function server(upload: (id: string, items: UploadItem[]) => Promise<Response>) {
  let items: UploadItem[] = [];
  let manifest: unknown;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input.toString();
    if (url === "/api/upload-batches") {
      const body = JSON.parse(init!.body as string);
      manifest = body;
      if (!items.length) items = body.files.map((entry: UploadItem, ordinal: number) => ({ ...entry, ordinal,
        state: "QUEUED", document_id: null, attempts: 0, error_code: null,
        error_message: null, retryable: true, updated_at: "2026-09-26T00:00:00Z" }));
      return reply({ batch_id: "batch-1", items });
    }
    if (url === "/api/documents") return upload((init!.body as FormData).get("client_file_id") as string, items);
    if (url.endsWith("/transport-failure")) return reply({});
    if (url.includes("/items?")) return reply({ items, next_cursor: null });
    return reply(items.find((item) => url.endsWith(item.client_file_id)));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, manifest: () => manifest };
}
function finish(id: string, items: UploadItem[]) {
  const item = items.find((item) => item.client_file_id === id)!;
  item.state = "REGISTERED"; item.document_id = `doc-${id}`;
  return reply({ documents: [{ document_id: item.document_id }], errors: [] }, 201);
}
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("durable upload transfers", () => {
  it("keeps at most three one-file transfers active and invalidates once", async () => {
    const pending: (() => void)[] = [];
    let active = 0; let peak = 0;
    const { fetchMock } = server(async (id, items) => {
      active++; peak = Math.max(peak, active);
      await new Promise<void>((resolve) => pending.push(() => { active--; resolve(); }));
      return finish(id, items);
    });
    const changed = vi.fn();
    const manager = new UploadTransferManager(changed);
    const running = manager.start({ files: Array.from({ length: 5 }, (_, i) => file(`${i}.pdf`)), caseId: "case" });
    await waitFor(() => expect(pending).toHaveLength(3));
    pending.shift()!();
    await waitFor(() => expect(pending).toHaveLength(3));
    pending.shift()!();
    await waitFor(() => expect(pending).toHaveLength(3));
    pending.splice(0).forEach((resolve) => resolve());
    await running;
    expect(peak).toBe(3);
    expect(changed).toHaveBeenCalledOnce();
    for (const [url, init] of fetchMock.mock.calls) {
      if (url === "/api/documents") expect((init!.body as FormData).getAll("files")).toHaveLength(1);
    }
    expect(manager.getSnapshot().batch!.items.every((item) => item.state === "REGISTERED")).toBe(true);
  });

  it("restores outcomes after refresh and reselects only unfinished files", async () => {
    let reject = true;
    const { fetchMock } = server(async (id, items) => {
      if (reject && items.find((item) => item.client_file_id === id)!.name === "bad.pdf") {
        return new Response("<html>Request too large</html>", { status: 413 });
      }
      return finish(id, items);
    });
    const input = { files: [file("good.pdf"), file("bad.pdf")], caseId: "" };
    await new UploadTransferManager().start(input);
    const restored = new UploadTransferManager();
    await restored.restore();
    expect(restored.getSnapshot().batch!.items[1].error_message).toContain("too large");
    await restored.retry();
    expect(restored.getSnapshot().error).toContain("Reselect");
    reject = false;
    await restored.start(input);
    const posts = fetchMock.mock.calls.filter(([url]) => url === "/api/documents");
    expect(posts).toHaveLength(3); // Two initial uploads, only the unfinished file on reselection.
    expect(restored.getSnapshot().batch!.items.every((item) => item.state === "REGISTERED")).toBe(true);
  });

  it("persists the manifest identity before a lost creation response", async () => {
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
      requests.push(JSON.parse(init.body).client_request_id);
      throw new Error("network lost");
    }));
    const input = { files: [file("one.pdf")], caseId: "" };
    await new UploadTransferManager().start(input);
    await new UploadTransferManager().start(input);
    expect(requests).toHaveLength(2);
    expect(requests[0]).toBe(requests[1]);
  });

  it("resolves a lost upload response before retransmitting bytes", async () => {
    const { fetchMock } = server(async (id, items) => {
      finish(id, items);
      throw new Error("connection lost after commit");
    });
    const manager = new UploadTransferManager();
    await manager.start({ files: [file("one.pdf")], caseId: "" });
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/documents")).toHaveLength(1);
    expect(manager.getSnapshot().batch!.items[0].state).toBe("REGISTERED");
  });

  it("pauses queued transfers while allowing active transfers to finish", async () => {
    const pending: (() => void)[] = [];
    server(async (id, items) => {
      await new Promise<void>((resolve) => pending.push(resolve));
      return finish(id, items);
    });
    const manager = new UploadTransferManager();
    const running = manager.start({ files: Array.from({ length: 5 }, (_, i) => file(`${i}.pdf`)), caseId: "" });
    await waitFor(() => expect(pending).toHaveLength(3));
    manager.pause();
    pending.forEach((resolve) => resolve());
    await running;
    expect(manager.getSnapshot().batch!.items.filter((item) => item.state === "REGISTERED")).toHaveLength(3);
    expect(manager.getSnapshot().batch!.items.filter((item) => item.state === "QUEUED")).toHaveLength(2);
  });
});
