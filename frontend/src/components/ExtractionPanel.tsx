import { LoaderCircle, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { DocumentRecord, ExtractionReview } from "../types";
import type { CitationTarget } from "./DocumentViewer";
import { type FieldPolicy, GenericResultView } from "./GenericResultView";

export type ExtractionRun = {
  extraction_run_id: string;
  document_id: string;
  parse_run_id: string;
  schema_id: string;
  schema_version: number;
  schema_hash: string;
  extractor_version: "2.1";
  status: "RUNNING" | "EXTRACTED" | "FAILED";
  error_message: string | null;
  requested_by: string;
  job_run_id: number | null;
  started_at: string;
  completed_at: string | null;
};

type ExtractableSchema = {
  schema_id: string;
  schema_version: number;
  display_name: string;
  schema_hash: string;
  status: "PRODUCTION" | "PUBLISHED";
};

type ExtractionPanelProps = {
  document: DocumentRecord;
  onViewEvidence: (target: CitationTarget) => void;
  onDocumentsChanged: () => void;
};

const formatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function ExtractionPanel({
  document,
  onViewEvidence,
  onDocumentsChanged,
}: ExtractionPanelProps) {
  const documentId = document.document_id;
  const [runs, setRuns] = useState<ExtractionRun[]>([]);
  const [runsState, setRunsState] = useState<"loading" | "ready" | "error">("loading");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [review, setReview] = useState<ExtractionReview | null>(null);
  const [resultState, setResultState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [schemas, setSchemas] = useState<ExtractableSchema[]>([]);
  const [schemaKey, setSchemaKey] = useState("");
  const [triggering, setTriggering] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const parsed = [
    "PARSED",
    "EXTRACTING",
    "EXTRACTED",
    "EXTRACT_FAILED",
    "VALIDATED_PASS",
    "REVIEW_REQUIRED",
  ].includes(document.status);

  const loadRuns = useCallback(
    async (signal?: AbortSignal): Promise<ExtractionRun[]> => {
      const response = await fetch(`/api/documents/${documentId}/extraction-runs`, { signal });
      if (!response.ok) throw new Error("Extraction history request failed");
      const payload = (await response.json()) as unknown;
      return Array.isArray(payload) ? payload.filter(isExtractionRun) : [];
    },
    [documentId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setRuns([]);
    setSelectedRunId(null);
    setReview(null);
    setResultState("idle");
    setError(null);
    setActiveRunId(null);
    setRunsState("loading");
    loadRuns(controller.signal)
      .then((history) => {
        if (controller.signal.aborted) return;
        setRuns(history);
        setSelectedRunId(latestSuccessfulId(history));
        setRunsState("ready");
      })
      .catch((cause: unknown) => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) {
          setRunsState("error");
        }
      });
    return () => controller.abort();
  }, [documentId, loadRuns]);

  useEffect(() => {
    const controller = new AbortController();
    setSchemas([]);
    setSchemaKey("");
    fetch("/api/schemas?status=ALL", { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<unknown>) : []))
      .then((payload) => {
        if (controller.signal.aborted) return;
        const available = Array.isArray(payload) ? payload.filter(isExtractableSchema) : [];
        setSchemas(available);
        const preferred = available.find(
          (item) =>
            item.schema_id === document.template_id ||
            `${item.schema_id}_v${item.schema_version}` === document.template_id,
        );
        const selected = preferred ?? available[0];
        setSchemaKey(selected ? `${selected.schema_id}:${selected.schema_version}` : "");
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [document.template_id]);

  const schema = useMemo(
    () =>
      schemas.find(
        (item) => `${item.schema_id}:${item.schema_version}` === schemaKey,
      ) ?? null,
    [schemaKey, schemas],
  );

  const selectedRun = useMemo(
    () => runs.find((run) => run.extraction_run_id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  useEffect(() => {
    if (!selectedRun || selectedRun.status !== "EXTRACTED") {
      setReview(null);
      setResultState("idle");
      return;
    }
    const controller = new AbortController();
    setReview(null);
    setResultState("loading");
    fetch(`/api/extractions/${selectedRun.extraction_run_id}/review`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Extraction result request failed");
        return response.json() as Promise<unknown>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        if (!isExtractionReview(payload)) throw new Error("Extraction review was invalid");
        setReview(payload);
        setResultState("ready");
      })
      .catch((cause: unknown) => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) {
          setResultState("error");
        }
      });
    return () => controller.abort();
  }, [documentId, selectedRun]);

  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const history = await loadRuns();
        if (cancelled) return;
        setRuns(history);
        const run = history.find((item) => item.extraction_run_id === activeRunId);
        if (run && run.status === "RUNNING") {
          timer = window.setTimeout(() => void poll(), 1000);
          return;
        }
        setActiveRunId(null);
        setTriggering(false);
        if (run) setSelectedRunId(run.extraction_run_id);
        setError(run?.status === "FAILED" ? run.error_message ?? "Extraction failed." : null);
        onDocumentsChanged();
      } catch {
        if (!cancelled) {
          setActiveRunId(null);
          setTriggering(false);
          setError("Extraction status polling failed.");
        }
      }
    };
    timer = window.setTimeout(() => void poll(), 800);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeRunId, loadRuns, onDocumentsChanged]);

  async function handleExtract() {
    if (!schema) {
      setError("No production schema is available for this document.");
      return;
    }
    setTriggering(true);
    setError(null);
    try {
      const response = await fetch(`/api/documents/${documentId}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_id: schema.schema_id,
          schema_version: schema.schema_version,
        }),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok || !isExtractionRun(payload)) {
        throw new Error(errorMessage(payload) ?? "Extraction could not start.");
      }
      setRuns((current) => [payload, ...current.filter((run) => run.extraction_run_id !== payload.extraction_run_id)]);
      setSelectedRunId(payload.extraction_run_id);
      setActiveRunId(payload.extraction_run_id);
    } catch (cause: unknown) {
      setTriggering(false);
      setError(cause instanceof Error ? cause.message : "Extraction could not start.");
    }
  }

  const busy = triggering || activeRunId !== null || document.status === "EXTRACTING";

  return (
    <section className="extraction-panel" aria-labelledby="extraction-title">
      <div className="extraction-heading">
        <div>
          <p className="eyebrow">Extraction evidence</p>
          <h3 id="extraction-title">Typed fields and source citations</h3>
        </div>
        <button
          className="extract-action"
          type="button"
          disabled={!parsed || busy || !schema}
          onClick={() => void handleExtract()}
        >
          {busy ? (
            <LoaderCircle className="spin" size={16} aria-hidden="true" />
          ) : runs.length > 0 ? (
            <RotateCcw size={16} aria-hidden="true" />
          ) : (
            <Play size={16} aria-hidden="true" />
          )}
          {busy ? "Extracting" : runs.length > 0 ? "Re-run extraction" : "Run extraction"}
        </button>
      </div>

      {!parsed ? (
        <p className="extraction-hint">A successful parse is required before extraction.</p>
      ) : null}
      {error ? (
        <p className="extraction-error" role="alert">{error}</p>
      ) : null}

      {parsed ? (
        <div className="extraction-selector-row">
          <label htmlFor="extraction-schema-selector">
            Extraction schema
            <span>Choose the published contract to apply to this document.</span>
          </label>
          <select
            id="extraction-schema-selector"
            value={schemaKey}
            disabled={busy || !schemas.length}
            onChange={(event) => setSchemaKey(event.target.value)}
          >
            {!schemas.length ? <option value="">No extractable schema published</option> : null}
            {schemas.map((item) => {
              const key = `${item.schema_id}:${item.schema_version}`;
              return (
                <option key={key} value={key}>
                  {item.display_name} · v{item.schema_version}
                </option>
              );
            })}
          </select>
        </div>
      ) : null}

      {runsState === "loading" ? <p className="extraction-hint">Loading extraction history…</p> : null}
      {runsState === "error" ? (
        <p className="extraction-error" role="alert">Extraction history is unavailable.</p>
      ) : null}

      {runsState === "ready" && runs.length === 0 ? (
        <p className="extraction-hint">No extraction has been run for this document yet.</p>
      ) : null}

      {runsState === "ready" && runs.length > 0 ? (
        <div className="extraction-selector-row">
          <label htmlFor="extraction-run-selector">
            Extraction run
            <span>The latest successful run is shown by default; prior runs remain inspectable.</span>
          </label>
          <select
            id="extraction-run-selector"
            value={selectedRunId ?? ""}
            onChange={(event) => setSelectedRunId(event.target.value || null)}
          >
            {runs.map((run) => (
              <option key={run.extraction_run_id} value={run.extraction_run_id}>
                {run.extraction_run_id.slice(0, 8)} · {run.status} · {formatter.format(new Date(run.started_at))}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {selectedRun ? <RunProvenance run={selectedRun} /> : null}

      {selectedRun && selectedRun.status === "FAILED" ? (
        <p className="extraction-error" role="alert">
          {selectedRun.error_message ?? "This extraction attempt failed."}
        </p>
      ) : null}
      {selectedRun && selectedRun.status === "RUNNING" ? (
        <p className="extraction-hint">This extraction run is still in progress.</p>
      ) : null}

      {resultState === "loading" ? <p className="extraction-hint">Loading extracted fields…</p> : null}
      {resultState === "error" ? (
        <p className="extraction-error" role="alert">The extracted fields could not be loaded.</p>
      ) : null}
      {resultState === "ready" && review ? (
        <GenericResultView
          rootMode={review.root_mode}
          hierarchy={review.result}
          fields={review.fields}
          fieldPolicies={reviewFieldPolicies(review)}
          onViewEvidence={onViewEvidence}
        />
      ) : null}
    </section>
  );
}

function RunProvenance({ run }: { run: ExtractionRun }) {
  return (
    <div className="extraction-provenance" aria-label="Extraction run provenance">
      <span className={`extraction-status extraction-status-${run.status.toLowerCase()}`}>
        {run.status}
      </span>
      <span>{run.schema_id} v{run.schema_version}</span>
      <span className="schema-hash" title={run.schema_hash}>SHA {run.schema_hash.slice(0, 12)}</span>
      <span>ai_extract {run.extractor_version}</span>
    </div>
  );
}

function latestSuccessfulId(runs: ExtractionRun[]): string | null {
  const successful = runs.find((run) => run.status === "EXTRACTED");
  return (successful ?? runs[0])?.extraction_run_id ?? null;
}

function errorMessage(payload: unknown): string | null {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error?: { message?: string } }).error;
    if (error && typeof error.message === "string") return error.message;
  }
  return null;
}

function isExtractionRun(value: unknown): value is ExtractionRun {
  if (!value || typeof value !== "object") return false;
  const run = value as Partial<ExtractionRun>;
  return (
    typeof run.extraction_run_id === "string" &&
    typeof run.schema_id === "string" &&
    typeof run.schema_version === "number" &&
    typeof run.schema_hash === "string" &&
    (run.status === "RUNNING" || run.status === "EXTRACTED" || run.status === "FAILED")
  );
}

function isExtractableSchema(value: unknown): value is ExtractableSchema {
  if (!value || typeof value !== "object") return false;
  const schema = value as Partial<ExtractableSchema> & { status?: string };
  return (
    typeof schema.schema_id === "string" &&
    typeof schema.schema_version === "number" &&
    typeof schema.display_name === "string" &&
    typeof schema.schema_hash === "string" &&
    (schema.status === "PRODUCTION" || schema.status === "PUBLISHED")
  );
}

function isExtractionReview(value: unknown): value is ExtractionReview {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ExtractionReview>;
  return (
    isExtractionRun(result.run) &&
    Array.isArray(result.fields) &&
    typeof result.result === "object" &&
    result.result !== null &&
    typeof result.field_policies === "object" &&
    result.field_policies !== null
  );
}

function reviewFieldPolicies(review: ExtractionReview): Map<string, FieldPolicy> {
  return new Map(
    Object.entries(review.field_policies).map(([path, policy]) => [
      path,
      {
        confidenceThreshold: policy.confidence_threshold,
        citationRequired: policy.citation_required,
      },
    ]),
  );
}
