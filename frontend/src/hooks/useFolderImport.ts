import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { UploadItem } from "./useUploadBatch";
import { isSignedInScope } from "../lib/requestCache";
import { readJson, removeKey, writeJson } from "../lib/safeStorage";

// A folder import is an upload batch registered by a server Job, so progress uses the same
// upload-batch endpoints. Nothing transfers from the browser; closing the tab does not stop it.
export type FolderImportBatch = {
  client_request_id: string; batch_id: string | null; folder: string; case_id: string | null;
  items: UploadItem[]; skipped_files?: number;
};
type Snapshot = {
  batch: FolderImportBatch | null; folders: string[] | null; root: string | null;
  busy: boolean; running: boolean; error: string | null;
};
const STORAGE_KEY = "idp:folder-import:v1"; // Suffixed with the signed-in user's cache scope.
export const IMPORT_POLL_MS = 5_000;
const unfinished = (item: UploadItem) => item.state === "QUEUED" || item.state === "UPLOADING";

class ImportHttpError extends Error { constructor(message: string, readonly status: number) { super(message); } }
async function json<T>(response: Response): Promise<T> {
  let payload: { error?: { message?: string } } | null = null;
  try { payload = await response.json(); } catch { /* Gateways may return HTML. */ }
  if (response.type === "opaqueredirect" || response.status === 401 || response.status === 403) {
    throw new Error("Your sign-in has expired. Reload the app, then retry.");
  }
  if (!response.ok) throw new ImportHttpError(payload?.error?.message ?? `The request failed (HTTP ${response.status}).`, response.status);
  if (!payload) throw new Error("The import service returned an empty response.");
  return payload as T;
}
const call = (url: string, init?: RequestInit) => fetch(url, { ...init, redirect: "manual" });

