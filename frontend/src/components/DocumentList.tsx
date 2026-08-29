import { FileText, RefreshCw } from "lucide-react";

import type { DocumentRecord } from "../types";

type DocumentListProps = {
  documents: DocumentRecord[];
  loading: boolean;
  selectedDocumentId: string | null;
  onRefresh: () => void;
  onSelect: (document: DocumentRecord) => void;
  selectedIds?: Set<string>;
  onToggleSelect?: (documentId: string) => void;
  onToggleAll?: () => void;
};

const formatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export function DocumentList({
  documents,
  loading,
  selectedDocumentId,
  onRefresh,
  onSelect,
  selectedIds,
  onToggleSelect,
  onToggleAll,
}: DocumentListProps) {
  const selectable = Boolean(selectedIds && onToggleSelect && onToggleAll);
  const allSelected = Boolean(
    selectable && documents.length > 0 && documents.every((item) => selectedIds?.has(item.document_id)),
  );
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
              <tr>
                {selectable ? (
                  <th className="select-cell">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={onToggleAll}
                      aria-label={allSelected ? "Clear selection" : "Select all documents"}
                    />
                  </th>
                ) : null}
                <th>Document</th><th>Case</th><th>Status</th><th>Uploaded</th><th>Size</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((document) => (
                <tr
                  className={document.document_id === selectedDocumentId ? "selected-row" : undefined}
                  key={document.document_id}
                >
                  {selectable ? (
                    <td className="select-cell">
                      <input
                        type="checkbox"
                        checked={selectedIds?.has(document.document_id) ?? false}
                        onChange={() => onToggleSelect?.(document.document_id)}
                        aria-label={`Select ${document.file_name}`}
                      />
                    </td>
                  ) : null}
                  <td>
                    <button
                      className="document-link"
                      type="button"
                      onClick={() => onSelect(document)}
                    >
                      {document.file_name}
                    </button>
                    <span className="document-id">{document.document_id.slice(0, 8)}</span>
                  </td>
                  <td>{document.case_id ?? "-"}</td>
                  <td>
                    <span className={`status-label status-${document.status.toLowerCase()}`}>
                      {document.status}
                    </span>
                  </td>
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
