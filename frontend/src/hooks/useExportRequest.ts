import { useEffect, useState } from "react";
import { readJson, writeJson } from "../lib/safeStorage";

type ExportState = { export_id: string; state: string; selected_count: number; runs_processed: number;
  bytes: number | null; filename: string | null; error: string | null; download_url: string | null };
type Pending = { client_request_id: string; run_ids: string[]; format: "xlsx" | "csv"; include_historical_duplicates?: boolean };

export function useExportRequest(scope: string) {
  const key = `idp-export:${scope}`;
  const [saved, setSaved] = useState<{ id?: string; pending?: Pending }>(() => readJson(key) ?? {});
  const [status, setStatus] = useState<ExportState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // Blocked or full storage only loses reload recovery; the export itself still runs.
  useEffect(() => { writeJson(key, saved); }, [key, saved]);
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
    // Persist the exact idempotent request before transport; a reload can replay it. Without
    // storage, submit anyway: the server deduplicates replays by client_request_id.
    writeJson(key, { pending });
    setSaved({ pending }); setSubmitting(true); setError(null); setStatus(null); setConfirmation(false);
    try {
      const result = await fetch("/api/export-requests", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(pending) });
      if (!result.ok) {
        const body = await result.json();
        if (body.code === "CONFIRM_HISTORICAL_DUPLICATES") { setConfirmation(true); throw new Error(body.message); }
        throw new Error(body.error?.message || "Export could not be submitted. Retry to confirm.");
      }
      const data = await result.json() as ExportState;
      setSaved({ id: data.export_id }); setStatus(data);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setSubmitting(false); }
  }
  return { status, error, submitting, confirmation,
    confirm: () => saved.pending ? submit({ ...saved.pending, include_historical_duplicates: true }) : Promise.resolve(), pending: saved.pending,
    start: (run_ids: string[], format: "xlsx" | "csv" = "xlsx") => submit({
      client_request_id: crypto.randomUUID(), run_ids, format }),
    retry: () => saved.pending ? submit(saved.pending) : Promise.resolve(),
    dismiss: () => { setSaved({}); setStatus(null); setError(null); },
  };
}
