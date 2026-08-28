import { Clock3, FileSearch, LoaderCircle, Play, RotateCcw } from "lucide-react";

import type { DocumentRecord, ParseRun } from "../App";
import { DocumentViewer } from "./DocumentViewer";

type DocumentDetailProps = {
  document: DocumentRecord | null;
  runs: ParseRun[];
  loading: boolean;
  starting: boolean;
  onParse: () => void;
};

const formatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function DocumentDetail({
  document,
  runs,
  loading,
  starting,
  onParse,
}: DocumentDetailProps) {
  if (!document) {
    return (
      <section className="document-detail empty-detail" aria-label="Document detail">
        <FileSearch size={24} aria-hidden="true" />
        <strong>Select a document</strong>
        <span>Choose a registered PDF to inspect its parse status.</span>
      </section>
    );
  }

  const retry = document.status === "PARSED" || document.status === "PARSE_FAILED";
  const unavailable = document.status === "PARSING" || starting;

  return (
    <section className="document-detail" aria-labelledby="detail-title">
      <div className="detail-header">
        <div>
          <p className="eyebrow">Document detail</p>
          <h2 id="detail-title">{document.file_name}</h2>
          <span className="detail-identity">{document.document_id}</span>
        </div>
        <button className="parse-action" type="button" disabled={unavailable} onClick={onParse}>
          {unavailable ? (
            <LoaderCircle className="spin" size={16} aria-hidden="true" />
          ) : retry ? (
            <RotateCcw size={16} aria-hidden="true" />
          ) : (
            <Play size={16} aria-hidden="true" />
          )}
          {document.status === "PARSING" ? "Parsing" : retry ? "Retry parse" : "Parse document"}
        </button>
      </div>

      <dl className="document-facts">
        <div><dt>Status</dt><dd><span className={`status-label status-${document.status.toLowerCase()}`}>{document.status}</span></dd></div>
        <div><dt>Case</dt><dd>{document.case_id ?? "-"}</dd></div>
        <div><dt>Profile</dt><dd>{document.template_id}</dd></div>
        <div><dt>Uploaded by</dt><dd>{document.uploaded_by}</dd></div>
      </dl>

      <DocumentViewer
        documentId={document.document_id}
        documentStatus={document.status}
      />

      <div className="history-heading">
        <div><Clock3 size={16} aria-hidden="true" /><h3>Parse history</h3></div>
        <span>{runs.length} {runs.length === 1 ? "attempt" : "attempts"}</span>
      </div>
      {loading ? <p className="history-state">Loading parse history...</p> : null}
      {!loading && runs.length === 0 ? <p className="history-state">No parse attempts recorded.</p> : null}
      {!loading && runs.length > 0 ? (
        <ol className="run-history">
          {runs.map((run) => (
            <li key={run.parse_run_id}>
              <div><strong>{run.status}</strong><span>{formatter.format(new Date(run.started_at))}</span></div>
              <dl>
                <div><dt>Parser</dt><dd>{run.parser_version}</dd></div>
                <div><dt>Pages</dt><dd>{run.page_count ?? "-"}</dd></div>
                <div><dt>Run</dt><dd>{run.parse_run_id.slice(0, 8)}</dd></div>
              </dl>
              {run.parse_error ? <p className="run-error">{parseErrorMessage(run.parse_error)}</p> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function parseErrorMessage(error: ParseRun["parse_error"]): string {
  if (!error) return "";
  if (Array.isArray(error)) return "The parser reported one or more page errors.";
  const message = error.error_message;
  return typeof message === "string" ? message : "The parse attempt failed.";
}
