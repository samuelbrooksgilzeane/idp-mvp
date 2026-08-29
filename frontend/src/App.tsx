import { useCallback, useEffect, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { WorkflowHeader } from "./components/WorkflowHeader";
import { DocumentDetailPage } from "./pages/DocumentDetailPage";
import { DocumentsPage } from "./pages/DocumentsPage";
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
    blurb: "Compare invoices across a case and export the detail for review.",
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
  const location = useLocation();

  const loadDocuments = useCallback(async (signal?: AbortSignal) => {
    setDocumentsLoading(true);
    try {
      const response = await fetch("/api/documents", { signal });
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
                onDocumentsChanged={loadDocuments}
              />
            }
          />
          <Route
            path="/documents/:documentId"
            element={<DocumentDetailPage onDocumentsChanged={() => void loadDocuments()} />}
          />
          <Route path="/results" element={<ResultsPage />} />
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
  if (pathname.startsWith("/results")) return "results";
  if (pathname.startsWith("/schema")) return "schema";
  return "documents";
}
