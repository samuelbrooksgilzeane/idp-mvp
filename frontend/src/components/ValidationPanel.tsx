import { LoaderCircle, MapPin, Play, RotateCcw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { DocumentRecord } from "../types";
import type { CitationTarget } from "./DocumentViewer";
import type { CitationCoordinate } from "./viewerGeometry";

type Severity = "INFO" | "WARNING" | "BLOCKING";
type ResultStatus = "PASS" | "FAIL" | "UNCERTAIN" | "SKIPPED";

export type ValidationResult = {
  rule_id: string;
  field_path: string | null;
  validator_type: string;
  severity: Severity;
  status: ResultStatus;
  message: string;
  actual_value: string | null;
  expected_value: string | null;
  evidence: string | null;
  validator_version: string;
};

export type ValidationRun = {
  validation_run_id: string;
  document_id: string;
  extraction_run_id: string;
  schema_id: string;
  schema_version: number;
  schema_hash: string;
  validator_version: string;
  status: string;
  document_status: "VALIDATED_PASS" | "REVIEW_REQUIRED";
  requested_by: string;
  started_at: string;
  completed_at: string | null;
};

export type ValidationSummary = {
  total: number;
  passed: number;
  failed: number;
  uncertain: number;
  skipped: number;
  blocking: number;
  warning: number;
  info: number;
};

type ValidationReport = {
  run: ValidationRun;
  summary: ValidationSummary;
  results: ValidationResult[];
};

type ValidationPanelProps = {
  document: DocumentRecord;
  onViewEvidence: (target: CitationTarget) => void;
  onDocumentsChanged: () => void;
};

const OUTCOME_FILTERS = ["Issues", "All", "Passed"] as const;
type OutcomeFilter = (typeof OUTCOME_FILTERS)[number];

const formatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function ValidationPanel({
  document,
  onViewEvidence,
  onDocumentsChanged,
}: ValidationPanelProps) {
  const documentId = document.document_id;
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [citations, setCitations] = useState<Record<string, CitationCoordinate[]>>({});
  const [filter, setFilter] = useState<OutcomeFilter>("Issues");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const evidenceNonce = useRef(0);

  const extractable = ["EXTRACTED", "VALIDATED_PASS", "REVIEW_REQUIRED"].includes(
    document.status,
  );

  const loadRuns = useCallback(
    async (signal?: AbortSignal): Promise<ValidationRun[]> => {
      const response = await fetch(`/api/documents/${documentId}/validation-runs`, { signal });
      if (!response.ok) throw new Error("Validation history request failed");
      const payload = (await response.json()) as unknown;
      return Array.isArray(payload) ? payload.filter(isValidationRun) : [];
    },
    [documentId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setRuns([]);
    setSelectedRunId(null);
    setReport(null);
    setCitations({});
    setError(null);
    setState("loading");
    loadRuns(controller.signal)
      .then((history) => {
        if (controller.signal.aborted) return;
        setRuns(history);
        setSelectedRunId(history[0]?.validation_run_id ?? null);
        setState(history.length ? "ready" : "empty");
      })
      .catch((cause: unknown) => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) setState("error");
      });
    return () => controller.abort();
  }, [documentId, loadRuns]);

  useEffect(() => {
    if (!selectedRunId) {
      setReport(null);
      return;
    }
    const controller = new AbortController();
    fetch(`/api/documents/${documentId}/validations/${selectedRunId}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Validation report request failed");
        return response.json() as Promise<ValidationReport>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setReport(payload);
        setState("ready");
      })
      .catch((cause: unknown) => {
        if (!(cause instanceof DOMException && cause.name === "AbortError")) setState("error");
      });
    return () => controller.abort();
  }, [documentId, selectedRunId]);

  // Evidence lives on the extraction the run evaluated, so a failing field can be traced
  // back to the exact cited region of the page.
  useEffect(() => {
    const extractionRunId = report?.run.extraction_run_id;
    if (!extractionRunId) return;
    const controller = new AbortController();
    fetch(`/api/documents/${documentId}/extractions/${extractionRunId}`, {
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((payload: unknown) => {
        if (controller.signal.aborted || !payload || typeof payload !== "object") return;
        const fields = (payload as { fields?: unknown }).fields;
        if (!Array.isArray(fields)) return;
        const map: Record<string, CitationCoordinate[]> = {};
        for (const field of fields as {
          field_path?: unknown;
          citations?: { bbox?: { coord?: number[]; page_id?: number }[] }[];
        }[]) {
          if (typeof field.field_path !== "string" || !Array.isArray(field.citations)) continue;
          const boxes: CitationCoordinate[] = [];
          for (const citation of field.citations) {
            for (const box of citation.bbox ?? []) {
              if (Array.isArray(box.coord) && typeof box.page_id === "number") {
                boxes.push({ page_id: box.page_id, coord: box.coord });
              }
            }
          }
          if (boxes.length) map[field.field_path] = boxes;
        }
        setCitations(map);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [documentId, report?.run.extraction_run_id]);

  async function handleValidate() {
    setRunning(true);
    setError(null);
    try {
      const response = await fetch(`/api/documents/${documentId}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) {
        throw new Error(errorMessage(payload) ?? "Validation could not run.");
      }
      const fresh = payload as ValidationReport;
      setReport(fresh);
      setRuns((current) => [fresh.run, ...current]);
      setSelectedRunId(fresh.run.validation_run_id);
      setState("ready");
      onDocumentsChanged();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Validation could not run.");
    } finally {
      setRunning(false);
    }
  }

  function showEvidence(result: ValidationResult) {
    const boxes = result.field_path ? citations[result.field_path] : undefined;
    if (!boxes?.length) return;
    evidenceNonce.current += 1;
    onViewEvidence({
      pageId: boxes[0].page_id,
      fieldLabel: result.field_path ?? "evidence",
      boxes,
      nonce: evidenceNonce.current,
    });
  }

  const visible = useMemo(() => {
    const results = report?.results ?? [];
    if (filter === "All") return results;
    if (filter === "Passed") return results.filter((r) => r.status === "PASS");
    return results.filter((r) => r.status === "FAIL" || r.status === "UNCERTAIN");
  }, [report, filter]);

  return (
    <section className="validation-panel" aria-labelledby="validation-title">
      <div className="validation-heading">
        <div>
          <p className="eyebrow">Deterministic validation</p>
          <h3 id="validation-title">Explainable checks and exceptions</h3>
        </div>
        <button
          className="validate-action"
          type="button"
          disabled={!extractable || running}
          onClick={() => void handleValidate()}
        >
          {running ? (
            <LoaderCircle className="spin" size={16} aria-hidden="true" />
          ) : runs.length ? (
            <RotateCcw size={16} aria-hidden="true" />
          ) : (
            <Play size={16} aria-hidden="true" />
          )}
          {running ? "Validating" : runs.length ? "Re-run validation" : "Run validation"}
        </button>
      </div>

      {!extractable ? (
        <p className="validation-hint">A successful extraction is required before validation.</p>
      ) : null}
      {error ? <p className="validation-error" role="alert">{error}</p> : null}
      {state === "loading" ? <p className="validation-hint">Loading validation history…</p> : null}
      {state === "error" ? (
        <p className="validation-error" role="alert">Validation results are unavailable.</p>
      ) : null}
      {state === "empty" && !report ? (
        <p className="validation-hint">This document has not been validated yet.</p>
      ) : null}

      {runs.length > 1 ? (
        <div className="validation-selector-row">
          <label htmlFor="validation-run-selector">
            Validation run
            <span>Every attempt is retained; the most recent is shown by default.</span>
          </label>
          <select
            id="validation-run-selector"
            value={selectedRunId ?? ""}
            onChange={(event) => setSelectedRunId(event.target.value || null)}
          >
            {runs.map((run) => (
              <option key={run.validation_run_id} value={run.validation_run_id}>
                {run.validation_run_id.slice(0, 8)} · {run.document_status} ·{" "}
                {formatter.format(new Date(run.started_at))}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {report ? (
        <>
          <div className="validation-outcome" aria-label="Validation outcome">
            <span
              className={`outcome-badge outcome-${report.run.document_status.toLowerCase()}`}
            >
              <ShieldCheck size={13} aria-hidden="true" />
              {report.run.document_status === "VALIDATED_PASS"
                ? "No blocking exceptions"
                : "Review required"}
            </span>
            <span>{report.summary.failed} failed</span>
            <span>{report.summary.uncertain} uncertain</span>
            <span>{report.summary.skipped} skipped</span>
            <span>{report.summary.passed} passed</span>
            <span className="schema-hash" title={report.run.schema_hash}>
              {report.run.schema_id} v{report.run.schema_version}
            </span>
            <span>validator {report.run.validator_version}</span>
          </div>

          <p className="validation-disclaimer">
            These are automated observations against the registered contract. Neither outcome
            means a person has approved this document.
          </p>

          <div className="validation-filters" aria-label="Result filters">
            {OUTCOME_FILTERS.map((option) => (
              <button
                key={option}
                type="button"
                className={filter === option ? "active" : undefined}
                aria-pressed={filter === option}
                onClick={() => setFilter(option)}
              >
                {option}
              </button>
            ))}
          </div>

          {visible.length === 0 ? (
            <p className="validation-hint">No results match this filter.</p>
          ) : (
            <ul className="validation-results">
              {visible.map((result, index) => {
                const hasEvidence = Boolean(
                  result.field_path && citations[result.field_path]?.length,
                );
                return (
                  <li key={`${result.rule_id}-${result.field_path ?? "document"}-${index}`}>
                    <div className="result-header">
                      <span className={`result-status result-${result.status.toLowerCase()}`}>
                        {result.status}
                      </span>
                      <span className={`result-severity severity-${result.severity.toLowerCase()}`}>
                        {result.severity}
                      </span>
                      <strong>{result.rule_id}</strong>
                      {result.field_path ? <code>{result.field_path}</code> : null}
                    </div>
                    <p>{result.message}</p>
                    {result.actual_value !== null || result.expected_value !== null ? (
                      <dl className="result-values">
                        <div>
                          <dt>Observed</dt>
                          <dd>{result.actual_value ?? "-"}</dd>
                        </div>
                        <div>
                          <dt>Expected</dt>
                          <dd>{result.expected_value ?? "-"}</dd>
                        </div>
                      </dl>
                    ) : null}
                    {hasEvidence ? (
                      <button
                        type="button"
                        className="evidence-button"
                        onClick={() => showEvidence(result)}
                      >
                        <MapPin size={13} aria-hidden="true" /> View evidence
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      ) : null}
    </section>
  );
}

function errorMessage(payload: unknown): string | null {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error?: { message?: string } }).error;
    if (error && typeof error.message === "string") return error.message;
  }
  return null;
}

function isValidationRun(value: unknown): value is ValidationRun {
  if (!value || typeof value !== "object") return false;
  const run = value as Partial<ValidationRun>;
  return (
    typeof run.validation_run_id === "string" &&
    typeof run.schema_hash === "string" &&
    (run.document_status === "VALIDATED_PASS" || run.document_status === "REVIEW_REQUIRED")
  );
}