export class FolderImportManager {
  private snapshot: Snapshot = { batch: null, folders: null, root: null, busy: false, running: false, error: null };
  private listeners = new Set<() => void>();
  private timer: number | null = null;
  private scope: string | null = null; // Nothing is saved until the signed-in user is known.
  constructor(private readonly onProgress: () => void = () => {}, scope: string | null = null) {
    removeKey(STORAGE_KEY); // The unscoped entry from before per-user keys; its owner is unknown.
    if (scope && isSignedInScope(scope)) { this.scope = scope; this.snapshot = { ...this.snapshot, batch: this.load() }; }
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private load(): FolderImportBatch | null {
    const saved = readJson<FolderImportBatch>(`${STORAGE_KEY}:${this.scope}`);
    return saved && typeof saved.folder === "string" && Array.isArray(saved.items) ? saved : null;
  }
  private update(changes: Partial<Snapshot>) {
    this.snapshot = { ...this.snapshot, ...changes };
    if (this.scope) {
      // Progress still works with storage disabled.
      if (this.snapshot.batch) writeJson(`${STORAGE_KEY}:${this.scope}`, this.snapshot.batch);
      else removeKey(`${STORAGE_KEY}:${this.scope}`);
    }
    this.listeners.forEach((listener) => listener());
  }
  // Another signed-in user sees their own saved import, never the previous user's folders or
  // progress; "signed-out" (often a failed check) changes nothing.
  setScope = (scope: string) => {
    if (!isSignedInScope(scope) || scope === this.scope) return;
    const previous = this.scope;
    this.scope = scope;
    // An import started before the first answer belongs to this user: save it under their scope.
    if (previous === null && this.snapshot.batch) { this.update({}); return; }
    this.stopPolling();
    this.update({ batch: this.load(), folders: null, root: null, running: false, error: null });
    void this.restore();
  };
  private fail(error: unknown) {
    this.update({ error: error instanceof Error ? error.message : "The folder import request failed." });
  }
  // Safe to call again after dispose (a remount): it re-reads progress and resumes polling.
  restore = async () => {
    if (this.snapshot.batch?.batch_id) await this.refresh();
  };
  loadFolders = async () => {
    this.update({ busy: true, error: null });
    try {
      const listing = await json<{ root: string; folders: { name: string }[] }>(await call("/api/imports/folders"));
      this.update({ root: listing.root, folders: listing.folders.map((folder) => folder.name) });
    } catch (error) { this.fail(error); }
    finally { this.update({ busy: false }); }
  };
  start = async (folder: string, caseId: string) => {
    if (this.snapshot.busy || this.snapshot.batch) return;
    const batch: FolderImportBatch = { client_request_id: crypto.randomUUID(), batch_id: null, folder,
      case_id: caseId.trim() || null, items: [] };
    this.update({ batch, busy: true, error: null }); // Persist identity before the request; a replay reuses it.
    await this.create(batch);
  };
  private async create(batch: FolderImportBatch) {
    // A user switch during the request: the answer is about a batch no longer shown.
    const current = () => this.snapshot.batch?.client_request_id === batch.client_request_id;
    try {
      const created = await json<{ batch_id: string; items: UploadItem[]; skipped_files: number }>(await call("/api/imports", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_request_id: batch.client_request_id, folder: batch.folder, case_id: batch.case_id }),
      }));
      if (!current()) return;
      this.update({ batch: { ...batch, batch_id: created.batch_id, items: created.items, skipped_files: created.skipped_files } });
      this.schedule();
    } catch (error) {
      if (!current()) return;
      // Rejected (empty folder, too many files, unknown folder): nothing was saved, so forget it.
      // Otherwise keep the identity; Retry replays the same request.
      const rejected = error instanceof ImportHttpError && error.status >= 400 && error.status < 500;
      if (rejected) this.update({ batch: null });
      this.fail(error);
    }
    finally { this.update({ busy: false }); }
  }
  // Starts another Job run for unfinished files; before the batch exists, replays its creation.
  retry = async () => {
    const batch = this.snapshot.batch;
    if (!batch || this.snapshot.busy) return;
    this.update({ busy: true, error: null });
    if (!batch.batch_id) { await this.create(batch); return; }
    try {
      await json(await call(`/api/imports/${batch.batch_id}/resume`, { method: "POST" }));
      await this.refresh();
    } catch (error) { this.fail(error); }
    finally { this.update({ busy: false }); }
  };
  clear = () => {
    this.stopPolling();
    this.update({ batch: null, error: null, running: false });
  };
  refresh = async () => {
    const batch = this.snapshot.batch;
    if (!batch?.batch_id) return;
    try {
      const items: UploadItem[] = [];
      let cursor: string | null = "-1";
      do {
        const page: { items: UploadItem[]; next_cursor: string | null } = await json(
          await call(`/api/upload-batches/${batch.batch_id}/items?limit=100&cursor=${cursor}`));
        items.push(...page.items);
        if (page.next_cursor === cursor || items.length > 1000) throw new Error("Import progress could not be read.");
        cursor = page.next_cursor;
      } while (cursor !== null);
      if (this.snapshot.batch?.client_request_id !== batch.client_request_id) return;
      const before = batch.items.filter((item) => !unfinished(item)).length;
      const running = items.some(unfinished);
      this.update({ batch: { ...this.snapshot.batch, items }, running, error: null });
      if (items.filter((item) => !unfinished(item)).length !== before) this.onProgress();
      if (running) this.schedule(); else this.stopPolling();
    } catch (error) {
      // Stop polling; "Retry unfinished files" resumes the import and progress.
      this.stopPolling(); this.update({ running: false }); this.fail(error);
    }
  };
  private schedule() {
    this.stopPolling();
    this.update({ running: true });
    this.timer = window.setTimeout(() => { this.timer = null; void this.refresh(); }, IMPORT_POLL_MS);
  }
  private stopPolling() {
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
  }
  dispose = () => this.stopPolling();
}

export function useFolderImport(onProgress: () => void, scope: string) {
  const callback = useRef(onProgress);
  callback.current = onProgress;
  const [manager] = useState(() => new FolderImportManager(() => callback.current(), scope));
  const state = useSyncExternalStore(manager.subscribe, manager.getSnapshot);
  useEffect(() => { void manager.restore(); return manager.dispose; }, [manager]);
  useEffect(() => { manager.setScope(scope); }, [manager, scope]);
  return { ...state, loadFolders: manager.loadFolders, start: manager.start, retry: manager.retry,
    clear: manager.clear, refresh: manager.refresh };
}

export type FolderImportController = ReturnType<typeof useFolderImport>;
