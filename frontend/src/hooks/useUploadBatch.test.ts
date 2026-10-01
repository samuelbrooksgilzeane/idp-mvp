import { afterEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { AUTO_RETRY_DELAY_MS, BUSY_RETRY_DELAYS_MS, SIGN_IN_MESSAGE, UploadTransferManager, type UploadItem } from "./useUploadBatch";

function file(name: string) { return new File(["%PDF-small"], name, { type: "application/pdf", lastModified: 123 }); }
function reply(payload: unknown, status = 200) { return { ok: status < 400, status, json: async () => payload } as Response; }
function server(upload: (id: string, items: UploadItem[]) => Promise<Response>, limits?: unknown) {
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
    if (url === "/api/upload-batches/limits" && limits) return reply(limits);
    if (url === "/api/documents") return upload((init!.body as FormData).get("client_file_id") as string, items);
    if (url.endsWith("/transport-failure")) return reply({});
    if (url.includes("/items?")) {
      const params = new URL(url, "https://local.test").searchParams;
      const start = Number(params.get("cursor")) + 1;
      const page = items.slice(start, start + Number(params.get("limit")));
      return reply({ items: page, next_cursor: start + page.length < items.length ? String(start + page.length - 1) : null });
    }
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
const SCOPE = "user-a";
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("durable upload transfers", () => {
  it("enforces the deployed file-count limit before creating a manifest", async () => {
    const fetchMock = vi.fn(async () => reply({ max_files: 2, max_file_bytes: 1024 }));
    vi.stubGlobal("fetch", fetchMock);
    const manager = new UploadTransferManager(undefined, SCOPE);
    await manager.restore();
    await manager.start({ files: [file("a.pdf"), file("b.pdf"), file("c.pdf")], caseId: "" });
    expect(manager.getSnapshot().maxFiles).toBe(2);
    expect(manager.getSnapshot().maxFileBytes).toBe(1024);
    expect(manager.getSnapshot().error).toBe("Select between 1 and 2 PDFs.");
    expect(manager.getSnapshot().batch).toBeNull();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith("/api/upload-batches/limits");
  });

  it("accounts for 1,000 synthetic files across ten progress pages with three transfers maximum", async () => {
    let active = 0; let peak = 0;
    const { fetchMock } = server(async (id, items) => {
      active++; peak = Math.max(peak, active);
      await Promise.resolve();
      active--;
      return finish(id, items);
    });
    const manager = new UploadTransferManager(undefined, SCOPE);
    await manager.start({ files: Array.from({ length: 1000 }, (_, i) => file(`${i}.pdf`)), caseId: "synthetic" });
    expect(peak).toBe(3);
    expect(manager.getSnapshot().error).toBeNull();
    const items = manager.getSnapshot().batch!.items;
    expect(items).toHaveLength(1000);
    expect(new Set(items.map((item) => item.document_id)).size).toBe(1000);
    expect(items.every((item) => item.state === "REGISTERED")).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/documents")).toHaveLength(1000);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/items?"))).toHaveLength(10);
    const restored = new UploadTransferManager(undefined, SCOPE);
    await restored.restore();
    expect(restored.getSnapshot().batch!.items).toHaveLength(1000);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/documents")).toHaveLength(1000);
  });

  it("stops a repeated pagination cursor without discarding saved outcomes", async () => {
    server(async (id, items) => finish(id, items));
    const manager = new UploadTransferManager(undefined, SCOPE);
    await manager.start({ files: [file("one.pdf")], caseId: "" });
    const saved = manager.getSnapshot().batch;
    const fetchMock = vi.fn(async () => reply({ items: [], next_cursor: "-1" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(manager.refresh()).rejects.toThrow("pagination did not advance");
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(manager.getSnapshot().batch).toBe(saved);
  });

  it("keeps at most three one-file transfers active and invalidates once", async () => {
    const pending: (() => void)[] = [];
    let active = 0; let peak = 0;
    const { fetchMock } = server(async (id, items) => {
      active++; peak = Math.max(peak, active);
      await new Promise<void>((resolve) => pending.push(() => { active--; resolve(); }));
      return finish(id, items);
    });
    const changed = vi.fn();
    const manager = new UploadTransferManager(changed, SCOPE);
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

  it("runs the deployment's configured number of parallel transfers, capped at eight", async () => {
    for (const [configured, expected] of [[5, 5], [20, 8]]) {
      const pending: (() => void)[] = [];
      let active = 0; let peak = 0;
      server(async (id, items) => {
        active++; peak = Math.max(peak, active);
        await new Promise<void>((resolve) => pending.push(() => { active--; resolve(); }));
        return finish(id, items);
      }, { max_files: 1000, max_file_bytes: 1024, parallel_transfers: configured });
      const manager = new UploadTransferManager(undefined, SCOPE);
      await manager.restore();
      expect(manager.getSnapshot().parallelTransfers).toBe(expected);
      const running = manager.start({ files: Array.from({ length: 12 }, (_, i) => file(`${i}.pdf`)), caseId: "" });
      await waitFor(() => expect(pending).toHaveLength(expected));
      while (pending.length) { pending.splice(0).forEach((resolve) => resolve()); await new Promise((r) => setTimeout(r, 0)); }
      await running;
      expect(peak).toBe(expected);
      localStorage.clear(); vi.unstubAllGlobals();
    }
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
    await new UploadTransferManager(undefined, SCOPE).start(input);
    const restored = new UploadTransferManager(undefined, SCOPE);
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
    await new UploadTransferManager(undefined, SCOPE).start(input);
    await new UploadTransferManager(undefined, SCOPE).start(input);
    expect(requests).toHaveLength(2);
    expect(requests[0]).toBe(requests[1]);
  });

  it("resolves a lost upload response before retransmitting bytes", async () => {
    const { fetchMock } = server(async (id, items) => {
      finish(id, items);
      throw new Error("connection lost after commit");
    });
    const manager = new UploadTransferManager(undefined, SCOPE);
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
    const manager = new UploadTransferManager(undefined, SCOPE);
    const running = manager.start({ files: Array.from({ length: 5 }, (_, i) => file(`${i}.pdf`)), caseId: "" });
    await waitFor(() => expect(pending).toHaveLength(3));
    manager.pause();
    pending.forEach((resolve) => resolve());
    await running;
    expect(manager.getSnapshot().batch!.items.filter((item) => item.state === "REGISTERED")).toHaveLength(3);
    expect(manager.getSnapshot().batch!.items.filter((item) => item.state === "QUEUED")).toHaveLength(2);
  });

  it("waits and retries a file the server reports as busy instead of failing it", async () => {
    vi.useFakeTimers();
    let calls = 0;
    const { fetchMock } = server(async (id, items) => ++calls === 1
      ? reply({ error: { code: "UPLOAD_BUSY", message: "Already uploading." } }, 409)
      : finish(id, items));
    const manager = new UploadTransferManager(undefined, SCOPE);
    const running = manager.start({ files: [file("one.pdf")], caseId: "" });
    await vi.advanceTimersByTimeAsync(0);
    expect(manager.getSnapshot().batch!.items[0]).toMatchObject({ state: "QUEUED", error_code: null });
    await vi.advanceTimersByTimeAsync(BUSY_RETRY_DELAYS_MS[0]);
    await running;
    expect(manager.getSnapshot().batch!.items[0].state).toBe("REGISTERED");
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/documents")).toHaveLength(2);
  });

  it.each([
    ["an expired session (401)", () => reply({}, 401)],
    ["a redirect to sign-in", () => ({ ok: false, status: 0, type: "opaqueredirect", json: async () => { throw new Error("opaque"); } }) as unknown as Response],
  ])("pauses the whole batch on %s and resumes after sign-in", async (_label, signedOut) => {
    let expired = true;
    const { fetchMock } = server(async (id, items) => expired ? signedOut() : finish(id, items));
    const manager = new UploadTransferManager(undefined, SCOPE);
    const input = { files: Array.from({ length: 5 }, (_, i) => file(`${i}.pdf`)), caseId: "" };
    await manager.start(input);
    const snapshot = manager.getSnapshot();
    expect(snapshot).toMatchObject({ paused: true, signInRequired: true, error: SIGN_IN_MESSAGE, busy: false });
    expect(snapshot.batch!.items.every((item) => item.state === "QUEUED")).toBe(true);
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/documents")).toHaveLength(3);
    expect(fetchMock.mock.calls.some(([url]) => url.toString().endsWith("/transport-failure"))).toBe(false);
    for (const [, init] of fetchMock.mock.calls) if (init) expect(init.redirect).toBe("manual");
    expired = false;
    await manager.retry();
    expect(manager.getSnapshot().signInRequired).toBe(false);
    expect(manager.getSnapshot().batch!.items.every((item) => item.state === "REGISTERED")).toBe(true);
  });

  it("makes one automatic pass over retryable failures after the queue drains", async () => {
    vi.useFakeTimers();
    let down = true;
    server(async (id, items) => down ? reply({ error: { code: "HTTP_503", message: "Unavailable" } }, 503) : finish(id, items));
    const manager = new UploadTransferManager(undefined, SCOPE);
    const running = manager.start({ files: [file("one.pdf")], caseId: "" });
    await vi.advanceTimersByTimeAsync(750 + 1500);
    expect(manager.getSnapshot().batch!.items[0]).toMatchObject({ state: "FAILED", retryable: true });
    expect(manager.getSnapshot().retryingSoon).toBe(true);
    down = false;
    await vi.advanceTimersByTimeAsync(AUTO_RETRY_DELAY_MS);
    await running;
    expect(manager.getSnapshot().retryingSoon).toBe(false);
    expect(manager.getSnapshot().batch!.items[0].state).toBe("REGISTERED");
  });

  it("warns before unload and holds a screen wake lock only while transferring", async () => {
    const sentinel = { release: vi.fn(async () => undefined), addEventListener: vi.fn() };
    const request = vi.fn(async () => sentinel);
    Object.defineProperty(navigator, "wakeLock", { configurable: true, value: { request } });
    const added = vi.spyOn(window, "addEventListener");
    const removed = vi.spyOn(window, "removeEventListener");
    try {
      const pending: (() => void)[] = [];
      server(async (id, items) => { await new Promise<void>((resolve) => pending.push(resolve)); return finish(id, items); });
      const manager = new UploadTransferManager(undefined, SCOPE);
      const running = manager.start({ files: [file("one.pdf")], caseId: "" });
      await waitFor(() => expect(pending).toHaveLength(1));
      expect(added).toHaveBeenCalledWith("beforeunload", expect.any(Function));
      expect(request).toHaveBeenCalledWith("screen");
      expect(removed).not.toHaveBeenCalledWith("beforeunload", expect.any(Function));
      pending[0]();
      await running;
      expect(removed).toHaveBeenCalledWith("beforeunload", expect.any(Function));
      expect(sentinel.release).toHaveBeenCalled();
    } finally {
      added.mockRestore(); removed.mockRestore();
      delete (navigator as { wakeLock?: unknown }).wakeLock;
    }
  });

  it("keeps each signed-in user's batch to themselves and ignores a failed identity check", async () => {
    localStorage.setItem("idp:upload-batch:v1", "{}");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("network lost"); }));
    const manager = new UploadTransferManager(undefined, "user-a");
    expect(localStorage.getItem("idp:upload-batch:v1")).toBeNull(); // unscoped: owner unknown
    await manager.start({ files: [file("ann.pdf")], caseId: "case-ann" });
    manager.setScope("signed-out");
    expect(manager.getSnapshot().batch?.case_id).toBe("case-ann");
    manager.setScope("user-b");
    expect(manager.getSnapshot().batch).toBeNull();
    expect(new UploadTransferManager(undefined, "user-b").getSnapshot().batch).toBeNull();
    manager.setScope("user-a");
    expect(manager.getSnapshot().batch?.case_id).toBe("case-ann");
  });

  it("saves a batch started before the user was known under that user", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("network lost"); }));
    const manager = new UploadTransferManager();
    await manager.start({ files: [file("ann.pdf")], caseId: "case-ann" });
    expect(localStorage.length).toBe(0);
    manager.setScope("user-a");
    expect(manager.getSnapshot().batch?.case_id).toBe("case-ann");
    expect(new UploadTransferManager(undefined, "user-a").getSnapshot().batch?.case_id).toBe("case-ann");
  });

  it("drops a creation answer that arrives after another user signs in", async () => {
    let answer: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn((url: string) => url === "/api/upload-batches"
      ? new Promise<Response>((resolve) => { answer = resolve; })
      : Promise.resolve(reply({ items: [], next_cursor: null }))));
    const manager = new UploadTransferManager(undefined, "user-a");
    const started = manager.start({ files: [file("ann.pdf")], caseId: "" });
    await waitFor(() => expect(answer).toBeDefined());
    manager.setScope("user-b");
    answer!(reply({ batch_id: "batch-ann", items: [] }));
    await started;
    expect(manager.getSnapshot().batch).toBeNull();
    expect(localStorage.getItem("idp:upload-batch:v1:user-b")).toBeNull();
  });
});
