import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { UploadInput } from "../components/UploadPanel";

export type UploadItem = {
  client_file_id: string; name: string; size: number; last_modified: number | null;
  ordinal: number; state: "QUEUED" | "UPLOADING" | "REGISTERED" | "ALREADY_REGISTERED" | "FAILED";
  document_id: string | null; attempts: number; error_code: string | null;
  error_message: string | null; retryable: boolean; updated_at: string;
};
type Manifest = Pick<UploadItem, "client_file_id" | "name" | "size" | "last_modified">;
type SavedBatch = {
  client_request_id: string; case_id: string | null; files: Manifest[];
  batch_id: string | null; items: UploadItem[];
};
type Snapshot = { batch: SavedBatch | null; busy: boolean; paused: boolean; error: string | null; maxFiles: number; maxFileBytes: number | null; automaticPreparation?: boolean; bulkExtraction?: boolean };
const STORAGE_KEY = "idp:upload-batch:v1"; // Storage is isolated by this project's app origin.
const complete = (item: UploadItem) => item.state === "REGISTERED" || item.state === "ALREADY_REGISTERED";
const signature = (file: { name: string; size: number; lastModified?: number; last_modified?: number | null }) =>
  JSON.stringify([file.name, file.size, file.lastModified ?? file.last_modified]);

class UploadHttpError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message); }
}
async function jsonResponse<T>(response: Response): Promise<T> {
  let payload: { error?: { message?: string; code?: string } } | null = null;
  try { payload = await response.json(); } catch { /* Gateways may return HTML. */ }
  if (!response.ok) {
    const message = response.status === 413 ? "The PDF is too large for the server or app gateway."
      : response.status === 401 || response.status === 403 ? "Sign in again before retrying this file."
      : `The request failed (HTTP ${response.status}).`;
    throw new UploadHttpError(payload?.error?.message ?? message, response.status,
      payload?.error?.code ?? `HTTP_${response.status}`);
  }
  if (!payload) throw new Error("The upload service returned an empty response.");
  return payload as T;
}

