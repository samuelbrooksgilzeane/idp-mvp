import { afterEach, describe, expect, it, vi } from "vitest";
import { FolderImportManager, IMPORT_POLL_MS } from "./useFolderImport";
import type { UploadItem } from "./useUploadBatch";

function reply(payload: unknown, status = 200) { return { ok: status < 400, status, type: "basic", json: async () => payload } as Response; }
function item(id: string, state: UploadItem["state"]): UploadItem {
  return { client_file_id: id, name: `${id}.pdf`, relative_path: `sub/${id}.pdf`, size: 9, last_modified: null,
    ordinal: Number(id), state, document_id: state === "REGISTERED" ? `doc-${id}` : null, attempts: 0,
    error_code: null, error_message: null, retryable: true, updated_at: "2026-09-30T00:00:00Z" };
}
function server() {
  let items = [item("0", "QUEUED"), item("1", "QUEUED")];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input.toString();
    expect(init?.redirect).toBe("manual");
    if (url === "/api/imports/folders") return reply({ root: "/Volumes/c/s/idp_import", folders: [{ name: "invoices" }] });
    if (url === "/api/imports") return reply({ batch_id: "batch-1", items, skipped_files: 2 }, 201);
    if (url === "/api/imports/batch-1/resume") return reply({ batch_id: "batch-1" });
    if (url.startsWith("/api/upload-batches/batch-1/items?")) return reply({ items, next_cursor: null });
    return reply({ error: { message: "Unexpected" } }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, set: (next: UploadItem[]) => { items = next; } };
}
afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("folder import progress", () => {
  it("lists folders, starts one import and polls server progress until every file is finished", async () => {
    vi.useFakeTimers();
    const { fetchMock, set } = server();
    const progress = vi.fn();
    const manager = new FolderImportManager(progress);
    await manager.loadFolders();
    expect(manager.getSnapshot()).toMatchObject({ root: "/Volumes/c/s/idp_import", folders: ["invoices"] });

    await manager.start("invoices", " CASE-7 ");
    const body = JSON.parse(fetchMock.mock.calls.find(([url]) => url === "/api/imports")![1]!.body as string);
    expect(body).toMatchObject({ folder: "invoices", case_id: "CASE-7" });
    expect(manager.getSnapshot()).toMatchObject({ running: true, batch: { batch_id: "batch-1", skipped_files: 2 } });
    // A second start while one import is shown is ignored.
    await manager.start("invoices", "");
    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/imports")).toHaveLength(1);

    set([item("0", "REGISTERED"), item("1", "UPLOADING")]);
    await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS);
    expect(progress).toHaveBeenCalledOnce();
    expect(manager.getSnapshot().running).toBe(true);

    set([item("0", "REGISTERED"), item("1", "FAILED")]);
    await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS);
    expect(manager.getSnapshot().running).toBe(false);
    expect(progress).toHaveBeenCalledTimes(2);
    const polls = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(IMPORT_POLL_MS * 3);
    expect(fetchMock.mock.calls.length).toBe(polls); // Polling stopped.

    await manager.retry();
    expect(fetchMock).toHaveBeenCalledWith("/api/imports/batch-1/resume", { method: "POST", redirect: "manual" });
    manager.dispose();
  });

  it("restores a saved import after a reload and replays an unconfirmed start with its identity", async () => {
    const { fetchMock } = server();
    localStorage.setItem("idp:folder-import:v1", JSON.stringify({
      client_request_id: "request-9", batch_id: null, folder: "invoices", case_id: null, items: [],
    }));
    const manager = new FolderImportManager();
    expect(manager.getSnapshot().batch?.folder).toBe("invoices");
    await manager.retry();
    const body = JSON.parse(fetchMock.mock.calls.find(([url]) => url === "/api/imports")![1]!.body as string);
    expect(body.client_request_id).toBe("request-9");
    expect(JSON.parse(localStorage.getItem("idp:folder-import:v1")!).batch_id).toBe("batch-1");
    manager.clear();
    expect(localStorage.getItem("idp:folder-import:v1")).toBeNull();
  });

  it("shows the server's reason when a folder cannot be imported", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ error: { message: "The folder contains no PDF files." } }, 422)));
    const manager = new FolderImportManager();
    await manager.start("empty", "");
    expect(manager.getSnapshot().error).toBe("The folder contains no PDF files.");
    expect(manager.getSnapshot().busy).toBe(false);
    expect(manager.getSnapshot().batch).toBeNull(); // Nothing was saved; choose another folder.
  });
});
