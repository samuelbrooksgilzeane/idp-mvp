import { Download, FileSpreadsheet, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { InvoiceSummary } from "../types";

type ResultsPageProps = { caseIds: string[] };

export function ResultsPage({ caseIds }: ResultsPageProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const caseId = searchParams.get("case_id") || null;
  const [rows, setRows] = useState<InvoiceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const query = caseId ? `?case_id=${encodeURIComponent(caseId)}` : "";
    setLoading(true);
    setError(null);
    fetch(`/api/results/invoices${query}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Invoice results request failed");
        return response.json() as Promise<InvoiceSummary[]>;
      })
      .then(setRows)
      .catch((caught: unknown) => {
        if (!(caught instanceof DOMException && caught.name === "AbortError")) {
          setRows([]);
          setError("Invoice results could not be loaded.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [caseId, reloadToken]);

  const metrics = useMemo(() => ({
    invoices: rows.length,
    lines: rows.reduce((total, row) => total + row.line_item_count, 0),
    reconciled: rows.filter((row) => {
      const delta = numberValue(row.reconciliation_delta);
      return delta !== null && Math.abs(delta) <= 0.01;
    }).length,
    review: rows.filter((row) => row.document_status === "REVIEW_REQUIRED").length,
  }), [rows]);
  const exportQuery = caseId ? `?case_id=${encodeURIComponent(caseId)}` : "";

  return (
    <section className="results-workspace" aria-labelledby="results-title">
      <div className="results-toolbar">
        <div className="case-filter">
          <label htmlFor="results-case-filter">Case</label>
          <select
            id="results-case-filter"
            value={caseId ?? ""}
            onChange={(event) => {
              const value = event.target.value;
              setSearchParams(value ? { case_id: value } : {}, { replace: true });
            }}
          >
            <option value="">All cases</option>
            {caseIds.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <span>{caseId ? `Showing ${caseId}` : "Showing every registered case"}</span>
        </div>
        <div className="results-actions">
          <button
            className="icon-button"
            type="button"
            onClick={() => setReloadToken((value) => value + 1)}
            aria-label="Refresh invoice results"
            title="Refresh invoice results"
          >
            <RefreshCw size={16} aria-hidden="true" />
          </button>
          <a className="export-action" href={`/api/exports/invoices.xlsx${exportQuery}`} download>
            <Download size={16} aria-hidden="true" />Export XLSX
          </a>
        </div>
      </div>

      <dl className="result-metrics" aria-label="Invoice result totals">
        <div><dt>Invoices</dt><dd>{metrics.invoices}</dd></div>
        <div><dt>Line items</dt><dd>{metrics.lines}</dd></div>
        <div><dt>Reconciled</dt><dd>{metrics.reconciled}</dd></div>
        <div><dt>Review required</dt><dd>{metrics.review}</dd></div>
      </dl>

      <div className="results-heading">
        <div>
          <p className="eyebrow">Latest successful extraction</p>
          <h2 id="results-title">Invoice summary</h2>
        </div>
        <span>{rows.length} {rows.length === 1 ? "invoice" : "invoices"}</span>
      </div>

      {loading ? <div className="results-state">Loading invoice results...</div> : null}
      {!loading && error ? (
        <div className="results-state results-error"><strong>Results unavailable</strong><span>{error}</span></div>
      ) : null}
      {!loading && !error && !rows.length ? (
        <div className="results-state results-empty">
          <FileSpreadsheet size={24} aria-hidden="true" />
          <strong>No extracted invoices</strong>
          <span>{caseId ? "This case has no successful invoice extractions yet." : "Successful invoice extractions will appear here."}</span>
        </div>
      ) : null}
      {!loading && !error && rows.length ? (
        <div className="table-scroll results-table-scroll">
          <table className="results-table">
            <thead><tr>
              <th>Invoice</th><th>Case</th><th>Seller</th><th>Invoice date</th>
              <th>Lines</th><th>Line sum</th><th>Stated total</th><th>Delta</th><th>Validation</th>
            </tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={`${row.document_id}-${row.invoice_index}`}>
                  <td>
                    <Link className="document-link" to={`/documents/${row.document_id}`}>
                      {row.invoice_number ?? "Invoice number unavailable"}
                    </Link>
                    <span className="result-file-name">
                      {row.file_name}
                      {row.invoice_index > 0 ? ` · invoice ${row.invoice_index + 1}` : ""}
                    </span>
                  </td>
                  <td>{row.case_id ?? "-"}</td>
                  <td>{row.seller_name ?? "-"}</td>
                  <td>{formatDate(row.invoice_date)}</td>
                  <td>{row.line_item_count}</td>
                  <td>{formatMoney(row.line_items_sum, row.currency)}</td>
                  <td>{formatMoney(row.total_amount, row.currency)}</td>
                  <td className={deltaClass(row.reconciliation_delta)}>
                    {formatMoney(row.reconciliation_delta, row.currency)}
                  </td>
                  <td>{validationBadge(row.document_status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function numberValue(value: string | number | null): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatMoney(value: string | number | null, currency: string | null): string {
  const parsed = numberValue(value);
  if (parsed === null) return "-";
  try {
    return new Intl.NumberFormat(undefined, {
      style: currency ? "currency" : "decimal",
      currency: currency ?? undefined,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(parsed);
  } catch {
    return `${currency ? `${currency} ` : ""}${parsed.toFixed(2)}`;
  }
}

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" })
    .format(new Date(`${value}T00:00:00Z`));
}

function deltaClass(value: string | number | null): string | undefined {
  const parsed = numberValue(value);
  if (parsed === null) return undefined;
  return Math.abs(parsed) <= 0.01 ? "delta-match" : "delta-exception";
}

function validationBadge(status: string | null) {
  if (!status) return <span className="validation-badge validation-pending">Not run</span>;
  return (
    <span className={`validation-badge validation-${status.toLowerCase()}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}
