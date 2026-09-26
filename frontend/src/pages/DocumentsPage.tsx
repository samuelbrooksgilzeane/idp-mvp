import { useNavigate } from "react-router-dom";

import { BatchActions } from "../components/BatchActions";
import { DocumentList } from "../components/DocumentList";
import { UploadPanel, type UploadInput } from "../components/UploadPanel";
import { prefetchDocumentExtractionReview } from "../lib/extractionReviewPrefetch";
import type { ApiError, DocumentRecord, Notice } from "../types";
import { useEffect, useMemo, useState } from "react";

type UploadFailure = {
  file_name: string;
  code: string;
  message: string;
  document_id: string | null;
};
type UploadBatchResponse = { documents: DocumentRecord[]; errors: UploadFailure[] };

type DocumentsPageProps = {
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
  const [uploading, setUploading] = useState(false);
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
  const statuses = ["UPLOADED", "PARSING", "PARSED", "PARSE_FAILED", "EXTRACTING",
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

  async function handleUpload(input: UploadInput) {
    setUploading(true);
    setNotice(null);
    // Upload no longer assigns any one extraction schema: a schema is chosen separately, at
    // extraction time, from whichever published schemas exist (see BatchActions / SchemaPage).
    try {
      // Send one PDF per request. App gateways can reject a combined multipart request before
      // FastAPI sees it, returning an HTML error page that cannot be parsed as JSON. Isolating
      // files keeps request bodies bounded and lets the remaining PDFs continue after one fails.
      const registered: DocumentRecord[] = [];
      const failures: UploadFailure[] = [];
      for (const file of input.files) {
        const body = new FormData();
        body.append("files", file);
        if (input.caseId.trim()) body.append("case_id", input.caseId.trim());
        try {
          const response = await fetch("/api/documents", { method: "POST", body });
          const payload = await readUploadResponse(response);
          if (!response.ok) {
            failures.push({
              file_name: file.name,
              code: `HTTP_${response.status}`,
              message: uploadFailureMessage(file.name, response.status, payload),
              document_id: apiErrorDocumentId(payload),
            });
            continue;
          }
          if (!isUploadBatchResponse(payload)) {
            failures.push({
              file_name: file.name,
              code: "INVALID_UPLOAD_RESPONSE",
              message: `${file.name}: the upload service returned an unexpected response.`,
              document_id: null,
            });
            continue;
          }
          registered.push(...payload.documents);
          failures.push(...payload.errors);
        } catch (error: unknown) {
          failures.push({
            file_name: file.name,
            code: "UPLOAD_REQUEST_FAILED",
            message: `${file.name}: ${
              error instanceof Error ? error.message : "the upload request failed."
            }`,
            document_id: null,
          });
        }
      }

      const accepted = registered.length;
      setNotice(
        failures.length
          ? {
              kind: "error",
              message:
                input.files.length === 1 && failures.length === 1
                  ? failures[0].message
                  : `${accepted} of ${input.files.length} registered. ${failures
                      .map((error) => error.message)
                      .join(" ")}`,
            }
          : {
              kind: "success",
              message: `${accepted} ${accepted === 1 ? "document" : "documents"} registered.`,
            },
      );
      if (accepted) await onDocumentsChanged();
    } catch (error: unknown) {
      setNotice({
        kind: "error",
        message: error instanceof Error ? error.message : "Upload failed.",
      });
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="intake-layout" aria-label="PDF parsing workspace">
      <UploadPanel uploading={uploading} notice={notice} onUpload={handleUpload} />
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
              {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
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

async function readUploadResponse(
  response: Pick<Response, "ok" | "status"> & Partial<Pick<Response, "text" | "json">>,
): Promise<unknown> {
  if (typeof response.text === "function") {
    const text = await response.text();
    if (!text.trim()) return null;
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return null;
    }
  }
  // Some test doubles and non-browser fetch implementations expose only json().
  return typeof response.json === "function" ? response.json() : null;
}

function isUploadBatchResponse(value: unknown): value is UploadBatchResponse {
  if (!value || typeof value !== "object") return false;
  const response = value as Partial<UploadBatchResponse>;
  return Array.isArray(response.documents) && Array.isArray(response.errors);
}

function apiErrorMessage(value: unknown): string | null {
  if (!value || typeof value !== "object" || !("error" in value)) return null;
  const error = (value as ApiError).error;
  return typeof error?.message === "string" ? error.message : null;
}

function apiErrorDocumentId(value: unknown): string | null {
  if (!value || typeof value !== "object" || !("error" in value)) return null;
  const error = (value as { error?: { document_id?: unknown } }).error;
  return typeof error?.document_id === "string" ? error.document_id : null;
}

function uploadFailureMessage(fileName: string, status: number, payload: unknown): string {
  const apiMessage = apiErrorMessage(payload);
  if (apiMessage) return apiMessage;
  if (status === 413) {
    return `${fileName}: the PDF is too large for the server or app gateway.`;
  }
  if (status >= 500) {
    return `${fileName}: the upload service is unavailable (HTTP ${status}).`;
  }
  return `${fileName}: the upload was rejected (HTTP ${status}).`;
}
