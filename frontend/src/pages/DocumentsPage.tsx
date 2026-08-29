import { useNavigate } from "react-router-dom";

import { BatchActions } from "../components/BatchActions";
import { DocumentList } from "../components/DocumentList";
import { UploadPanel, type UploadInput } from "../components/UploadPanel";
import type { ApiError, DocumentRecord, Notice } from "../types";
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
  onDocumentsChanged: () => Promise<void> | void;
};

export function DocumentsPage({ documents, loading, onDocumentsChanged }: DocumentsPageProps) {
  const navigate = useNavigate();
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const knownIds = useMemo(
    () => new Set(documents.map((item) => item.document_id)),
    [documents],
  );
  // A selection only ever refers to documents still in the registry.
  const selection = useMemo(
    () => [...selectedIds].filter((id) => knownIds.has(id)),
    [selectedIds, knownIds],
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
    setSelectedIds((current) =>
      current.size === documents.length ? new Set() : new Set(knownIds),
    );
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
        <BatchActions
          selectedIds={selection}
          useCase="invoice"
          onClear={() => setSelectedIds(new Set())}
          onDocumentsChanged={onDocumentsChanged}
        />
        <DocumentList
          documents={documents}
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
