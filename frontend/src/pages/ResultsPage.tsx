import { Download, FileSpreadsheet, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { prefetchExtractionReview } from "../lib/extractionReviewPrefetch";
import { Pagination } from "../components/Pagination";
import type { ExtractionRunPage, ExtractionRunSummary } from "../types";

const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function ResultsPage() {
  const [rows, setRows] = useState<ExtractionRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const [caseId, setCaseId] = useState("");
  const [schemaKey, setSchemaKey] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [latestOnly, setLatestOnly] = useState(true);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [duplicateWarning, setDuplicateWarning] = useState<{
    documentName: string;
    runIds: string[];
  } | null>(null);

  const requestQuery = useMemo(() => {
    const parameters = new URLSearchParams({ latest_only: String(latestOnly), limit: "50" });
    if (caseId) parameters.set("case_id", caseId);
    if (schemaKey) parameters.set("schema_id", schemaKey);
    if (status) parameters.set("status", status);
    if (search.trim()) parameters.set("search", search.trim());
    return parameters.toString();
  }, [caseId, latestOnly, schemaKey, search, status]);

  useEffect(() => {
    const controller = new AbortController();
    setPage(1);
    setLoading(true);
    setError(null);
    fetch(`/api/extractions?${requestQuery}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Extraction runs request failed");
        return response.json() as Promise<ExtractionRunPage>;
      })
      .then((page) => {
        setRows(page.items);
        setNextCursor(page.next_cursor);
      })
      .catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setRows([]);
          setNextCursor(null);
          setError("Extraction runs could not be loaded.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [reloadToken, requestQuery]);

  const schemas = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of rows) {
      // Pages arrive newest first; retain the display name from the newest observed version.
      if (!seen.has(row.schema_id)) seen.set(row.schema_id, row.schema_display_name);
    }
    return [...seen.entries()];
  }, [rows]);
  const statuses = useMemo(() => [...new Set(rows.map((row) => row.status))].sort(), [rows]);
  const caseIds = useMemo(
    () => [...new Set(rows.flatMap((row) => (row.case_id ? [row.case_id] : [])))].sort(),
    [rows],
  );

  const visible = rows;
  const pageCount = Math.max(1, Math.ceil(visible.length / 10));
  const pageRows = visible.slice((page - 1) * 10, page * 10);
  const visibleIds = useMemo(
    () => new Set(visible.map((row) => row.extraction_run_id)),
    [visible],
  );
  const selection = useMemo(
    () => [...selectedIds].filter((id) => visibleIds.has(id)),
    [selectedIds, visibleIds],
  );

  function toggleSelect(runId: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds((current) => {
      const allSelected = pageRows.length > 0 && pageRows.every((row) => current.has(row.extraction_run_id));
      const next = new Set(current);
      for (const row of pageRows) {
        if (allSelected) next.delete(row.extraction_run_id);
        else next.add(row.extraction_run_id);
      }
      return next;
    });
  }

  function findDuplicateRun(runIds: string[]): { documentName: string; runIds: string[] } | null {
    const byKey = new Map<string, ExtractionRunSummary[]>();
    for (const row of rows) {
      if (!runIds.includes(row.extraction_run_id)) continue;
      const key = `${row.document_id}:${row.schema_id}`;
      byKey.set(key, [...(byKey.get(key) ?? []), row]);
    }
    for (const group of byKey.values()) {
      if (group.length > 1) {
        return { documentName: group[0].document_name, runIds: group.map((row) => row.extraction_run_id) };
      }
    }
    return null;
  }

  async function runExport(runIds: string[], format: "xlsx" | "csv" = "xlsx") {
    setExporting(true);
    setExportError(null);
    try {
      const response = await fetch("/api/exports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_ids: runIds, format }),
      });
      if (!response.ok) throw new Error("The export could not be generated.");
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const match = /filename="([^"]+)"/.exec(disposition);
      const filename = match ? match[1] : `extraction-results.${format === "csv" ? "zip" : "xlsx"}`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause: unknown) {
      setExportError(cause instanceof Error ? cause.message : "The export could not be generated.");
    } finally {
      setExporting(false);
    }
  }

  function handleExportSelected() {
    const duplicate = findDuplicateRun(selection);
    if (duplicate) {
      setDuplicateWarning(duplicate);
      return;
    }
    void runExport(selection);
  }

  async function loadMore() {
    if (!nextCursor) return;
    setLoading(true);
    setError(null);
    try {
      const parameters = new URLSearchParams(requestQuery);
      parameters.set("cursor", nextCursor);
      const response = await fetch(`/api/extractions?${parameters.toString()}`);
      if (!response.ok) throw new Error("Extraction runs request failed");
      const page = (await response.json()) as ExtractionRunPage;
      setRows((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
      setPage((current) => current + 1);
    } catch {
      setError("More extraction runs could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="results-workspace" aria-labelledby="results-title">
      <div className="registry-filters results-filters">
        <div className="registry-filter">
          <label htmlFor="results-case-filter">Case</label>
          <select id="results-case-filter" value={caseId} onChange={(event) => setCaseId(event.target.value)}>
            <option value="">All cases</option>
            {caseIds.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div className="registry-filter">
          <label htmlFor="results-schema-filter">Schema</label>
          <select id="results-schema-filter" value={schemaKey} onChange={(event) => setSchemaKey(event.target.value)}>
            <option value="">All schemas</option>
            {schemas.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
        <div className="registry-filter">
          <label htmlFor="results-status-filter">Status</label>
          <select id="results-status-filter" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">All statuses</option>
            {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div className="registry-filter">
          <label htmlFor="results-name-filter">Search</label>
          <input
            id="results-name-filter"
            type="search"
            value={search}
            placeholder="File name"
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <label className="results-latest-toggle">
          <input
            type="checkbox"
            checked={latestOnly}
            onChange={(event) => setLatestOnly(event.target.checked)}
          />
          Latest runs only
        </label>
      </div>

      <div className="results-heading">
        <div>
          <p className="eyebrow">Extraction runs</p>
          <h2 id="results-title">Results</h2>
        </div>
        <div className="results-actions">
          <button
            className="icon-button"
            type="button"
            onClick={() => setReloadToken((value) => value + 1)}
            aria-label="Refresh extraction runs"
            title="Refresh extraction runs"
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
          <button
            className="export-action"
            type="button"
            disabled={!selection.length || exporting}
            onClick={handleExportSelected}
          >
            <Download size={16} aria-hidden="true" />
            {exporting ? "Exporting…" : `Export selected${selection.length ? ` (${selection.length})` : ""}`}
          </button>
        </div>
      </div>

      {exportError ? <p className="notice notice-error">{exportError}</p> : null}

      {duplicateWarning ? (
        <div className="duplicate-run-warning" role="alertdialog">
          <p>
            Two extraction runs for <strong>{duplicateWarning.documentName}</strong> are selected.
          </p>
          <div className="duplicate-run-actions">
            <button
              type="button"
              className="primary-action"
              onClick={() => {
                const latestId = rows
                  .filter((row) => duplicateWarning.runIds.includes(row.extraction_run_id))
                  .sort((a, b) => b.started_at.localeCompare(a.started_at))[0]?.extraction_run_id;
                setDuplicateWarning(null);
                void runExport(latestId ? [...selection.filter((id) => !duplicateWarning.runIds.includes(id)), latestId] : selection);
              }}
            >
              Use latest selected run
            </button>
            <button
              type="button"
              onClick={() => {
                setDuplicateWarning(null);
                void runExport(selection);
              }}
            >
              Include both
            </button>
          </div>
        </div>
      ) : null}

      {loading ? <div className="results-state">Loading extraction runs...</div> : null}
      {!loading && error ? (
        <div className="results-state results-error"><strong>Results unavailable</strong><span>{error}</span></div>
      ) : null}
      {!loading && !error && !visible.length ? (
        <div className="results-state results-empty">
          <FileSpreadsheet size={24} aria-hidden="true" />
          <strong>No extraction runs</strong>
          <span>Successful extractions will appear here once a document has been extracted.</span>
        </div>
      ) : null}
      {!loading && !error && visible.length ? (
        <div className="table-scroll results-table-scroll">
          <table className="results-table run-list-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="Select all visible runs"
                    checked={pageRows.length > 0 && pageRows.every((row) => selectedIds.has(row.extraction_run_id))}
                    onChange={toggleAll}
                  />
                </th>
                <th>Document</th>
                <th>Schema</th>
                <th>Extracted</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row) => (
                <tr key={row.extraction_run_id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select run for ${row.document_name}`}
                      checked={selectedIds.has(row.extraction_run_id)}
                      onChange={() => toggleSelect(row.extraction_run_id)}
                    />
                  </td>
                  <td>
                    <Link
                      className="document-link"
                      to={`/results/${row.extraction_run_id}`}
                      onPointerEnter={() => {
                        if (row.status === "EXTRACTED") prefetchExtractionReview(row.extraction_run_id);
                      }}
                      onFocus={() => {
                        if (row.status === "EXTRACTED") prefetchExtractionReview(row.extraction_run_id);
                      }}
                    >
                      {row.document_name}
                    </Link>
                    {row.is_latest ? <span className="latest-badge">Latest</span> : null}
                  </td>
                  <td>{row.schema_display_name} · v{row.schema_version}</td>
                  <td>{formatter.format(new Date(row.started_at))}</td>
                  <td>
                    <span className={`status-label status-${row.status.toLowerCase()}`}>{row.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {!error && visible.length ? (
        <Pagination
          page={Math.min(page, pageCount)}
          pageCount={pageCount}
          itemCount={visible.length}
          itemLabel="runs"
          onPageChange={setPage}
          hasMore={Boolean(nextCursor)}
          onLoadNext={() => void loadMore()}
          loading={loading}
        />
      ) : null}
    </section>
  );
}
