import type { useExportRequest } from "../hooks/useExportRequest";

export function ExportStatus({ task }: { task: ReturnType<typeof useExportRequest> }) {
  if (!task.status && !task.pending && !task.error) return null;
  return <section className="notice" aria-label="Export progress" aria-live="polite">
    {task.status ? <p>Export: {task.status.state.toLowerCase()} · {task.status.runs_processed} of {task.status.selected_count} results processed</p> : <p>Confirming export submission…</p>}
    {task.error || task.status?.error ? <p role="alert">{task.error || task.status?.error}</p> : null}
    {task.status?.download_url ? <a href={task.status.download_url} download={task.status.filename || true}>Download export</a> : null}
    {task.confirmation ? <button type="button" onClick={() => void task.confirm()}>Include all selected historical runs</button> : null}
    {task.pending && !task.confirmation ? <button type="button" disabled={task.submitting} onClick={() => void task.retry()}>Retry submission</button> : null}
    <button type="button" onClick={task.dismiss}>Dismiss</button>
  </section>;
}
