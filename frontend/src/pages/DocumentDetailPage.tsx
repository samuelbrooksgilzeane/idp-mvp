import { ArrowLeft, Clock3, LoaderCircle, Play, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { DocumentViewer, type CitationTarget } from "../components/DocumentViewer";
import { ExtractionPanel } from "../components/ExtractionPanel";
import { ValidationPanel } from "../components/ValidationPanel";
import type { ApiError, DocumentRecord, Notice, ParseRun } from "../types";

const TABS = ["Extraction", "Validation", "History"] as const;
type Tab = (typeof TABS)[number];

const RETRYABLE = [
  "PARSED",
  "PARSE_FAILED",
  "EXTRACTED",
  "EXTRACT_FAILED",
  "VALIDATED_PASS",
  "REVIEW_REQUIRED",
];

const formatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

type DocumentDetailPageProps = { onDocumentsChanged: () => void };

export function DocumentDetailPage({ onDocumentsChanged }: DocumentDetailPageProps) {
  const { documentId = "" } = useParams();
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing">("loading");
  const [runs, setRuns] = useState<ParseRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [tab, setTab] = useState<Tab>("Extraction");
  const [citationTarget, setCitationTarget] = useState<CitationTarget | null>(null);

  // Fetched by id rather than read from a list, so a deep link or refresh resolves on its own.
  const loadDocument = useCallback(async () => {
    try {
      const response = await fetch(`/api/documents/${documentId}`);
      if (!response.ok) throw new Error("Document request failed");
      setDocument((await response.json()) as DocumentRecord);
      setState("ready");
    } catch {
      setState("missing");
    }
  }, [documentId]);

  const loadRuns = useCallback(async () => {
    setRunsLoading(true);
    try {
      const response = await fetch(`/api/documents/${documentId}/parse-runs`);
      if (!response.ok) throw new Error("Parse history request failed");
      const history = (await response.json()) as ParseRun[];
      setRuns(history);
      setActiveRunId(history.find((run) => run.status === "RUNNING")?.parse_run_id ?? null);
    } catch {
      setNotice({ kind: "error", message: "Parse history is unavailable." });
    } finally {
      setRunsLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    setCitationTarget(null);
    setTab("Extraction");
    void loadDocument();
    void loadRuns();
  }, [loadDocument, loadRuns]);

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
        setRuns((current) => [
          run,
          ...current.filter((item) => item.parse_run_id !== run.parse_run_id),
        ]);
        if (run.status === "RUNNING") {
          timer = window.setTimeout(() => void poll(), 500);
          return;
        }
        setActiveRunId(null);
        setNotice({
          kind: run.status === "SUCCESS" ? "success" : "error",
          message:
            run.status === "SUCCESS"
              ? "Document parsed successfully."
              : "Document parsing failed.",
        });
        await loadDocument();
        onDocumentsChanged();
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
  }, [activeRunId, loadDocument, onDocumentsChanged]);

  async function handleParse() {
    if (!document) return;
    setStarting(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/documents/${document.document_id}/parse`, {
        method: "POST",
      });
      const payload = (await response.json()) as ParseRun | ApiError;
      if (!response.ok) {
        throw new Error("error" in payload ? payload.error.message : "Parsing could not start.");
      }
      const run = payload as ParseRun;
      setDocument({ ...document, status: "PARSING" });
      setRuns((current) => [run, ...current]);
      setActiveRunId(run.parse_run_id);
    } catch (error: unknown) {
      setNotice({
        kind: "error",
        message: error instanceof Error ? error.message : "Parsing could not start.",
      });
    } finally {
      setStarting(false);
    }
  }

  function showEvidence(target: CitationTarget) {
    // The viewer sits alongside the panel, so citing a value only has to move the highlight.
    setCitationTarget(target);
  }

  if (state === "loading") {
    return <p className="page-state">Loading document…</p>;
  }
  if (state === "missing" || !document) {
    return (
      <section className="page-state" role="alert">
        <strong>Document not found</strong>
        <Link to="/">Back to documents</Link>
      </section>
    );
  }

  const retry = RETRYABLE.includes(document.status);
  const unavailable = ["PARSING", "EXTRACTING"].includes(document.status) || starting;

  return (
    <section className="document-detail" aria-labelledby="detail-title">
      <Link className="back-link" to="/">
        <ArrowLeft size={14} aria-hidden="true" /> All documents
      </Link>

      <div className="detail-header">
        <div>
          <p className="eyebrow">Document detail</p>
          <h2 id="detail-title">{document.file_name}</h2>
          <span className="detail-identity">{document.document_id}</span>
        </div>
        <button
          className="parse-action"
          type="button"
          disabled={unavailable}
          onClick={() => void handleParse()}
        >
          {unavailable ? (
            <LoaderCircle className="spin" size={16} aria-hidden="true" />
          ) : retry ? (
            <RotateCcw size={16} aria-hidden="true" />
          ) : (
            <Play size={16} aria-hidden="true" />
          )}
          {document.status === "PARSING"
            ? "Parsing"
            : document.status === "EXTRACTING"
              ? "Extracting"
              : retry
                ? "Retry parse"
                : "Parse document"}
        </button>
      </div>

      <dl className="document-facts">
        <div>
          <dt>Status</dt>
          <dd>
            <span className={`status-label status-${document.status.toLowerCase()}`}>
              {document.status}
            </span>
          </dd>
        </div>
        <div><dt>Case</dt><dd>{document.case_id ?? "-"}</dd></div>
        <div><dt>Profile</dt><dd>{document.template_id}</dd></div>
        <div><dt>Uploaded by</dt><dd>{document.uploaded_by}</dd></div>
      </dl>

      {notice ? <p className={`notice notice-${notice.kind}`}>{notice.message}</p> : null}

      <div className="detail-tabs" role="tablist" aria-label="Document sections">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "active" : undefined}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </div>

      <div
        className={`detail-workspace${tab === "History" ? " detail-workspace-single" : ""}`}
      >
        {/* The source stays on screen beside the values, so citing a field highlights its
            region in place rather than navigating away. It also stays mounted while hidden,
            keeping its loaded page image and zoom across tab switches. */}
        <div className="detail-evidence" hidden={tab === "History"}>
          <DocumentViewer
            documentId={document.document_id}
            documentStatus={document.status}
            citationTarget={citationTarget}
          />
        </div>

        <div className="detail-panel" role="tabpanel">
        {tab === "Extraction" ? (
          <ExtractionPanel
            document={document}
            onViewEvidence={showEvidence}
            onDocumentsChanged={() => {
              void loadDocument();
              onDocumentsChanged();
            }}
          />
        ) : null}
        {tab === "Validation" ? (
          <ValidationPanel
            document={document}
            onViewEvidence={showEvidence}
            onDocumentsChanged={() => {
              void loadDocument();
              onDocumentsChanged();
            }}
          />
        ) : null}
        {tab === "History" ? (
          <>
            <div className="history-heading">
              <div><Clock3 size={16} aria-hidden="true" /><h3>Parse history</h3></div>
              <span>{runs.length} {runs.length === 1 ? "attempt" : "attempts"}</span>
            </div>
            {runsLoading ? <p className="history-state">Loading parse history...</p> : null}
            {!runsLoading && runs.length === 0 ? (
              <p className="history-state">No parse attempts recorded.</p>
            ) : null}
            {!runsLoading && runs.length > 0 ? (
              <ol className="run-history">
                {runs.map((run) => (
                  <li key={run.parse_run_id}>
                    <div>
                      <strong>{run.status}</strong>
                      <span>{formatter.format(new Date(run.started_at))}</span>
                    </div>
                    <dl>
                      <div><dt>Parser</dt><dd>{run.parser_version}</dd></div>
                      <div><dt>Pages</dt><dd>{run.page_count ?? "-"}</dd></div>
                      <div><dt>Run</dt><dd>{run.parse_run_id.slice(0, 8)}</dd></div>
                    </dl>
                    {run.parse_error ? (
                      <p className="run-error">{parseErrorMessage(run.parse_error)}</p>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : null}
          </>
        ) : null}
        </div>
      </div>
    </section>
  );
}

function parseErrorMessage(error: ParseRun["parse_error"]): string {
  if (!error) return "";
  if (Array.isArray(error)) return "The parser reported one or more page errors.";
  const message = error.error_message;
  return typeof message === "string" ? message : "The parse attempt failed.";
}
