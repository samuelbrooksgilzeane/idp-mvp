import { FileText, LoaderCircle, MapPin, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { DocumentRecord } from "../types";
import type { CitationTarget } from "./DocumentViewer";
import type { CitationCoordinate } from "./viewerGeometry";

type CitationBox = { coord: [number, number, number, number]; page_id: number };
type Citation = { id: number; bbox: CitationBox[] };

export type ExtractedField = {
  field_path: string;
  field_type: string;
  value: unknown;
  value_string: string | null;
  confidence_score: number | null;
  citation_ids: number[];
  citations: Citation[];
  extraction_error: string | null;
};

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

export type InvoiceCandidate = {
  invoice_number: string | null;
  invoice_date: string | null;
  seller_name: string | null;
  subtotal: string | null;
  discount_amount: string | null;
  tax_amount: string | null;
  total_amount: string | null;
  currency: string | null;
};

type ExtractionResult = {
  run: ExtractionRun;
  fields: ExtractedField[];
  candidates: InvoiceCandidate[];
};

type ProductionSchema = {
  schema_id: string;
  schema_version: number;
  display_name: string;
  schema_hash: string;
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
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [resultState, setResultState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [schema, setSchema] = useState<ProductionSchema | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const evidenceNonce = useRef(0);

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
    setResult(null);
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
    setSchema(null);
    fetch(`/api/schemas?status=PRODUCTION&use_case=${encodeURIComponent(document.use_case)}`, {
      signal: controller.signal,
    })
      .then((response) => (response.ok ? (response.json() as Promise<unknown>) : []))
      .then((payload) => {
        if (controller.signal.aborted) return;
        const available = Array.isArray(payload) ? payload.filter(isProductionSchema) : [];
        setSchema(available[0] ?? null);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [document.use_case]);

  const selectedRun = useMemo(
    () => runs.find((run) => run.extraction_run_id === selectedRunId) ?? null,
    [runs, selectedRunId],
  );

  useEffect(() => {
    if (!selectedRun || selectedRun.status !== "EXTRACTED") {
      setResult(null);
      setResultState("idle");
      return;
    }
    const controller = new AbortController();
    setResult(null);
    setResultState("loading");
    fetch(`/api/documents/${documentId}/extractions/${selectedRun.extraction_run_id}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Extraction result request failed");
        return response.json() as Promise<unknown>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        if (!isExtractionResult(payload)) throw new Error("Extraction result was invalid");
        setResult(payload);
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

  function handleViewEvidence(field: ExtractedField) {
    const boxes: CitationCoordinate[] = field.citations.flatMap((citation) =>
      citation.bbox.map((box) => ({ page_id: box.page_id, coord: box.coord })),
    );
    if (boxes.length === 0) return;
    evidenceNonce.current += 1;
    onViewEvidence({
      pageId: boxes[0].page_id,
      fieldLabel: field.field_path,
      boxes,
      nonce: evidenceNonce.current,
    });
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
      {resultState === "ready" && result ? (
        <FieldTable fields={result.fields} onViewEvidence={handleViewEvidence} />
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

function FieldTable({
  fields,
  onViewEvidence,
}: {
  fields: ExtractedField[];
  onViewEvidence: (field: ExtractedField) => void;
}) {
  const header = fields.filter((field) => !field.field_path.includes("["));
  const groups = groupRepeatedFields(fields);
  return (
    <>
      <p className="extraction-disclaimer">
        <FileText size={14} aria-hidden="true" /> Model confidence is metadata about the extraction,
        not a guarantee that a value is correct. Candidate data is not approved data.
      </p>
      <div className="extraction-table-scroll">
        <table className="extraction-table">
          <thead>
            <tr>
              <th>Field</th>
              <th>Extracted value</th>
              <th>Model confidence</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {header.map((field) => {
              const hasCitation = field.citations.some((citation) => citation.bbox.length > 0);
              return (
                <tr key={field.field_path}>
                  <td>
                    <strong>{field.field_path}</strong>
                    <code>{field.field_type}</code>
                    {field.extraction_error ? (
                      <small className="field-error">{field.extraction_error}</small>
                    ) : null}
                  </td>
                  <td>{field.value_string ?? <span className="value-null">Not returned</span>}</td>
                  <td>{formatConfidence(field.confidence_score)}</td>
                  <td>
                    {hasCitation ? (
                      <button
                        type="button"
                        className="evidence-button"
                        onClick={() => onViewEvidence(field)}
                      >
                        <MapPin size={13} aria-hidden="true" /> View evidence
                      </button>
                    ) : (
                      <span className="no-citation">No citation returned</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {[...groups.entries()].map(([name, rows]) => (
        <RepeatedFieldTable
          key={name}
          name={name}
          rows={rows}
          onViewEvidence={onViewEvidence}
        />
      ))}
    </>
  );
}

/** Group `line_items[0].amount` style paths into ordered rows keyed by their array name. */
function groupRepeatedFields(
  fields: ExtractedField[],
): Map<string, Map<number, Record<string, ExtractedField>>> {
  const groups = new Map<string, Map<number, Record<string, ExtractedField>>>();
  for (const field of fields) {
    const match = /^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]\.(.+)$/.exec(field.field_path);
    if (!match) continue;
    const [, name, index, leaf] = match;
    const rows = groups.get(name) ?? new Map<number, Record<string, ExtractedField>>();
    const row = rows.get(Number(index)) ?? {};
    row[leaf] = field;
    rows.set(Number(index), row);
    groups.set(name, rows);
  }
  return groups;
}

function RepeatedFieldTable({
  name,
  rows,
  onViewEvidence,
}: {
  name: string;
  rows: Map<number, Record<string, ExtractedField>>;
  onViewEvidence: (field: ExtractedField) => void;
}) {
  const ordered = [...rows.entries()].sort((a, b) => a[0] - b[0]);
  const columns = [...new Set(ordered.flatMap(([, row]) => Object.keys(row)))];
  return (
    <div className="line-item-group">
      <div className="line-item-heading">
        <strong>{name.replace(/_/g, " ")}</strong>
        <span>{ordered.length} {ordered.length === 1 ? "line" : "lines"}</span>
      </div>
      <div className="extraction-table-scroll">
        <table className="extraction-table line-item-table">
          <thead>
            <tr>
              <th>#</th>
              {columns.map((column) => <th key={column}>{column.replace(/_/g, " ")}</th>)}
            </tr>
          </thead>
          <tbody>
            {ordered.map(([index, row]) => (
              <tr key={index}>
                <td>{index + 1}</td>
                {columns.map((column) => {
                  const field = row[column];
                  if (!field) {
                    return <td key={column}><span className="value-null">Not returned</span></td>;
                  }
                  const cited = field.citations.some((citation) => citation.bbox.length > 0);
                  return (
                    <td key={column}>
                      <span className="line-value">
                        {field.value_string ?? <span className="value-null">Not returned</span>}
                      </span>
                      <small title="Model confidence">
                        {formatConfidence(field.confidence_score)}
                      </small>
                      {cited ? (
                        <button
                          type="button"
                          className="evidence-link"
                          onClick={() => onViewEvidence(field)}
                        >
                          <MapPin size={11} aria-hidden="true" />
                          <span className="visually-hidden">
                            View evidence for {field.field_path}
                          </span>
                        </button>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatConfidence(confidence: number | null): string {
  if (confidence === null) return "Not reported";
  return `${Math.round(confidence * 100)}%`;
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

function isProductionSchema(value: unknown): value is ProductionSchema {
  if (!value || typeof value !== "object") return false;
  const schema = value as Partial<ProductionSchema> & { status?: string };
  return (
    typeof schema.schema_id === "string" &&
    typeof schema.schema_version === "number" &&
    schema.status === "PRODUCTION"
  );
}

function isExtractionResult(value: unknown): value is ExtractionResult {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<ExtractionResult>;
  return isExtractionRun(result.run) && Array.isArray(result.fields);
}
