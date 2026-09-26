import { useCallback, useEffect, useRef, useState } from "react";

export type ExtractionBatchStatus = {
  batch_id: string; state: string; total: number; validated: number;
  counts: Record<string, number>; terminal: boolean;
};
type Item = { document_id: string; document_name?: string; ordinal: number; state: string; error_message: string | null };
type Pending = { path: string; body: Record<string, unknown> };
type Saved = { batchId: string | null; pending: Pending | null };
async function payload<T>(response: Response): Promise<T> {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message ?? "Extraction batch request failed.");
  return data as T;
}

export function useExtractionBatch(enabled: boolean, scope: string, onComplete: () => void | Promise<void>) {
  const key = `idp:extraction-batch:${scope}`;
  const [saved, setSaved] = useState<Saved>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(key) ?? "null") as Saved | null;
      if (stored && (typeof stored.batchId === "string" || stored.batchId === null)) return stored;
    } catch { /* Storage can be unavailable. */ }
    return { batchId: null, pending: null };
  });
  const [status, setStatus] = useState<ExtractionBatchStatus | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [cursors, setCursors] = useState([-1]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const complete = useRef(onComplete);
  useEffect(() => { complete.current = onComplete; }, [onComplete]);
  const after = cursors[cursors.length - 1];
  const persist = useCallback((value: Saved) => {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Continue this session. */ }
    setSaved(value);
  }, [key]);

  useEffect(() => {
    if (!enabled || !saved.batchId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let delay = 5000;
      if (!document.hidden) {
        try {
          const result = await payload<ExtractionBatchStatus>(await fetch(`/api/extraction-batches/${saved.batchId}`, { signal: controller.signal }));
          if (controller.signal.aborted) return;
          setStatus(result); setError(null);
          if (result.terminal) { void complete.current(); return; }
        } catch (caught) {
          if (controller.signal.aborted) return;
          setError(caught instanceof Error ? caught.message : "Progress is temporarily unavailable.");
          delay = 15000;
        }
      }
      timer = setTimeout(() => void poll(), delay);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [enabled, saved.batchId]);

  useEffect(() => {
    if (!enabled || !status || document.hidden) return;
    const controller = new AbortController();
    fetch(`/api/extraction-batches/${status.batch_id}/items?after=${after}&limit=50`, { signal: controller.signal })
      .then((response) => payload<{ items: Item[]; next_cursor: number | null }>(response))
      .then((page) => { if (!controller.signal.aborted) { setItems(page.items); setNextCursor(page.next_cursor); } })
      .catch((caught) => { if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Member progress is unavailable."); });
    return () => controller.abort();
  }, [enabled, status, after]);

  async function submit(pending: Pending) {
    if (submitting.current) return;
    submitting.current = true; setBusy(true); setError(null);
    persist({ batchId: saved.batchId, pending });
    try {
      const result = await payload<ExtractionBatchStatus>(await fetch(pending.path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending.body),
      }));
      persist({ batchId: result.batch_id, pending: null });
      setStatus(result); setItems([]); setCursors([-1]); setNextCursor(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Submission response was lost. Retry the same request.");
    } finally { submitting.current = false; setBusy(false); }
  }
  return {
    status, items, error, busy, pending: saved.pending, batchId: saved.batchId,
    start: (ids: string[], schemaId: string, schemaVersion: number) => submit({
      path: "/api/extraction-batches", body: { client_request_id: crypto.randomUUID(), document_ids: ids, schema_id: schemaId, schema_version: schemaVersion },
    }),
    retry: () => saved.batchId ? submit({ path: `/api/extraction-batches/${saved.batchId}/retry`, body: { client_request_id: crypto.randomUUID() } }) : Promise.resolve(),
    retrySubmission: () => saved.pending ? submit(saved.pending) : Promise.resolve(),
    clear: () => { persist({ batchId: null, pending: null }); setStatus(null); setItems([]); setError(null); },
    previous: () => setCursors((values) => values.length > 1 ? values.slice(0, -1) : values),
    next: () => { if (nextCursor !== null) setCursors((values) => [...values, nextCursor]); },
    hasPrevious: cursors.length > 1, hasNext: nextCursor !== null,
  };
}
export type ExtractionBatchController = ReturnType<typeof useExtractionBatch>;
