import { Link } from "react-router-dom";
import type { ExtractionBatchController } from "../hooks/useExtractionBatch";

export function ExtractionBatchProgress({ batch }: { batch: ExtractionBatchController }) {
  const status = batch.status;
  if (!batch.batchId && !batch.pending && !batch.error) return null;
  const succeeded = status?.counts.SUCCEEDED ?? 0;
  const failed = status?.counts.FAILED ?? 0;
  return <section className="batch-actions extraction-batch-progress" aria-label="Extraction batch progress">
    <div className="batch-summary">
      <strong>{batch.pending ? "Submission not confirmed" : status?.terminal ? "Extraction complete" : "Extraction in progress"}</strong>
      {status ? <span>{succeeded} succeeded · {failed} failed · {status.total - succeeded - failed} remaining</span> : <span>Restoring saved progress…</span>}
      {status?.state === "VALIDATING" ? <span>Checking documents: {status.validated} of {status.total}</span> : null}
    </div>
    <p>{batch.pending ? "Request details are saved in this browser. Retry submission to confirm acceptance." : "Your request is saved. You can leave this page and return to check progress."}</p>
    {batch.error ? <p role="alert">{batch.error}</p> : null}
    {batch.pending ? <button type="button" disabled={batch.busy} onClick={() => void batch.retrySubmission()}>Retry submission</button> : null}
    {status?.terminal && failed > 0 && !batch.pending ? <button type="button" disabled={batch.busy} onClick={() => void batch.retry()}>Retry failed documents</button> : null}
    {(status?.terminal || batch.error) && !batch.busy ? <button type="button" onClick={batch.clear} title="This does not cancel submitted work">Dismiss progress</button> : null}
    {batch.items.length ? <ul>{batch.items.map((item) => <li key={item.ordinal}>
      <Link to={`/documents/${item.document_id}`}>{item.document_name ?? `Document ${item.ordinal + 1}`}</Link>
      {" · "}{({ QUEUED: "Waiting", CLAIMED: "Starting", RUNNING: "Processing", SUCCEEDED: "Complete", FAILED: "Failed" } as Record<string, string>)[item.state] ?? item.state}
      {item.error_message ? <span> — {item.error_message}</span> : null}
    </li>)}</ul> : null}
    {batch.hasPrevious || batch.hasNext ? <nav aria-label="Extraction members">
      <button type="button" disabled={!batch.hasPrevious} onClick={batch.previous}>Previous</button>
      <button type="button" disabled={!batch.hasNext} onClick={batch.next}>Next</button>
    </nav> : null}
  </section>;
}
