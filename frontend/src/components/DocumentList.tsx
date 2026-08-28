import { FileText, RefreshCw } from "lucide-react";

import type { DocumentRecord } from "../App";

type DocumentListProps = {
  documents: DocumentRecord[];
  loading: boolean;
  onRefresh: () => void;
};

const formatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export function DocumentList({ documents, loading, onRefresh }: DocumentListProps) {
  return (
    <section className="registry" aria-labelledby="registry-title">
      <div className="registry-header">
        <div>
          <p className="eyebrow">Current intake</p>
          <h2 id="registry-title">Registered documents</h2>
        </div>
        <div className="registry-actions">
          <span>{documents.length} {documents.length === 1 ? "document" : "documents"}</span>
          <button
            className="icon-button"
            type="button"
            onClick={onRefresh}
            aria-label="Refresh documents"
            title="Refresh documents"
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
        </div>
      </div>

      {loading ? <p className="registry-state">Loading registry...</p> : null}
      {!loading && !documents.length ? (
        <div className="empty-registry">
          <FileText size={24} aria-hidden="true" />
          <strong>No documents registered</strong>
          <span>Uploaded PDFs will appear here.</span>
        </div>
      ) : null}
      {!loading && documents.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>Document</th><th>Case</th><th>Status</th><th>Uploaded</th><th>Size</th></tr>
            </thead>
            <tbody>
              {documents.map((document) => (
                <tr key={document.document_id}>
                  <td>
                    <strong>{document.file_name}</strong>
                    <span className="document-id">{document.document_id.slice(0, 8)}</span>
                  </td>
                  <td>{document.case_id ?? "-"}</td>
                  <td><span className="status-label">{document.status}</span></td>
                  <td>{formatter.format(new Date(document.uploaded_at))}</td>
                  <td>{formatBytes(document.file_size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
