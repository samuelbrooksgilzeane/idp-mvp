import { useEffect, useMemo, useState } from "react";

export type SchemaSummary = {
  schema_id: string;
  schema_version: number;
  display_name: string;
  use_case: string;
  schema_hash: string;
  status: "PRODUCTION";
};

export type SchemaField = {
  field_path: string;
  label: string;
  field_type: string;
  description: string;
  required: boolean;
  citation_required: boolean;
  confidence_threshold: number;
  risk_tier: "low" | "medium" | "high";
};

export type SchemaRule = {
  rule_id: string;
  rule_type: string;
  description: string;
  field_paths: string[];
  tolerance: number | null;
};

export type SchemaDetail = SchemaSummary & {
  instructions: string;
  fields: SchemaField[];
  document_rules: SchemaRule[];
};

type SchemaViewerProps = {
  useCase: string;
};

export function SchemaViewer({ useCase }: SchemaViewerProps) {
  const [schemas, setSchemas] = useState<SchemaSummary[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [detail, setDetail] = useState<SchemaDetail | null>(null);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">(
    "idle",
  );

  useEffect(() => {
    const controller = new AbortController();
    setSchemas([]);
    setSelectedKey("");
    setDetail(null);
    setListState("loading");
    fetch(`/api/schemas?status=PRODUCTION&use_case=${encodeURIComponent(useCase)}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Schema list request failed");
        return response.json() as Promise<unknown>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        const available = Array.isArray(payload) ? payload.filter(isSchemaSummary) : [];
        setSchemas(available);
        setSelectedKey(available.length > 0 ? schemaKey(available[0]) : "");
        setListState("ready");
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setListState("error");
        }
      });
    return () => controller.abort();
  }, [useCase]);

  const selected = useMemo(
    () => schemas.find((schema) => schemaKey(schema) === selectedKey) ?? null,
    [schemas, selectedKey],
  );

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      setDetailState("idle");
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setDetailState("loading");
    fetch(`/api/schemas/${encodeURIComponent(selected.schema_id)}/versions/${selected.schema_version}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Schema detail request failed");
        return response.json() as Promise<unknown>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        if (!isSchemaDetail(payload)) throw new Error("Schema detail response was invalid");
        setDetail(payload);
        setDetailState("ready");
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setDetailState("error");
        }
      });
    return () => controller.abort();
  }, [selected]);

  return (
    <section className="schema-viewer" aria-labelledby="schema-viewer-title">
      <div className="schema-heading">
        <div>
          <p className="eyebrow">Extraction contract</p>
          <h3 id="schema-viewer-title">Approved field specification</h3>
        </div>
        <span className="schema-readonly">Read only</span>
      </div>

      {listState === "loading" ? <SchemaSkeleton /> : null}
      {listState === "error" ? (
        <div className="schema-message schema-message-error" role="alert">
          <strong>Schema registry unavailable</strong>
          <span>The approved extraction contract could not be loaded.</span>
        </div>
      ) : null}
      {listState === "ready" && schemas.length === 0 ? (
        <div className="schema-message">
          <strong>No production schema</strong>
          <span>No approved extraction contract is registered for this use case.</span>
        </div>
      ) : null}

      {listState === "ready" && schemas.length > 0 ? (
        <>
          <div className="schema-selector-row">
            <label htmlFor="schema-selector">
              Approved schema
              <span>Only deployed production versions are selectable.</span>
            </label>
            <select
              id="schema-selector"
              value={selectedKey}
              onChange={(event) => setSelectedKey(event.target.value)}
            >
              {schemas.map((schema) => (
                <option key={schemaKey(schema)} value={schemaKey(schema)}>
                  {schema.display_name} · v{schema.schema_version}
                </option>
              ))}
            </select>
          </div>
          {detailState === "loading" ? <SchemaSkeleton compact /> : null}
          {detailState === "error" ? (
            <div className="schema-message schema-message-error" role="alert">
              <strong>Schema detail unavailable</strong>
              <span>The selected version could not be inspected.</span>
            </div>
          ) : null}
          {detailState === "ready" && detail ? <SchemaContract detail={detail} /> : null}
        </>
      ) : null}
    </section>
  );
}

function SchemaContract({ detail }: { detail: SchemaDetail }) {
  return (
    <div className="schema-contract">
      <div className="schema-provenance" aria-label="Schema provenance">
        <span className="production-badge">{detail.status}</span>
        <span>Version {detail.schema_version}</span>
        <span className="schema-hash" title={detail.schema_hash}>
          SHA {detail.schema_hash.slice(0, 12)}
        </span>
        <span>{detail.fields.length} fields</span>
      </div>
      <p className="schema-instructions">{detail.instructions}</p>

      <div className="schema-table-scroll">
        <table className="schema-table">
          <thead>
            <tr>
              <th>Field</th>
              <th>Type</th>
              <th>Required</th>
              <th>Citation</th>
              <th>Threshold</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {detail.fields.map((field) => (
              <tr key={field.field_path}>
                <td>
                  <strong>{field.label}</strong>
                  <span>{field.field_path}</span>
                  <small>{field.description}</small>
                </td>
                <td><code>{field.field_type}</code></td>
                <td>{field.required ? "Required" : "Optional"}</td>
                <td>{field.citation_required ? "Required" : "Not required"}</td>
                <td>{Math.round(field.confidence_threshold * 100)}%</td>
                <td><span className={`risk-label risk-${field.risk_tier}`}>{field.risk_tier}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="schema-footer">
        <p>
          Confidence thresholds are initial policy settings and must be calibrated against
          benchmark results.
        </p>
        <span>{detail.document_rules.length} deterministic rules registered</span>
      </div>
    </div>
  );
}

function SchemaSkeleton({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`schema-skeleton${compact ? " schema-skeleton-compact" : ""}`} aria-label="Loading extraction schema">
      <span />
      <span />
      <span />
    </div>
  );
}

function schemaKey(schema: SchemaSummary): string {
  return `${schema.schema_id}:${schema.schema_version}`;
}

function isSchemaSummary(value: unknown): value is SchemaSummary {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<SchemaSummary>;
  return (
    typeof candidate.schema_id === "string" &&
    typeof candidate.schema_version === "number" &&
    typeof candidate.display_name === "string" &&
    candidate.status === "PRODUCTION"
  );
}

function isSchemaDetail(value: unknown): value is SchemaDetail {
  return isSchemaSummary(value) &&
    typeof (value as Partial<SchemaDetail>).instructions === "string" &&
    Array.isArray((value as Partial<SchemaDetail>).fields) &&
    Array.isArray((value as Partial<SchemaDetail>).document_rules);
}
