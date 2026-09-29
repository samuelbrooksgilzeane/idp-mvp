import { FileUp, LoaderCircle, Upload } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

export type UploadInput = { files: File[]; caseId: string };

type UploadPanelProps = {
  uploading: boolean;
  resuming?: boolean;
  maxFiles?: number;
  maxFileBytes?: number | null;
  notice: { kind: "success" | "error"; message: string } | null;
  onUpload: (input: UploadInput) => Promise<void>;
};

export function UploadPanel({ uploading, notice, onUpload, resuming = false, maxFiles = 1000, maxFileBytes = null }: UploadPanelProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [caseId, setCaseId] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!files.length) return;
    await onUpload({ files, caseId });
    setFiles([]);
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <aside className="upload-panel" aria-labelledby="upload-title">
      <div className="section-heading">
        <div className="section-icon"><FileUp size={18} aria-hidden="true" /></div>
        <div>
          <h2 id="upload-title">Upload PDFs</h2>
          <p className="upload-hint">
            Up to {maxFiles.toLocaleString()} PDFs per batch, three transferring at a time. Case ID is optional.
          </p>
        </div>
      </div>
      <form className="upload-form" onSubmit={(event) => void submit(event)}>
        <div className="upload-case">
        <label className="field-label" htmlFor="case-id">Case ID <span>Optional</span></label>
        <input
          id="case-id"
          disabled={resuming || uploading}
          maxLength={200}
          onChange={(event) => setCaseId(event.target.value)}
          placeholder="e.g. CASE-1042"
          value={caseId}
        />
        </div>

        <label className="file-picker" htmlFor="pdf-files">
          <Upload size={22} aria-hidden="true" />
          <strong>{files.length ? `${files.length} selected` : "Choose PDF files"}</strong>
          <span>
            {files.length
              ? files.slice(0, 3).map((file) => file.name).join(", ") + (files.length > 3 ? ` and ${files.length - 3} more` : "")
              : maxFileBytes ? `PDF only, up to ${(maxFileBytes / 1024 / 1024).toLocaleString()} MiB each` : "PDF only; the server enforces its configured size limit"}
          </span>
        </label>
        <input
          ref={inputRef}
          className="visually-hidden"
          id="pdf-files"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />

        <button className="primary-action" disabled={!files.length || files.length > maxFiles || uploading} type="submit">
          {uploading
            ? <LoaderCircle className="spin" size={17} aria-hidden="true" />
            : <Upload size={17} aria-hidden="true" />}
          {uploading ? "Uploading" : resuming ? "Reselect and continue" : "Upload documents"}
        </button>
        {files.length > maxFiles ? <p role="alert">Select at most {maxFiles.toLocaleString()} PDFs.</p> : null}
        {notice
          ? <p className={`notice notice-${notice.kind}`} role="status">{notice.message}</p>
          : null}
      </form>
    </aside>
  );
}
