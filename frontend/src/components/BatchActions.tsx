import { LoaderCircle, Play, ScanText, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Notice } from "../types";

type BatchKind = "parse" | "extract";

type BatchMember = { document_id: string; run_id: string; status: string };
type BatchResponse = {
  kind: BatchKind;
  job_run_id: number | null;
  requested: number;
  accepted: number;
  members: BatchMember[];
  errors: { document_id: string; code: string; message: string }[];
};
type BatchStatus = {
  kind: BatchKind;
  job_run_id: number;
  total: number;
  running: number;
  succeeded: number;
  failed: number;
};

type ProductionSchema = { schema_id: string; schema_version: number; status: string };

type BatchActionsProps = {
  selectedIds: string[];
  useCase: string;
  onClear: () => void;
  onDocumentsChanged: () => Promise<void> | void;
};

export function BatchActions({
  selectedIds,
  useCase,
  onClear,
  onDocumentsChanged,
}: BatchActionsProps) {
  const [schema, setSchema] = useState<ProductionSchema | null>(null);
  const [running, setRunning] = useState<BatchKind | null>(null);
  const [progress, setProgress] = useState<BatchStatus | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const active = useRef<{ kind: BatchKind; jobRunId: number } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/schemas?status=PRODUCTION&use_case=${encodeURIComponent(useCase)}`, {
      signal: controller.signal,
    })
      .then((response) => (response.ok ? (response.json() as Promise<unknown>) : []))
      .then((payload) => {
        if (controller.signal.aborted || !Array.isArray(payload)) return;
        setSchema((payload[0] as ProductionSchema) ?? null);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [useCase]);

  const poll = useCallback(async () => {
    const current = active.current;
    if (!current) return;
    try {
      const response = await fetch(`/api/batches/${current.kind}/${current.jobRunId}`);
      if (!response.ok) throw new Error("Batch status request failed");
      const status = (await response.json()) as BatchStatus;
      setProgress(status);
      if (status.running > 0) {
        window.setTimeout(() => void poll(), 1000);
        return;
      }
      active.current = null;
      setRunning(null);
      setNotice({
        kind: status.failed ? "error" : "success",
        message: status.failed
          ? `${status.succeeded} of ${status.total} succeeded; ${status.failed} failed.`
          : `All ${status.total} documents completed.`,
      });
      await onDocumentsChanged();
    } catch {
      active.current = null;
      setRunning(null);
      setNotice({ kind: "error", message: "Batch status polling failed." });
    }
  }, [onDocumentsChanged]);

  async function run(kind: BatchKind) {
    setRunning(kind);
    setNotice(null);
    setProgress(null);
    try {
      const body: Record<string, unknown> = { document_ids: selectedIds };
      if (kind === "extract") {
        if (!schema) throw new Error("No production schema is available for this use case.");
        body.schema_id = schema.schema_id;
        body.schema_version = schema.schema_version;
      }
      const response = await fetch(`/api/batches/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json()) as BatchResponse | { error?: { message?: string } };
      if (!response.ok) {
        const message = (payload as { error?: { message?: string } }).error?.message;
        throw new Error(message ?? "The batch could not start.");
      }
      const batch = payload as BatchResponse;
      // Documents that failed their own preconditions are reported without stopping the rest.
      if (batch.errors.length) {
        setNotice({
          kind: "error",
          message: `${batch.errors.length} of ${batch.requested} skipped: ${batch.errors[0].message}`,
        });
      }
      if (batch.job_run_id === null) {
        setRunning(null);
        if (!batch.errors.length) {
          setNotice({ kind: "error", message: "No selected document was eligible." });
        }
        return;
      }
      active.current = { kind, jobRunId: batch.job_run_id };
      setProgress({
        kind,
        job_run_id: batch.job_run_id,
        total: batch.accepted,
        running: batch.accepted,
        succeeded: 0,
        failed: 0,
      });
      await onDocumentsChanged();
      window.setTimeout(() => void poll(), 800);
    } catch (error: unknown) {
      setRunning(null);
      setNotice({
        kind: "error",
        message: error instanceof Error ? error.message : "The batch could not start.",
      });
    }
  }

  if (!selectedIds.length && !running && !notice) return null;

  const busy = running !== null;
  return (
    <section className="batch-actions" aria-label="Batch actions">
      <div className="batch-summary">
        <strong>{selectedIds.length} selected</strong>
        {progress ? (
          <span className="batch-progress">
            {progress.succeeded + progress.failed} of {progress.total} complete
            {progress.failed ? ` · ${progress.failed} failed` : ""}
          </span>
        ) : null}
      </div>
      <div className="batch-buttons">
        <button
          type="button"
          disabled={busy || !selectedIds.length}
          onClick={() => void run("parse")}
        >
          {running === "parse" ? (
            <LoaderCircle className="spin" size={15} aria-hidden="true" />
          ) : (
            <Play size={15} aria-hidden="true" />
          )}
          Parse selected
        </button>
        <button
          type="button"
          disabled={busy || !selectedIds.length || !schema}
          onClick={() => void run("extract")}
        >
          {running === "extract" ? (
            <LoaderCircle className="spin" size={15} aria-hidden="true" />
          ) : (
            <ScanText size={15} aria-hidden="true" />
          )}
          Extract selected
        </button>
        <button type="button" className="batch-clear" disabled={busy} onClick={onClear}>
          <X size={14} aria-hidden="true" /> Clear
        </button>
      </div>
      {notice ? <p className={`notice notice-${notice.kind}`}>{notice.message}</p> : null}
    </section>
  );
}
