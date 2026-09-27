import { useExportRequest } from "../hooks/useExportRequest";
import { ExportStatus } from "../components/ExportStatus";
import { Download, FileSpreadsheet, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { prefetchExtractionReview } from "../lib/extractionReviewPrefetch";
import { useCursorPage, invalidateListPages } from "../hooks/useCursorPage";
import { useListSelection } from "../hooks/useListSelection";
import { cacheScope } from "../lib/requestCache";
import { rememberCursor, previousCursor, saveScroll, restoreScroll } from "../lib/listNavigation";
import type { ExtractionRunSummary } from "../types";

const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function ResultsPage() {
  const [bulkExport, setBulkExport] = useState(false);
  const exportTask = useExportRequest(cacheScope());
  useEffect(() => {
    void fetch("/api/upload-batches/limits").then(r => r.json())
      .then(value => setBulkExport(value.bulk_export === true)).catch(() => undefined);
  }, []);
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const caseId = params.get("case") || "";
  const schemaKey = params.get("schema") || "";
  const status = params.get("status") || "";
  const search = params.get("search") || "";
  const latestOnly = params.get("latest") !== "false";
  const cursor = params.get("cursor") || "";
  const [searchInput, setSearchInput] = useState(search);
  const [reloadToken, setReloadToken] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useListSelection(`${cacheScope()}:results`);
  function filter(name: string, value: string) {
    setParams(current => { const next = new URLSearchParams(current);
      if (value) next.set(name, value); else next.delete(name);
      next.delete("cursor"); return next; });
  }
  function changeCursor(value: string) {
    setParams(current => { const next = new URLSearchParams(current);
      if (value) next.set("cursor", value); else next.delete("cursor"); return next; });
  }
  useEffect(() => { setSearchInput(search); }, [search]);
  useEffect(() => {
    if (searchInput === search) return;
    const timer = setTimeout(() => setParams(current => { const next = new URLSearchParams(current);
      if (searchInput) next.set("search", searchInput); else next.delete("search");
      next.delete("cursor"); return next; }), 250);
    return () => clearTimeout(timer);
  }, [searchInput, search, setParams]);
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

  const query = new URLSearchParams(requestQuery);
  if (cursor) query.set("cursor", cursor);
  const { items: rows, next_cursor: nextCursor, loading, error } = useCursorPage<ExtractionRunSummary>(
    `/api/extractions?${query}`, true, reloadToken);
  const cursorKey = `/api/extractions?${requestQuery}`;
  rememberCursor(cursorKey, nextCursor, cursor);
  const previous = previousCursor(cursorKey, cursor);
  const listUrl = location.pathname + location.search;
  useEffect(() => { if (!loading) restoreScroll(listUrl); }, [loading, listUrl]);
  const listContext = { url: listUrl, endpoint: "/api/extractions", query: query.toString(),
    ids: rows.map(row => row.extraction_run_id), next: nextCursor, previous, kind: "results" as const };
  const [schemas, setSchemas] = useState<[string, string][]>([]);
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const statuses = ["RUNNING", "EXTRACTED", "FAILED"];
  useEffect(() => {
    let active = true;
    void fetch("/api/schemas?status=ALL").then(r => r.json()).then(data => {
      if (active && Array.isArray(data)) setSchemas([...new Map(data.map((row: { schema_id: string; display_name: string }) => [row.schema_id, row.display_name])).entries()]);
    }).catch(() => undefined);
    void fetch("/api/documents/cases").then(r => r.json()).then(data => {
      if (active && Array.isArray(data)) setCaseIds(data);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  const visible = rows;
  const pageRows = rows;
  const selection = [...selectedIds];

  function toggleSelect(runId: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId);
      else if (next.size < 1000) next.add(runId);
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds((current) => {
      const allSelected = pageRows.length > 0 && pageRows.every((row) => current.has(row.extraction_run_id));
      const next = new Set(current);
      for (const row of pageRows) {
        if (allSelected) next.delete(row.extraction_run_id);
        else if (next.size < 1000) next.add(row.extraction_run_id);
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
    if (bulkExport) { await exportTask.start(runIds, format); return; }
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

  return (
    <section className="results-workspace" aria-labelledby="results-title">
      <div className="registry-filters results-filters">
        <div className="registry-filter">
          <label htmlFor="results-case-filter">Case</label>
          <select id="results-case-filter" value={caseId} onChange={(event) => filter("case", event.target.value)}>
            <option value="">All cases</option>
            {caseIds.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div className="registry-filter">
          <label htmlFor="results-schema-filter">Schema</label>
          <select id="results-schema-filter" value={schemaKey} onChange={(event) => filter("schema", event.target.value)}>
            <option value="">All schemas</option>
            {schemas.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
        <div className="registry-filter">
          <label htmlFor="results-status-filter">Status</label>
          <select id="results-status-filter" value={status} onChange={(event) => filter("status", event.target.value)}>
            <option value="">All statuses</option>
            {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div className="registry-filter">
          <label htmlFor="results-name-filter">Search</label>
          <input
            id="results-name-filter"
            type="search"
            value={searchInput}
            placeholder="File name"
            onChange={(event) => setSearchInput(event.target.value)}
          />
        </div>
        <label className="results-latest-toggle">
          <input
            type="checkbox"
            checked={latestOnly}
            onChange={(event) => filter("latest", String(event.target.checked))}
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
            onClick={() => { invalidateListPages(); setReloadToken((value) => value + 1); }}
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

      <ExportStatus task={exportTask} />
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

      {cursor ? <button type="button" onClick={() => changeCursor("")}>Reset list</button> : null}
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
                      state={{ list: listContext }}
                      onClick={() => saveScroll(listUrl)}
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
        <nav className="pagination" aria-label="runs pagination">
          <span>{rows.length} runs on this page · {selection.length} selected</span>
          <button disabled={loading || previous === undefined} onClick={() => changeCursor(previous || "")}>Previous page</button>
          <button disabled={loading || !nextCursor} onClick={() => changeCursor(nextCursor || "")}>Next page</button>
        </nav>
      ) : null}
    </section>
  );
}
