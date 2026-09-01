import { ArrowLeft, ChevronDown, Download, LoaderCircle, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { DocumentViewer, type CitationTarget } from "../components/DocumentViewer";
import { type FieldPolicy, GenericResultView } from "../components/GenericResultView";
import type { ExtractionReview } from "../types";

const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function ResultDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [review, setReview] = useState<ExtractionReview | null>(null);
  const [fieldPolicies, setFieldPolicies] = useState<Map<string, FieldPolicy>>(new Map());
  const [state, setState] = useState<"loading" | "ready" | "missing">("loading");
  const [citationTarget, setCitationTarget] = useState<CitationTarget | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState("loading");
    setCitationTarget(null);
    try {
      const response = await fetch(`/api/extractions/${runId}/review`);
      if (!response.ok) throw new Error("Extraction review request failed");
      const payload = (await response.json()) as ExtractionReview;
      setReview(payload);
      setFieldPolicies(
        new Map(
          Object.entries(payload.field_policies).map(([path, policy]) => [
            path,
            {
              confidenceThreshold: policy.confidence_threshold,
              citationRequired: policy.citation_required,
            },
          ]),
        ),
      );
      setState("ready");
    } catch {
      setState("missing");
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRerun() {
    if (!review) return;
    setRerunning(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/documents/${review.run.document_id}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_id: review.schema_id,
          schema_version: review.schema_version,
        }),
      });
      const payload = (await response.json()) as { extraction_run_id?: string; error?: { message: string } };
      if (!response.ok || !payload.extraction_run_id) {
        throw new Error(payload.error?.message ?? "Re-run could not start.");
      }
      navigate(`/results/${payload.extraction_run_id}`);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Re-run could not start.");
    } finally {
      setRerunning(false);
    }
  }

  async function handleExport() {
    setExporting(true);
    setNotice(null);
    try {
      const response = await fetch("/api/exports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_ids: [runId], format: "xlsx" }),
      });
      if (!response.ok) throw new Error("The export could not be generated.");
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const match = /filename="([^"]+)"/.exec(disposition);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = match ? match[1] : "extraction-results.xlsx";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "The export could not be generated.");
    } finally {
      setExporting(false);
    }
  }

  if (state === "loading") return <p className="page-state">Loading extraction run…</p>;
  if (state === "missing" || !review) {
    return (
      <section className="page-state" role="alert">
        <strong>Extraction run not found</strong>
        <Link to="/results">Back to results</Link>
      </section>
    );
  }

  const { document, run } = review;

  return (
    <section className="document-detail result-detail" aria-labelledby="result-detail-title">
      <Link className="back-link" to="/results">
        <ArrowLeft size={14} aria-hidden="true" /> All results
      </Link>

      <div className="detail-header">
        <div>
          <p className="eyebrow">{document.file_name}</p>
          <h2 id="result-detail-title">
            {review.schema_id} · v{review.schema_version}
          </h2>
          <span className="detail-identity">
            Extracted {formatter.format(new Date(run.started_at))} ·{" "}
            <span className={`status-label status-${run.status.toLowerCase()}`}>
              {run.status}
            </span>
          </span>
        </div>
        <div className="result-detail-actions">
          <button type="button" onClick={() => void handleRerun()} disabled={rerunning}>
            {rerunning ? (
              <LoaderCircle className="spin" size={16} aria-hidden="true" />
            ) : (
              <RotateCcw size={16} aria-hidden="true" />
            )}
            Re-run extraction
          </button>
          <button className="export-action" type="button" onClick={() => void handleExport()} disabled={exporting}>
            <Download size={16} aria-hidden="true" />
            {exporting ? "Exporting…" : "Export this run"}
          </button>
        </div>
      </div>

      {notice ? <p className="notice notice-error">{notice}</p> : null}

      <details className="run-details-drawer">
        <summary><ChevronDown size={14} aria-hidden="true" /> Run details</summary>
        <dl>
          <div><dt>Run ID</dt><dd>{run.extraction_run_id}</dd></div>
          <div><dt>Schema hash</dt><dd className="run-details-hash">{run.schema_hash}</dd></div>
          <div><dt>Parse run</dt><dd>{run.parse_run_id}</dd></div>
          <div><dt>Options</dt><dd>{JSON.stringify(run.options)}</dd></div>
        </dl>
      </details>

      <div className="detail-workspace">
        <div className="detail-evidence">
          <DocumentViewer
            documentId={document.document_id}
            documentStatus={document.status}
            citationTarget={citationTarget}
          />
        </div>
        <div className="detail-panel">
          <GenericResultView
            rootMode={review.root_mode}
            hierarchy={review.result}
            fields={review.fields}
            fieldPolicies={fieldPolicies}
            onViewEvidence={setCitationTarget}
          />
        </div>
      </div>
    </section>
  );
}
