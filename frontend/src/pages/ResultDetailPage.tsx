import { ArrowLeft, ChevronDown, Download, LoaderCircle, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { DocumentViewer, type CitationTarget } from "../components/DocumentViewer";
import { type FieldPolicy, GenericResultView } from "../components/GenericResultView";
import type {
  DocumentRecord,
  GenericExtractionRecords,
  GenericExtractionResult,
} from "../types";

const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

type SchemaFieldPolicy = {
  field_path: string;
  confidence_threshold: number;
  citation_required: boolean;
};

export function ResultDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [result, setResult] = useState<GenericExtractionResult | null>(null);
  const [records, setRecords] = useState<GenericExtractionRecords | null>(null);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
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
      const [resultResponse, recordsResponse] = await Promise.all([
        fetch(`/api/extractions/${runId}`),
        fetch(`/api/extractions/${runId}/records`),
      ]);
      if (!resultResponse.ok || !recordsResponse.ok) throw new Error("Extraction result request failed");
      const resultPayload = (await resultResponse.json()) as GenericExtractionResult;
      const recordsPayload = (await recordsResponse.json()) as GenericExtractionRecords;
      setResult(resultPayload);
      setRecords(recordsPayload);

      const [documentResponse, schemaResponse] = await Promise.all([
        fetch(`/api/documents/${resultPayload.run.document_id}`),
        fetch(
          `/api/schemas/${resultPayload.schema_id}/versions/${resultPayload.schema_version}`,
        ),
      ]);
      if (documentResponse.ok) setDocument((await documentResponse.json()) as DocumentRecord);
      if (schemaResponse.ok) {
        const schema = (await schemaResponse.json()) as { fields: SchemaFieldPolicy[] };
        setFieldPolicies(
          new Map(
            schema.fields.map((field) => [
              field.field_path,
              { confidenceThreshold: field.confidence_threshold, citationRequired: field.citation_required },
            ]),
          ),
        );
      }
      setState("ready");
    } catch {
      setState("missing");
    }
  }, [runId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRerun() {
    if (!result) return;
    setRerunning(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/documents/${result.run.document_id}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema_id: result.schema_id,
          schema_version: result.schema_version,
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
  if (state === "missing" || !result || !records) {
    return (
      <section className="page-state" role="alert">
        <strong>Extraction run not found</strong>
        <Link to="/results">Back to results</Link>
      </section>
    );
  }

  return (
    <section className="document-detail result-detail" aria-labelledby="result-detail-title">
      <Link className="back-link" to="/results">
        <ArrowLeft size={14} aria-hidden="true" /> All results
      </Link>

      <div className="detail-header">
        <div>
          <p className="eyebrow">{document?.file_name ?? result.run.document_id}</p>
          <h2 id="result-detail-title">
            {result.schema_id} · v{result.schema_version}
          </h2>
          <span className="detail-identity">
            Extracted {formatter.format(new Date(result.run.started_at))} ·{" "}
            <span className={`status-label status-${result.run.status.toLowerCase()}`}>
              {result.run.status}
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
          <div><dt>Run ID</dt><dd>{result.run.extraction_run_id}</dd></div>
          <div><dt>Schema hash</dt><dd className="run-details-hash">{result.run.schema_hash}</dd></div>
          <div><dt>Parse run</dt><dd>{result.run.parse_run_id}</dd></div>
          <div><dt>Options</dt><dd>{JSON.stringify(result.run.options)}</dd></div>
        </dl>
      </details>

      <div className="detail-workspace">
        <div className="detail-evidence">
          {document ? (
            <DocumentViewer
              documentId={document.document_id}
              documentStatus={document.status}
              citationTarget={citationTarget}
            />
          ) : null}
        </div>
        <div className="detail-panel">
          <GenericResultView
            rootMode={result.root_mode}
            hierarchy={result.result}
            fields={records.fields}
            fieldPolicies={fieldPolicies}
            onViewEvidence={setCitationTarget}
          />
        </div>
      </div>
    </section>
  );
}
