import { useEffect, useState } from "react";

type ExportState = { export_id: string; state: string; selected_count: number; runs_processed: number;
  bytes: number | null; filename: string | null; error: string | null; download_url: string | null };
type Pending = { client_request_id: string; run_ids: string[]; format: "xlsx" | "csv" };

export function useExportRequest(scope: string) {
  const key = `idp-export:${scope}`;
  const [saved, setSaved] = useState<{ id?: string; pending?: Pending }>(() => {
    try { return JSON.parse(localStorage.getItem(key) || "{}"); } catch { return {}; }
  });
  const [status, setStatus] = useState<ExportState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => { localStorage.setItem(key, JSON.stringify(saved)); }, [key, saved]);
  useEffect(() => {
    if (!saved.id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        if (!document.hidden) {
          const result = await fetch(`/api/export-requests/${saved.id}`, { signal: controller.signal });
          if (!result.ok) throw new Error("Export status could not be loaded.");
          const data = await result.json() as ExportState;
          if (controller.signal.aborted) return;
          setStatus(data); setError(null);
          if (["SUCCEEDED", "FAILED", "EXPIRED"].includes(data.state)) return;
        }
      } catch (cause) { if (!controller.signal.aborted) setError(String(cause)); }
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 5000);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [saved.id]);
  async function submit(pending: Pending) {
    // Persist the exact idempotent request before transport; a reload can replay it.
    localStorage.setItem(key, JSON.stringify({ pending }));
    setSaved({ pending }); setSubmitting(true); setError(null); setStatus(null);
    try {
      const result = await fetch("/api/export-requests", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending) });
      if (!result.ok) {
        const body = await result.json();
        throw new Error(body.error?.message || "Export could not be submitted. Retry to confirm.");
      }
      const data = await result.json() as ExportState;
      setSaved({ id: data.export_id }); setStatus(data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setSubmitting(false); }
  }
  return { status, error, submitting, pending: saved.pending,
    start: (run_ids: string[], format: "xlsx" | "csv" = "xlsx") => submit({
      client_request_id: crypto.randomUUID(), run_ids, format }),
    retry: () => saved.pending ? submit(saved.pending) : Promise.resolve(),
    dismiss: () => { setSaved({}); setStatus(null); setError(null); },
  };
}
