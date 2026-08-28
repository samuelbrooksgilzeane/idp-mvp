import { FileUp, LoaderCircle, Upload } from "lucide-react";
import { type FormEvent, useRef, useState } from "react";

export type UploadInput = { files: File[]; caseId: string };

type UploadPanelProps = {
  uploading: boolean;
  notice: { kind: "success" | "error"; message: string } | null;
  onUpload: (input: UploadInput) => Promise<void>;
};

export function UploadPanel({ uploading, notice, onUpload }: UploadPanelProps) {
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
        <div><p className="eyebrow">New intake</p><h2 id="upload-title">Upload PDFs</h2></div>
      </div>
      <form onSubmit={(event) => void submit(event)}>
        <label className="field-label" htmlFor="case-id">Case ID <span>Optional</span></label>
        <input
          id="case-id"
          maxLength={200}
          onChange={(event) => setCaseId(event.target.value)}
          placeholder="e.g. CASE-1042"
          value={caseId}
        />

        <div className="fixed-metadata" aria-label="Upload profile">
          <div><span>Template</span><strong>invoice_v1</strong></div>
          <div><span>Use case</span><strong>Invoice</strong></div>
        </div>

        <label className="file-picker" htmlFor="pdf-files">
          <Upload size={22} aria-hidden="true" />
          <strong>{files.length ? `${files.length} selected` : "Choose PDF files"}</strong>
          <span>
            {files.length
              ? files.map((file) => file.name).join(", ")
              : "PDF only, up to 25 MB each"}
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

        <button className="primary-action" disabled={!files.length || uploading} type="submit">
          {uploading
            ? <LoaderCircle className="spin" size={17} aria-hidden="true" />
            : <Upload size={17} aria-hidden="true" />}
          {uploading ? "Registering" : "Register documents"}
        </button>
        {notice
          ? <p className={`notice notice-${notice.kind}`} role="status">{notice.message}</p>
          : null}
      </form>
    </aside>
  );
}
