import { invalidateListPages } from "./hooks/useCursorPage";
import { setCacheScope } from "./lib/requestCache";
import { invalidateDocumentReviews } from "./lib/extractionReviewPrefetch";
import { ChevronRight } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Route, Routes, useLocation } from "react-router-dom";

import { WorkflowHeader } from "./components/WorkflowHeader";
const DocumentDetailPage = lazy(() => import("./pages/DocumentDetailPage").then(module => ({ default: module.DocumentDetailPage })));
import { DocumentsPage } from "./pages/DocumentsPage";
const ResultDetailPage = lazy(() => import("./pages/ResultDetailPage").then(module => ({ default: module.ResultDetailPage })));
import { ResultsPage } from "./pages/ResultsPage";
const SchemaPage = lazy(() => import("./pages/SchemaPage").then(module => ({ default: module.SchemaPage })));
import { useUploadBatch } from "./hooks/useUploadBatch";
import { useFolderImport } from "./hooks/useFolderImport";
import { useDocumentPage } from "./hooks/useDocumentPage";
import type { AppConfig, HealthResponse } from "./types";

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

const HEADINGS: Record<string, { crumbs: string[]; title?: string; blurb?: string }> = {
  documents: {
    crumbs: ["Documents"],
    title: "Documents",
    blurb: "Upload PDFs, prepare them, then select documents and a schema to extract.",
  },
  detail: { crumbs: ["Documents", "Document"] },
  results: {
    crumbs: ["Results"],
    title: "Results",
    blurb: "Every extraction run. Filter, open one to review it beside its source, or export.",
  },
  "result-detail": { crumbs: ["Results", "Extraction run"] },
  schema: {
    crumbs: ["Schemas"],
    title: "Schemas",
    blurb: "A schema lists the fields to extract from a document. Choose one when you extract.",
  },
};

const viewLoading = <div className="results-state" role="status">Loading view…</div>;

export function App() {
  const [runtime, setRuntime] = useState<RuntimeState>({ kind: "loading" });
  const [scope, setScope] = useState("initial");
  // Pages wait for the first scope answer (no warehouse work, so it is quick). Rendering them
  // under the placeholder scope meant remounting them under the real one moments later, and
  // every page requested all of its data twice on each load.
  const [scopeSettled, setScopeSettled] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const [chatAppUrl, setChatAppUrl] = useState<string | null>(null);
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
  // Polls server-side progress; owned here so it keeps going while the user navigates.
  const folderImport = useFolderImport(() => {
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
      }).catch(() => undefined).finally(() => { if (active) setScopeSettled(true); });
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
    const controller = new AbortController();
    fetch("/api/app-config", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Configuration request failed");
        return response.json() as Promise<AppConfig>;
      })
      .then((config) => setChatAppUrl(config.chat_app_url ?? null))
      .catch(() => undefined);
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

  const blurb = sectionFor(location.pathname) === "documents" && upload.automaticPreparation
    ? "Upload PDFs — they are prepared automatically. Then select documents and a schema to extract."
    : heading.blurb;

  return (
    <div className="app-shell">
      <WorkflowHeader appName={appName} runtimeMode={runtimeMode} apiStatus={apiStatus} chatAppUrl={chatAppUrl} />
      <div className="app-main">
      <header className="top-bar" aria-label="Location">
        {heading.crumbs.map((crumb, index) => index === heading.crumbs.length - 1
          ? <strong key={crumb}>{crumb}</strong>
          : <span className="crumb" key={crumb}>{crumb}<ChevronRight size={14} aria-hidden="true" /></span>)}
      </header>
      <main>
        {heading.title ? (
          <section className="page-heading" aria-labelledby="page-title">
            <div>
              <h1 id="page-title">{heading.title}</h1>
              <p>{blurb}</p>
            </div>
          </section>
        ) : null}

        <Suspense fallback={viewLoading}>
        {!scopeSettled ? viewLoading : (
        <Routes key={scope}>
          <Route
            path="/"
            element={
              <DocumentsPage
                key={appName}
                upload={upload}
                folderImport={folderImport}
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
        )}
        </Suspense>
      </main>
      </div>
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
