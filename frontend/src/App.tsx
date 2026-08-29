import { useCallback, useEffect, useMemo, useState } from "react";

import { DocumentDetail } from "./components/DocumentDetail";
import { DocumentList } from "./components/DocumentList";
import { UploadPanel, type UploadInput } from "./components/UploadPanel";
import { WorkflowHeader } from "./components/WorkflowHeader";

export type HealthResponse = {
  status: "ok";
  mode: "mock" | "databricks";
  application_name: string;
  configuration: Record<string, boolean>;
};

export type DocumentStatus = "UPLOADED" | "PARSING" | "PARSED" | "PARSE_FAILED";

export type DocumentRecord = {
  document_id: string;
  case_id: string | null;
  template_id: string;
  use_case: string;
  file_name: string;
  file_size: number;
  content_sha256: string;
  status: DocumentStatus;
  uploaded_by: string;
  uploaded_at: string;
  updated_at: string;
};

export type ParseRun = {
  parse_run_id: string;
  document_id: string;
  parser_version: "2.0";
  status: "RUNNING" | "SUCCESS" | "FAILED";
  page_count: number | null;
  parse_error: Record<string, unknown> | unknown[] | null;
  requested_by: string;
  started_at: string;
  completed_at: string | null;
};

type UploadFailure = {
  file_name: string;
  code: string;
  message: string;
  document_id: string | null;
};
type UploadBatchResponse = { documents: DocumentRecord[]; errors: UploadFailure[] };
type ApiError = { error: { code: string; message: string } };
type RuntimeState =
  | { kind: "loading" }
  | { kind: "ready"; health: HealthResponse }
  | { kind: "unavailable" };
type Notice = { kind: "success" | "error"; message: string } | null;

