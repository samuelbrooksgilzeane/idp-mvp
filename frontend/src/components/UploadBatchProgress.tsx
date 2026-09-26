import { useState } from "react";
import { Link } from "react-router-dom";
import type { UploadBatchController, UploadItem } from "../hooks/useUploadBatch";

const labels: Record<UploadItem["state"], string> = {
  QUEUED: "Waiting to upload", UPLOADING: "Uploading", REGISTERED: "Uploaded",
  ALREADY_REGISTERED: "Already uploaded", FAILED: "Upload failed",
};
export function UploadBatchProgress({ upload }: { upload: UploadBatchController }) {
  const [page, setPage] = useState(0);
  if (!upload.batch) return upload.error ? <p role="alert">{upload.error}</p> : null;
  const { items } = upload.batch;
  const registered = items.filter((item) => ["REGISTERED", "ALREADY_REGISTERED"].includes(item.state)).length;
  const queued = items.filter((item) => item.state === "QUEUED").length;
  const transferring = items.filter((item) => item.state === "UPLOADING").length;
  const failed = items.filter((item) => item.state === "FAILED").length;
  const current = Math.min(page, Math.max(0, Math.ceil(items.length / 25) - 1));
  return <section className="upload-batch-progress" aria-label="Upload progress">
    <h3>Upload progress</h3>
    <p role="status">{registered} of {items.length} uploaded · {queued} waiting · {transferring} uploading · {failed} need attention</p>
    {upload.batch.case_id ? <p>Case: {upload.batch.case_id}</p> : null}
    <p>Uploads continue while you navigate in this app. Closing or refreshing the tab stops unfinished transfers; reselect the original files to continue. Completed files are skipped.</p>
    {upload.automaticPreparation ? <p>Uploaded documents prepare in the background. Look for “Ready to extract” in the document list.</p> : null}
    {upload.error ? <p role="alert">{upload.error}</p> : null}
    {upload.paused ? <p>Paused. Active transfers finish before the queue stops.</p> : null}
    <button type="button" disabled={!upload.busy || upload.paused} onClick={upload.pause}>Pause uploads</button>
    <button type="button" disabled={upload.busy || registered === items.length} onClick={() => void upload.retry()}>Retry unfinished files</button>
    <button type="button" disabled={upload.busy} onClick={() => {
      if (registered === items.length || window.confirm("Start a new upload batch? This batch's saved outcomes remain on the server.")) upload.clear();
    }}>New upload batch</button>
    <ul>{items.slice(current * 25, current * 25 + 25).map((item) => <li key={item.client_file_id}>
      <strong>{item.name}</strong> — {labels[item.state]}
      {item.document_id ? <> · <Link to={`/documents/${item.document_id}`}>View document</Link></> : null}
      {item.error_message ? <p>{item.error_message}</p> : null}
      {item.error_code ? <details><summary>Diagnostics</summary>{item.error_code}</details> : null}
    </li>)}</ul>
    {items.length > 25 ? <nav aria-label="Upload file pages">
      <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous files</button>
      <span>Page {current + 1} of {Math.ceil(items.length / 25)}</span>
      <button type="button" disabled={(current + 1) * 25 >= items.length} onClick={() => setPage(current + 1)}>Next files</button>
    </nav> : null}
  </section>;
}
