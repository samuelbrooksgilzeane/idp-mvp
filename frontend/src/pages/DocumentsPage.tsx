import { useNavigate } from "react-router-dom";

import { BatchActions } from "../components/BatchActions";
import { DocumentList } from "../components/DocumentList";
import { UploadPanel, type UploadInput } from "../components/UploadPanel";
import type { ApiError, DocumentRecord, DocumentStatus, Notice } from "../types";
import { useMemo, useState } from "react";

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
};

export function DocumentsPage({
  documents,
  loading,
  caseIds,
  selectedCaseId,
  onCaseChanged,
  onDocumentsChanged,
}: DocumentsPageProps) {
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [status, setStatus] = useState<DocumentStatus | "">("");
  const [search, setSearch] = useState("");

  // Offer only the statuses the case actually contains, plus whichever one is selected, so
  // the control never lists a state the registry cannot show or silently drops its own value.
  const statuses = useMemo(() => {
    const present = new Set(documents.map((item) => item.status));
    if (status) present.add(status);
    return [...present].sort();
  }, [documents, status]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return documents.filter(
      (item) =>
        (!status || item.status === status) &&
        (!term || item.file_name.toLowerCase().includes(term)),
    );
  }, [documents, status, search]);
  const visibleIds = useMemo(
    () => new Set(visible.map((item) => item.document_id)),
    [visible],
  );
  // A batch only ever acts on documents the filters leave visible, so a hidden document
  // can never be swept into a run the user cannot see.
  const selection = useMemo(
    () => [...selectedIds].filter((id) => visibleIds.has(id)),
    [selectedIds, visibleIds],
  );

  function toggleSelect(documentId: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds((current) => {
      const allSelected =
        visible.length > 0 && visible.every((item) => current.has(item.document_id));
      const next = new Set(current);
      for (const item of visible) {
        if (allSelected) next.delete(item.document_id);
        else next.add(item.document_id);
      }
      return next;
    });
  }

  function changeCase(caseId: string | null) {
    setSelectedIds(new Set());
    onCaseChanged(caseId);
  }

  async function handleUpload(input: UploadInput) {
    setUploading(true);
    setNotice(null);
    const body = new FormData();
    input.files.forEach((file) => body.append("files", file));
    if (input.caseId.trim()) body.append("case_id", input.caseId.trim());
    body.append("template_id", "invoice_v1");
    body.append("use_case", "invoice");
    try {
      const response = await fetch("/api/documents", { method: "POST", body });
      const payload = (await response.json()) as UploadBatchResponse | ApiError;
      if (!response.ok) {
        throw new Error("error" in payload ? payload.error.message : "Upload failed.");
      }
      const result = payload as UploadBatchResponse;
      const accepted = result.documents.length;
      setNotice(
        result.errors.length
          ? { kind: "error", message: result.errors.map((error) => error.message).join(" ") }
          : {
              kind: "success",
              message: `${accepted} ${accepted === 1 ? "document" : "documents"} registered.`,
            },
      );
      await onDocumentsChanged();
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
              onChange={(event) => setStatus(event.target.value as DocumentStatus | "")}
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
              value={search}
              placeholder="File name"
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <p className="registry-filters-hint">
            The case scopes the registry; status and search narrow what is listed. A batch
            only ever runs the documents left visible here.
          </p>
        </div>
        <BatchActions
          selectedIds={selection}
          useCase="invoice"
          onClear={() => setSelectedIds(new Set())}
          onDocumentsChanged={onDocumentsChanged}
        />
        <DocumentList
          documents={visible}
          totalCount={documents.length}
          filtered={Boolean(status || search.trim())}
          loading={loading}
          selectedDocumentId={null}
          onRefresh={() => void onDocumentsChanged()}
          onSelect={(document) => navigate(`/documents/${document.document_id}`)}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onToggleAll={toggleAll}
        />
      </div>
    </section>
  );
}
