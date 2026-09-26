import { useNavigate } from "react-router-dom";

import { BatchActions } from "../components/BatchActions";
import { DocumentList } from "../components/DocumentList";
import { UploadBatchProgress } from "../components/UploadBatchProgress";
import type { UploadBatchController } from "../hooks/useUploadBatch";
import { UploadPanel } from "../components/UploadPanel";
import { documentStatusLabel } from "../lib/documentStatus";
import { prefetchDocumentExtractionReview } from "../lib/extractionReviewPrefetch";
import type { DocumentRecord, DocumentStatus, Notice } from "../types";
import { useEffect, useMemo, useState } from "react";

type DocumentsPageProps = {
  upload?: UploadBatchController;
  documents: DocumentRecord[];
  loading: boolean;
  caseIds: string[];
  selectedCaseId: string | null;
  onCaseChanged: (caseId: string | null) => void;
  onDocumentsChanged: () => Promise<void> | void;
  status?: string;
  search?: string;
  onStatusChanged?: (status: string) => void;
  onSearchChanged?: (search: string) => void;
  pageError?: string | null;
  hasPrevious?: boolean;
  hasNext?: boolean;
  onPrevious?: () => void;
  onNext?: () => void;
  onReset?: () => void;
  selectionScope?: string;
};

export function DocumentsPage({
  upload,
  documents,
  loading,
  caseIds,
  selectedCaseId,
  onCaseChanged,
  onDocumentsChanged,
  status = "", search = "", onStatusChanged, onSearchChanged,
  pageError, hasPrevious, hasNext, onPrevious, onNext, onReset,
  selectionScope = "project",
}: DocumentsPageProps) {
  const navigate = useNavigate();
  const [notice, setNotice] = useState<Notice>(null);
  const selectionKey = `idp:document-selection:${selectionScope}`;
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(selectionKey) ?? "[]");
      return new Set(Array.isArray(saved) ? saved.filter((id): id is string => typeof id === "string").slice(0, 1000) : []);
    } catch { return new Set(); }
  });
  useEffect(() => {
    try { sessionStorage.setItem(selectionKey, JSON.stringify([...selectedIds])); } catch { /* Storage may be unavailable. */ }
  }, [selectedIds, selectionKey]);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const [searchInput, setSearchInput] = useState(search);
  useEffect(() => { setSearchInput(search); }, [search]);
  useEffect(() => {
    if (searchInput === search) return;
    const timeout = window.setTimeout(() => onSearchChanged?.(searchInput), 250);
    return () => window.clearTimeout(timeout);
  }, [searchInput, search, onSearchChanged]);
  // These are domain states, not facets inferred from the current page.
  const statuses: DocumentStatus[] = ["UPLOADED", "PARSE_QUEUED", "PARSING", "PARSED", "PARSE_FAILED", "EXTRACTING",
    "EXTRACTED", "EXTRACT_FAILED", "VALIDATING", "VALIDATED_PASS", "REVIEW_REQUIRED"];
  const selection = useMemo(() => [...selectedIds], [selectedIds]);
  const pageDocuments = documents;

  function toggleSelect(documentId: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else if (next.size < 1000) next.add(documentId);
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds((current) => {
      const allSelected =
        pageDocuments.length > 0 && pageDocuments.every((item) => current.has(item.document_id));
      const next = new Set(current);
      for (const item of pageDocuments) {
        if (allSelected) next.delete(item.document_id);
        else if (next.size < 1000) next.add(item.document_id);
      }
      return next;
    });
  }

  function changeCase(caseId: string | null) {
    onCaseChanged(caseId);
  }

  async function handleDelete(document: DocumentRecord) {
    if (!window.confirm(`Delete ${document.file_name} from the document registry? Extraction results will be kept.`)) return;
    setDeletingId(document.document_id);
    setNotice(null);
    try {
      const response = await fetch(`/api/documents/${document.document_id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("The document could not be deleted.");
      setSelectedIds((current) => {
        const next = new Set(current);
        next.delete(document.document_id);
        return next;
      });
      setNotice({ kind: "success", message: `${document.file_name} deleted. Extraction results were kept.` });
      await onDocumentsChanged();
    } catch (error: unknown) {
      setNotice({ kind: "error", message: error instanceof Error ? error.message : "The document could not be deleted." });
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <section className="intake-layout" aria-label="PDF parsing workspace">
      <div>
        <UploadPanel uploading={upload?.busy ?? false} notice={notice}
          onUpload={upload?.start ?? (async () => {})} resuming={Boolean(upload?.batch)}
          maxFiles={upload?.maxFiles} maxFileBytes={upload?.maxFileBytes} />
        {upload ? <UploadBatchProgress upload={upload} /> : null}
      </div>
      <div className="registry-workspace">
        <div className="registry-filters">
          <div className="registry-filter">
            <label htmlFor="document-case-filter">Case</label>
            <select
              id="document-case-filter"
              value={selectedCaseId ?? ""}
              onChange={(event) => changeCase(event.target.value || null)}
            >
              <option value="">All cases</option>
              {caseIds.map((caseId) => <option key={caseId} value={caseId}>{caseId}</option>)}
            </select>
          </div>
          <div className="registry-filter">
            <label htmlFor="document-status-filter">Status</label>
            <select
              id="document-status-filter"
              value={status}
              onChange={(event) => onStatusChanged?.(event.target.value)}
            >
              <option value="">All statuses</option>
              {statuses.map((item) => <option key={item} value={item}>{documentStatusLabel(item)}</option>)}
            </select>
          </div>
          <div className="registry-filter">
            <label htmlFor="document-name-filter">Search</label>
            <input
              id="document-name-filter"
              type="search"
              value={searchInput}
              placeholder="File name"
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </div>
          <p className="registry-filters-hint">
            Filters change the list. Your explicit selection stays selected across pages and
            filters (up to 1,000 documents). Clear it before starting a different selection.
          </p>
        </div>
        <BatchActions
          automaticPreparation={upload?.automaticPreparation}
          selectedIds={selection}
          onClear={() => setSelectedIds(new Set())}
          onDocumentsChanged={onDocumentsChanged}
        />
        <DocumentList
          documents={pageDocuments}
          filtered={Boolean(status || search.trim())}
          loading={loading}
          selectedDocumentId={null}
          onRefresh={() => void onDocumentsChanged()}
          onSelect={(document) => navigate(`/documents/${document.document_id}`)}
          onPreview={(document) => prefetchDocumentExtractionReview(document.document_id)}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onToggleAll={toggleAll}
          onDelete={(document) => void handleDelete(document)}
          deletingId={deletingId}
        />
        {pageError ? <p role="alert">{pageError} <button type="button" onClick={onReset}>Reset list</button></p> : null}
        <nav aria-label="Document pages">
          <button type="button" disabled={loading || !hasPrevious} onClick={onPrevious}>Previous page</button>
          <button type="button" disabled={loading || !hasNext} onClick={onNext}>Next page</button>
          <button type="button" disabled={loading} onClick={onReset}>First page</button>
        </nav>
      </div>
    </section>
  );
}
