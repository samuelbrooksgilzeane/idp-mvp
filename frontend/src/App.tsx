import { useCallback, useEffect, useState } from "react";

import { DocumentList } from "./components/DocumentList";
import { UploadPanel, type UploadInput } from "./components/UploadPanel";
import { WorkflowHeader } from "./components/WorkflowHeader";

export type HealthResponse = {
  status: "ok";
  mode: "mock" | "databricks";
  application_name: string;
  configuration: Record<string, boolean>;
};

export type DocumentRecord = {
  document_id: string;
  case_id: string | null;
  template_id: string;
  use_case: string;
  file_name: string;
  file_size: number;
  content_sha256: string;
  status: "UPLOADED";
  uploaded_by: string;
  uploaded_at: string;
  updated_at: string;
};

type UploadFailure = {
  file_name: string;
  code: string;
  message: string;
  document_id: string | null;
};

type UploadBatchResponse = {
  documents: DocumentRecord[];
  errors: UploadFailure[];
};

type RuntimeState =
  | { kind: "loading" }
  | { kind: "ready"; health: HealthResponse }
  | { kind: "unavailable" };

type Notice = { kind: "success" | "error"; message: string } | null;

export function App() {
  const [runtime, setRuntime] = useState<RuntimeState>({ kind: "loading" });
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

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
      const payload = (await response.json()) as
        | UploadBatchResponse
        | { error: { code: string; message: string } };
      if (!response.ok) {
        const message = "error" in payload ? payload.error.message : "Upload failed.";
        throw new Error(message);
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

  const appName = runtime.kind === "ready" ? runtime.health.application_name : "IDP MVP";
  const runtimeMode = runtime.kind === "ready" ? runtime.health.mode : "unknown";
  const apiStatus =
    runtime.kind === "ready"
      ? "Reachable"
      : runtime.kind === "loading"
        ? "Checking"
        : "Unavailable";

  return (
    <div className="app-shell">
      <WorkflowHeader appName={appName} activeStep={1} />
      <main>
        <section className="page-heading" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">Document registry</p>
            <h1 id="page-title">Document intake</h1>
            <p>Register source PDFs before any parsing or extraction begins.</p>
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

        <section className="intake-layout" aria-label="PDF intake workspace">
          <UploadPanel uploading={uploading} notice={notice} onUpload={handleUpload} />
          <DocumentList
            documents={documents}
            loading={documentsLoading}
            onRefresh={() => void loadDocuments()}
          />
        </section>
      </main>
      <footer><span>Secure PDF intake</span><span>Files are not parsed in this increment</span></footer>
    </div>
  );
}
