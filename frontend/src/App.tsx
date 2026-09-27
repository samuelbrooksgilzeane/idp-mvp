import { invalidateListPages } from "./hooks/useCursorPage";
import { setCacheScope } from "./lib/requestCache";
import { invalidateDocumentReviews } from "./lib/extractionReviewPrefetch";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { WorkflowHeader } from "./components/WorkflowHeader";
const DocumentDetailPage = lazy(() => import("./pages/DocumentDetailPage").then(module => ({ default: module.DocumentDetailPage })));
import { DocumentsPage } from "./pages/DocumentsPage";
const ResultDetailPage = lazy(() => import("./pages/ResultDetailPage").then(module => ({ default: module.ResultDetailPage })));
import { ResultsPage } from "./pages/ResultsPage";
const SchemaPage = lazy(() => import("./pages/SchemaPage").then(module => ({ default: module.SchemaPage })));
import { useUploadBatch } from "./hooks/useUploadBatch";
import { useDocumentPage } from "./hooks/useDocumentPage";
import type { HealthResponse } from "./types";

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
  const [scope, setScope] = useState("initial");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const location = useLocation();
  const isRegistryRoute = location.pathname === "/";

  const registry = useDocumentPage(isRegistryRoute);
  const refreshRegistry = registry.refresh;

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
    invalidateDocumentReviews();
    invalidateListPages();
    if (!isRegistryRoute) return;
    refreshRegistry();
    await loadCaseIds();
  }, [isRegistryRoute, loadCaseIds, refreshRegistry]);

  const upload = useUploadBatch(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null; invalidateDocumentReviews(); void refreshDocuments();
    }, 750);
  });
  useEffect(() => () => { if (refreshTimer.current) clearTimeout(refreshTimer.current); }, []);
  useEffect(() => {
    let active = true;
    const updateScope = () => {
      void fetch("/api/upload-batches/limits").then(response => {
        if (!response.ok) { setCacheScope("signed-out"); setScope("signed-out"); throw new Error("Unavailable"); }
        return response.json();
      }).then(value => {
        if (active && typeof value.cache_scope === "string") {
          setCacheScope(value.cache_scope); setScope(value.cache_scope);
        }
      }).catch(() => undefined);
    };
    updateScope(); window.addEventListener("focus", updateScope);
    return () => { active = false; window.removeEventListener("focus", updateScope); };
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
    return () => controller.abort();
  }, []);

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

        <Suspense fallback={<div className="results-state" role="status">Loading view…</div>}>
        <Routes key={scope}>
          <Route
            path="/"
            element={
              <DocumentsPage
                key={appName}
                upload={upload}
                documents={registry.documents}
                loading={registry.loading}
                caseIds={caseIds}
                selectedCaseId={registry.caseId}
                onCaseChanged={(caseId) => registry.changeFilter("case", caseId)}
                status={registry.status}
                search={registry.search}
                onStatusChanged={(status) => registry.changeFilter("status", status)}
                onSearchChanged={(search) => registry.changeFilter("search", search)}
                pageError={registry.error}
                hasPrevious={registry.previousCursor !== undefined}
                hasNext={Boolean(registry.nextCursor)}
                onPrevious={() => registry.changeCursor(registry.previousCursor ?? "")}
                onNext={() => registry.changeCursor(registry.nextCursor ?? "")}
                onReset={() => registry.changeCursor("")}
                selectionScope={scope}
                nextCursor={registry.nextCursor}
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
        </Suspense>
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
