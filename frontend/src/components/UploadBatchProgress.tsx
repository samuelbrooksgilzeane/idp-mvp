import { useState } from "react";
import { Link } from "react-router-dom";
import type { UploadItem } from "../hooks/useUploadBatch";

// Browser uploads and folder imports share one progress view; a folder import runs on the server.
export type UploadProgressSource = {
  batch: { case_id: string | null; items: UploadItem[]; folder?: string; skipped_files?: number } | null;
  busy: boolean; running?: boolean; paused?: boolean; error: string | null;
  signInRequired?: boolean; retryingSoon?: boolean; automaticPreparation?: boolean;
  pause?: () => void; retry: () => Promise<void> | void; clear: () => void;
};

const browserLabels: Record<UploadItem["state"], string> = {
  QUEUED: "Waiting to upload", UPLOADING: "Uploading", REGISTERED: "Uploaded",
  ALREADY_REGISTERED: "Already uploaded", FAILED: "Upload failed",
};
const folderLabels: Record<UploadItem["state"], string> = {
  QUEUED: "Waiting to import", UPLOADING: "Importing", REGISTERED: "Imported",
  ALREADY_REGISTERED: "Already uploaded", FAILED: "Import failed",
};
export function UploadBatchProgress({ upload, source = "browser" }: { upload: UploadProgressSource; source?: "browser" | "folder" }) {
  const [page, setPage] = useState(0);
  if (!upload.batch) return upload.error ? <p role="alert">{upload.error}</p> : null;
  const folder = source === "folder";
  const labels = folder ? folderLabels : browserLabels;
  const { items } = upload.batch;
  const registered = items.filter((item) => ["REGISTERED", "ALREADY_REGISTERED"].includes(item.state)).length;
  const queued = items.filter((item) => item.state === "QUEUED").length;
  const transferring = items.filter((item) => item.state === "UPLOADING").length;
  const failed = items.filter((item) => item.state === "FAILED").length;
  const current = Math.min(page, Math.max(0, Math.ceil(items.length / 25) - 1));
  const done = items.length > 0 && registered === items.length;
  return <section className="upload-batch-progress" aria-label={folder ? "Folder import progress" : "Upload progress"}>
    <h3>{folder ? "Folder import" : "Upload progress"} <span className="upload-count">{registered} / {items.length}</span></h3>
    {upload.busy || upload.running ? <div className="activity-bar" aria-hidden="true" /> : null}
    {folder
      ? <p role="status">{registered} of {items.length} imported · {queued} waiting · {transferring} importing · {failed} need attention</p>
      : <p role="status">{registered} of {items.length} uploaded · {queued} waiting · {transferring} uploading · {failed} need attention</p>}
    {upload.batch.folder ? <p>Folder: {upload.batch.folder}{upload.batch.skipped_files ? ` · ${upload.batch.skipped_files} non-PDF files skipped` : ""}</p> : null}
    {upload.batch.case_id ? <p>Case: {upload.batch.case_id}</p> : null}
    {folder
      ? <p>The import runs on the server; you can close this tab. Imported files are removed from the folder; files that fail stay there with their reason below. The first files can take a minute while the import job starts.</p>
      : <p>Uploads continue while you navigate in this app. Closing or refreshing the tab stops unfinished transfers; reselect the original files to continue. Completed files are skipped.</p>}
    {upload.automaticPreparation ? <p>Uploaded documents prepare in the background. Look for “Ready to extract” in the document list.</p> : null}
    {upload.error ? <p role="alert">{upload.error}</p> : null}
    {upload.paused && !upload.signInRequired ? <p>Paused. Active transfers finish before the queue stops.</p> : null}
    {upload.retryingSoon ? <p role="status">Retrying failed files automatically in 30 seconds.</p> : null}
    {upload.pause ? <button type="button" disabled={!upload.busy || upload.paused} onClick={upload.pause}>Pause uploads</button> : null}
    <button type="button" disabled={upload.busy || done} onClick={() => void upload.retry()}>{upload.signInRequired ? "Resume" : "Retry unfinished files"}</button>
    <button type="button" disabled={upload.busy} onClick={() => {
      const question = folder ? "Start a new folder import? This import keeps running on the server." : "Start a new upload batch? This batch's saved outcomes remain on the server.";
      if (done || window.confirm(question)) upload.clear();
    }}>{folder ? "New folder import" : "New upload batch"}</button>
    <details className="upload-files"><summary>Show files</summary>
    <ul>{items.slice(current * 25, current * 25 + 25).map((item) => <li key={item.client_file_id}>
      <strong>{item.relative_path ?? item.name}</strong> — {labels[item.state]}
      {item.document_id ? <> · <Link to={`/documents/${item.document_id}`}>View document</Link></> : null}
      {item.error_message ? <p>{item.error_message}</p> : null}
      {item.error_code ? <details><summary>Diagnostics</summary>{item.error_code}</details> : null}
    </li>)}</ul>
    </details>
    {items.length > 25 ? <nav aria-label={folder ? "Import file pages" : "Upload file pages"}>
      <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous files</button>
      <span>Page {current + 1} of {Math.ceil(items.length / 25)}</span>
      <button type="button" disabled={(current + 1) * 25 >= items.length} onClick={() => setPage(current + 1)}>Next files</button>
    </nav> : null}
  </section>;
}
