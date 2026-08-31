import { useCallback, useEffect, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { WorkflowHeader } from "./components/WorkflowHeader";
import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { ResultDetailPage } from "./pages/ResultDetailPage";
import { ResultsPage } from "./pages/ResultsPage";
import { SchemaPage } from "./pages/SchemaPage";
import type { DocumentRecord, HealthResponse } from "./types";

export type {
  ApiError,
  DocumentRecord,
  DocumentStatus,
  HealthResponse,
  Notice,
  ParseRun,
} from "./types";

type RuntimeState =
  | { kind: "loading" }
  | { kind: "ready"; health: HealthResponse }
  | { kind: "unavailable" };

const HEADINGS: Record<string, { eyebrow: string; title: string; blurb: string }> = {
  documents: {
    eyebrow: "Document processing",
    title: "Upload and track documents",
    blurb: "Register PDFs and follow each one through parsing, extraction and validation.",
  },
  detail: {
    eyebrow: "Document processing",
    title: "Inspect a document",
    blurb: "Review parsed pages, extracted fields with evidence, and validation exceptions.",
  },
  results: {
    eyebrow: "Reporting",
    title: "Results and export",
    blurb: "Review every extraction run and export the ones you need.",
  },
  "result-detail": {
    eyebrow: "Reporting",
    title: "Extraction run",
    blurb: "Review one run's result beside its source, with citations and confidence.",
  },
  schema: {
    eyebrow: "Governance",
    title: "Extraction contract",
    blurb: "The approved, versioned schema every extraction is measured against.",
  },
};

export function App() {
  const [runtime, setRuntime] = useState<RuntimeState>({ kind: "loading" });
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const [documentCaseId, setDocumentCaseId] = useState<string | null>(null);
  const location = useLocation();
  const isRegistryRoute = location.pathname === "/";

  const loadDocuments = useCallback(async (caseId: string | null, signal?: AbortSignal) => {
    setDocumentsLoading(true);
    try {
      const query = caseId ? `?case_id=${encodeURIComponent(caseId)}` : "";
      const response = await fetch(`/api/documents${query}`, { signal });
      if (!response.ok) throw new Error("Documents request failed");
      setDocuments((await response.json()) as DocumentRecord[]);
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setDocuments([]);
      }
    } finally {
      if (!signal?.aborted) setDocumentsLoading(false);
    }
  }, []);

  const loadCaseIds = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch("/api/documents/cases", { signal });
      if (!response.ok) throw new Error("Cases request failed");
      const payload = (await response.json()) as unknown;
      setCaseIds(
        Array.isArray(payload)
          ? payload.filter((item): item is string => typeof item === "string")
          : [],
      );
    } catch (error: unknown) {
      if (!(error instanceof DOMException && error.name === "AbortError")) setCaseIds([]);
    }
  }, []);

  const refreshDocuments = useCallback(async () => {
    if (!isRegistryRoute) return;
    await Promise.all([loadDocuments(documentCaseId), loadCaseIds()]);
  }, [documentCaseId, isRegistryRoute, loadCaseIds, loadDocuments]);

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
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!isRegistryRoute) {
      setDocumentsLoading(false);
      return;
    }
    const controller = new AbortController();
    void loadDocuments(documentCaseId, controller.signal);
    return () => controller.abort();
  }, [documentCaseId, isRegistryRoute, loadDocuments]);

  useEffect(() => {
    if (!isRegistryRoute) return;
    const controller = new AbortController();
    void loadCaseIds(controller.signal);
    return () => controller.abort();
  }, [isRegistryRoute, loadCaseIds]);

  const appName = runtime.kind === "ready" ? runtime.health.application_name : "IDP MVP";
  const runtimeMode = runtime.kind === "ready" ? runtime.health.mode : "unknown";
  const apiStatus =
    runtime.kind === "ready" ? "Reachable" : runtime.kind === "loading" ? "Checking" : "Unavailable";
  const heading = HEADINGS[sectionFor(location.pathname)];

  return (
    <div className="app-shell">
      <WorkflowHeader appName={appName} />
      <main>
        <section className="page-heading" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">{heading.eyebrow}</p>
            <h1 id="page-title">{heading.title}</h1>
            <p>{heading.blurb}</p>
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

        <Routes>
          <Route
            path="/"
            element={
              <DocumentsPage
                documents={documents}
                loading={documentsLoading}
                caseIds={caseIds}
                selectedCaseId={documentCaseId}
                onCaseChanged={(caseId) => {
                  setDocumentCaseId(caseId);
                }}
                onDocumentsChanged={refreshDocuments}
              />
            }
          />
          <Route
            path="/documents/:documentId"
            element={<DocumentDetailPage onDocumentsChanged={() => void refreshDocuments()} />}
          />
          <Route path="/results" element={<ResultsPage />} />
          <Route path="/results/:runId" element={<ResultDetailPage />} />
          <Route path="/schema" element={<SchemaPage />} />
        </Routes>
      </main>
      <footer>
        <span>Retained parser contract 2.0</span>
        <span>Approved schema registry enabled</span>
      </footer>
    </div>
  );
}

function sectionFor(pathname: string): string {
  if (pathname.startsWith("/documents/")) return "detail";
  if (pathname.startsWith("/results/")) return "result-detail";
  if (pathname.startsWith("/results")) return "results";
  if (pathname.startsWith("/schema")) return "schema";
  return "documents";
}