// Owned above the list route: navigating to Results does not discard File objects or transfers.
export class UploadTransferManager {
  private snapshot: Snapshot = { batch: null, busy: false, paused: false, error: null, maxFiles: 1000, maxFileBytes: null };
  private listeners = new Set<() => void>();
  private files = new Map<string, File>();
  private stop = false;
  private restored = false;
  constructor(private readonly onFinished: () => void = () => {}) {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as SavedBatch | null;
      if (saved && Array.isArray(saved.files) && saved.files.length <= 1000 && Array.isArray(saved.items)) {
        this.snapshot = { ...this.snapshot, batch: saved };
      }
    } catch { /* A blocked or invalid storage entry must not prevent intake. */ }
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(changes: Partial<Snapshot>) {
    this.snapshot = { ...this.snapshot, ...changes };
    try {
      if (this.snapshot.batch) localStorage.setItem(STORAGE_KEY, JSON.stringify(this.snapshot.batch));
      else localStorage.removeItem(STORAGE_KEY);
    } catch { /* Transfers still work with storage disabled. */ }
    this.listeners.forEach((listener) => listener());
  }
  private patch(id: string, changes: Partial<UploadItem>) {
    const batch = this.snapshot.batch;
    if (batch) this.update({ batch: { ...batch, items: batch.items.map((item) => item.client_file_id === id ? { ...item, ...changes } : item) } });
  }
  restore = async () => {
    if (this.restored) return;
    this.restored = true;
    try {
      const limits = await jsonResponse<{ max_files: number; max_file_bytes: number; automatic_preparation?: boolean; bulk_extraction?: boolean }>(await fetch("/api/upload-batches/limits"));
      if (Number.isInteger(limits.max_files) && limits.max_files > 0 && Number.isInteger(limits.max_file_bytes) && limits.max_file_bytes > 0) {
        this.update({ maxFiles: Math.min(1000, limits.max_files), maxFileBytes: limits.max_file_bytes, automaticPreparation: Boolean(limits.automatic_preparation), bulkExtraction: Boolean(limits.bulk_extraction) });
      }
    } catch { /* The API still enforces limits when configuration is unavailable. */ }
    if (this.snapshot.busy) return;
    if (this.snapshot.batch?.batch_id) {
      this.update({ busy: true });
      try { await this.refresh(); } catch (error) { this.fail(error); }
      finally { this.update({ busy: false }); }
    }
  };
  private fail(error: unknown) { this.update({ error: error instanceof Error ? error.message : "Upload interrupted. Retry when ready." }); }
  refresh = async () => {
    const batch = this.snapshot.batch;
    if (!batch?.batch_id) return;
    const items: UploadItem[] = [];
    const visited = new Set<string>();
    let cursor: string | null = "-1";
    do {
      if (visited.has(cursor)) throw new Error("Upload progress pagination did not advance. Retry refreshing progress.");
      visited.add(cursor);
      const response: { items: UploadItem[]; next_cursor: string | null } = await jsonResponse(
        await fetch(`/api/upload-batches/${batch.batch_id}/items?limit=100&cursor=${cursor}`));
      items.push(...response.items);
      cursor = response.next_cursor;
      if (items.length > 1000) throw new Error("Unexpected upload batch size.");
    } while (cursor !== null);
    if (this.snapshot.batch?.client_request_id !== batch.client_request_id) return;
    // Gateway failures never reached the API; keep their local explanation until retry.
    const localItems = new Map(this.snapshot.batch.items.map((item) => [item.client_file_id, item]));
    const merged = items.map((item) => {
      const local = localItems.get(item.client_file_id);
      return item.state === "QUEUED" && local?.state === "FAILED" ? local : item;
    });
    this.update({ batch: { ...batch, items: merged } });
  };
  start = async (input: UploadInput) => {
    if (this.snapshot.busy) return;
    this.update({ busy: true, error: null, paused: false });
    this.stop = false;
    try {
      let batch = this.snapshot.batch;
      if (!batch) {
        if (!input.files.length || input.files.length > this.snapshot.maxFiles) throw new Error(`Select between 1 and ${this.snapshot.maxFiles} PDFs.`);
        const manifests = input.files.map((file) => ({ client_file_id: crypto.randomUUID(),
          name: file.name, size: file.size, last_modified: file.lastModified }));
        batch = { client_request_id: crypto.randomUUID(), case_id: input.caseId.trim() || null,
          files: manifests, batch_id: null, items: manifests.map((file, ordinal) => ({ ...file, ordinal,
            state: "QUEUED", attempts: 0, document_id: null, error_code: null, error_message: null,
            retryable: true, updated_at: new Date().toISOString() })) };
        this.update({ batch }); // Persist identity before sending the manifest request.
        input.files.forEach((file, index) => this.files.set(manifests[index].client_file_id, file));
      } else {
        // Never guess between same-name/same-size files with identical modification times.
        for (const file of input.files) {
          const matches = batch.items.filter((item) => !complete(item) && signature(item) === signature(file));
          if (matches.length > 1) throw new Error(`More than one unfinished file matches ${file.name}. Start a new batch for these files.`);
          if (matches.length === 1) this.files.set(matches[0].client_file_id, file);
        }
        if (!this.files.size) throw new Error("Reselect the original unfinished PDFs, including their original names and modification times.");
      }
      if (!batch.batch_id) {
        const created = await jsonResponse<{ batch_id: string; items: UploadItem[] }>(await fetch("/api/upload-batches", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_request_id: batch.client_request_id, case_id: batch.case_id, files: batch.files }),
        }));
        this.update({ batch: { ...batch, batch_id: created.batch_id, items: created.items } });
      } else await this.refresh();
      await this.drain();
    } catch (error) { this.fail(error); }
    finally { this.update({ busy: false }); }
  };
  retry = () => this.start({ files: [], caseId: this.snapshot.batch?.case_id ?? "" });
  pause = () => { this.stop = true; this.update({ paused: true }); };
  clear = () => {
    if (this.snapshot.busy) return;
    this.files.clear();
    this.update({ batch: null, error: null, paused: false });
  };
  private async drain() {
    const queue = this.snapshot.batch!.items.filter((item) => !complete(item) && this.files.has(item.client_file_id));
    let next = 0;
    const worker = async () => {
      while (!this.stop && next < queue.length) {
        const item = queue[next++];
        await this.transfer(item);
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, queue.length) }, worker));
    await this.refresh();
    this.onFinished();
  }
  private async transfer(item: UploadItem) {
    const batchId = this.snapshot.batch!.batch_id!;
    for (let attempt = 0; attempt < 3 && !this.stop; attempt++) {
      try {
        if (attempt > 0) {
          // Resolve a lost response before retransmitting the PDF.
          const known = await jsonResponse<UploadItem>(await fetch(`/api/upload-batches/${batchId}/items/${item.client_file_id}`));
          if (complete(known)) { this.patch(item.client_file_id, known); this.files.delete(item.client_file_id); return; }
        }
        const file = this.files.get(item.client_file_id)!;
        if (this.snapshot.maxFileBytes && file.size > this.snapshot.maxFileBytes) {
          throw new UploadHttpError("The PDF exceeds the configured per-file size limit.", 413, "HTTP_413");
        }
        const body = new FormData();
        body.append("files", file); body.append("upload_batch_id", batchId);
        body.append("client_file_id", item.client_file_id);
        this.patch(item.client_file_id, { state: "UPLOADING", error_code: null, error_message: null });
        const response = await jsonResponse<{ documents: { document_id: string }[] }>(
          await fetch("/api/documents", { method: "POST", body }));
        if (!response.documents?.[0]) throw new Error("The upload service returned an unexpected response.");
        this.patch(item.client_file_id, { state: "REGISTERED", document_id: response.documents[0].document_id });
        this.files.delete(item.client_file_id);
        return;
      } catch (error) {
        const retryable = !(error instanceof UploadHttpError) || error.status >= 500 || error.status === 429;
        this.patch(item.client_file_id, { state: "FAILED", retryable,
          error_code: error instanceof UploadHttpError ? error.code : "UPLOAD_REQUEST_FAILED",
          error_message: error instanceof Error ? error.message : "Upload interrupted." });
        const code = error instanceof UploadHttpError ? `HTTP_${error.status}` : "UPLOAD_REQUEST_FAILED";
        if (["HTTP_413", "HTTP_401", "HTTP_403", "HTTP_429", "HTTP_502", "HTTP_503", "HTTP_504", "UPLOAD_REQUEST_FAILED"].includes(code)) {
          try {
            await fetch(`/api/upload-batches/${batchId}/items/${item.client_file_id}/transport-failure`, {
              method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }),
            });
          } catch { /* Preserve the local explanation until connectivity/authentication returns. */ }
        }
        if (!retryable || attempt === 2) return;
        await new Promise((resolve) => window.setTimeout(resolve, 750 * 2 ** attempt));
      }
    }
  }
}

export function useUploadBatch(onFinished: () => void) {
  const callback = useRef(onFinished);
  callback.current = onFinished;
  const [manager] = useState(() => new UploadTransferManager(() => callback.current()));
  const state = useSyncExternalStore(manager.subscribe, manager.getSnapshot);
  useEffect(() => { void manager.restore(); }, [manager]);
  return { ...state, start: manager.start, retry: manager.retry, pause: manager.pause,
    clear: manager.clear, refresh: manager.refresh };
}

export type UploadBatchController = ReturnType<typeof useUploadBatch>;