export function App() {
  const [runtime, setRuntime] = useState<RuntimeState>({ kind: "loading" });
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [runs, setRuns] = useState<ParseRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [startingParse, setStartingParse] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const selectedDocument = useMemo(
    () => documents.find((document) => document.document_id === selectedDocumentId) ?? null,
    [documents, selectedDocumentId],
  );

  const loadDocuments = useCallback(async (signal?: AbortSignal) => {
    setDocumentsLoading(true);
    try {
      const response = await fetch("/api/documents", { signal });
      if (!response.ok) throw new Error("Documents request failed");
      setDocuments((await response.json()) as DocumentRecord[]);
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setNotice({ kind: "error", message: "The document registry is unavailable." });
      }
    } finally {
      if (!signal?.aborted) setDocumentsLoading(false);
    }
  }, []);

  const loadRuns = useCallback(async (documentId: string) => {
    setRunsLoading(true);
    try {
      const response = await fetch(`/api/documents/${documentId}/parse-runs`);
      if (!response.ok) throw new Error("Parse history request failed");
      const history = (await response.json()) as ParseRun[];
      setRuns(history);
      const running = history.find((run) => run.status === "RUNNING");
      setActiveRunId(running?.parse_run_id ?? null);
    } catch {
      setNotice({ kind: "error", message: "Parse history is unavailable." });
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/health", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health request failed");
        return response.json() as Promise<HealthResponse>;
      })
      .then((health) => setRuntime({ kind: "ready", health }))
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setRuntime({ kind: "unavailable" });
        }
      });
    void loadDocuments(controller.signal);
    return () => controller.abort();
  }, [loadDocuments]);

  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await fetch(`/api/runs/${activeRunId}`);
        if (!response.ok) throw new Error("Run status request failed");
        const run = (await response.json()) as ParseRun;
        if (cancelled) return;
        setRuns((current) => [run, ...current.filter((item) => item.parse_run_id !== run.parse_run_id)]);
        if (run.status === "RUNNING") {
          timer = window.setTimeout(() => void poll(), 500);
        } else {
          setActiveRunId(null);
          setNotice({
            kind: run.status === "SUCCESS" ? "success" : "error",
            message: run.status === "SUCCESS" ? "Document parsed successfully." : "Document parsing failed.",
          });
          await loadDocuments();
          if (selectedDocumentId) await loadRuns(selectedDocumentId);
        }
      } catch {
        if (!cancelled) {
          setActiveRunId(null);
          setNotice({ kind: "error", message: "Parse status polling failed." });
        }
      }
    };
    timer = window.setTimeout(() => void poll(), 250);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeRunId, loadDocuments, loadRuns, selectedDocumentId]);

  async function handleUpload(input: UploadInput) {
    setUploading(true);
    setNotice(null);
    const body = new FormData();
    input.files.forEach((file) => body.append("files", file));
    if (input.caseId.trim()) body.append("case_id", input.caseId.trim());
    body.append("template_id", "invoice_v1");
    body.append("use_case", "invoice");
    try {
      const response = await fetch("/api/documents", { method: "POST", body });
      const payload = (await response.json()) as UploadBatchResponse | ApiError;
      if (!response.ok) {
        throw new Error("error" in payload ? payload.error.message : "Upload failed.");
      }
      const result = payload as UploadBatchResponse;
      const accepted = result.documents.length;
      setNotice(
        result.errors.length
          ? { kind: "error", message: result.errors.map((error) => error.message).join(" ") }
          : {
              kind: "success",
              message: `${accepted} ${accepted === 1 ? "document" : "documents"} registered.`,
            },
      );
      await loadDocuments();
    } catch (error: unknown) {
      setNotice({
        kind: "error",
        message: error instanceof Error ? error.message : "Upload failed.",
      });
    } finally {
      setUploading(false);
    }
  }

  async function handleSelect(document: DocumentRecord) {
    setSelectedDocumentId(document.document_id);
    setRuns([]);
    await loadRuns(document.document_id);
  }

  async function handleParse() {
    if (!selectedDocument) return;
    setStartingParse(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/documents/${selectedDocument.document_id}/parse`, {
        method: "POST",
      });
      const payload = (await response.json()) as ParseRun | ApiError;
      if (!response.ok) {
        throw new Error("error" in payload ? payload.error.message : "Parsing could not start.");
      }
      const run = payload as ParseRun;
      setDocuments((current) =>
        current.map((document) =>
          document.document_id === selectedDocument.document_id
            ? { ...document, status: "PARSING" }
            : document,
        ),
      );
      setRuns((current) => [run, ...current]);
      setActiveRunId(run.parse_run_id);
    } catch (error: unknown) {
      setNotice({
        kind: "error",
        message: error instanceof Error ? error.message : "Parsing could not start.",
      });
    } finally {
      setStartingParse(false);
    }
  }

  const appName = runtime.kind === "ready" ? runtime.health.application_name : "IDP MVP";
  const runtimeMode = runtime.kind === "ready" ? runtime.health.mode : "unknown";
  const apiStatus =
    runtime.kind === "ready" ? "Reachable" : runtime.kind === "loading" ? "Checking" : "Unavailable";

  return (
    <div className="app-shell">
      <WorkflowHeader appName={appName} activeStep={3} />
      <main>
        <section className="page-heading" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">Document processing</p>
            <h1 id="page-title">Inspect and prepare extraction</h1>
            <p>Parse PDFs, inspect detected regions, and review the approved extraction contract.</p>
          </div>
          <dl className="runtime-summary" aria-label="Runtime status">
            <div><dt>Runtime</dt><dd>{runtimeMode}</dd></div>
            <div>
              <dt>API</dt>
              <dd className={`status-${apiStatus.toLowerCase()}`}>
                <span className="status-dot" aria-hidden="true" />{apiStatus}
              </dd>
            </div>
          </dl>
        </section>
        <section className="intake-layout" aria-label="PDF parsing workspace">
          <UploadPanel uploading={uploading} notice={notice} onUpload={handleUpload} />
          <div className="registry-workspace">
            <DocumentList
              documents={documents}
              loading={documentsLoading}
              selectedDocumentId={selectedDocumentId}
              onRefresh={() => void loadDocuments()}
              onSelect={(document) => void handleSelect(document)}
            />
            <DocumentDetail
              document={selectedDocument}
              runs={runs}
              loading={runsLoading}
              starting={startingParse}
              onParse={() => void handleParse()}
            />
          </div>
        </section>
      </main>
      <footer>
        <span>Retained parser contract 2.0</span>
        <span>Approved schema registry enabled</span>
      </footer>
    </div>
  );
}
