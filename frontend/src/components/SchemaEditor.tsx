import { CheckCircle2, Copy, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

// -- Types mirroring the backend's schema_models.ExtractField / SchemaRecord shapes ----------

export type FieldType =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "enum"
  | "object"
  | "array";

export type ApiSchemaField = {
  type: FieldType;
  description: string;
  labels?: string[] | null;
  items?: ApiSchemaField | null;
  properties?: Record<string, ApiSchemaField> | null;
};

export type SchemaSummary = {
  schema_id: string;
  schema_version: number;
  display_name: string;
  description: string | null;
  use_case: string;
  schema_hash: string;
  status: "PRODUCTION" | "DRAFT" | "PUBLISHED" | "RETIRED";
  root_mode: "SINGLE_RECORD" | "REPEATED_RECORDS";
  is_editable: boolean;
  created_by: string;
  created_at: string;
  published_at: string | null;
};

export type SchemaDetail = SchemaSummary & {
  instructions: string;
  schema_tree: Record<string, ApiSchemaField>;
};

type ValidationReport = {
  valid: boolean;
  depth: number;
  max_depth: number;
  leaf_count: number;
  max_leaves: number;
  errors: string[];
};

// -- Editable in-browser tree: a stable, ordered, renameable mirror of ApiSchemaField --------

type EditableField = {
  id: string;
  name: string;
  type: FieldType;
  description: string;
  labels: string;
  items: EditableField | null;
  properties: EditableField[];
};

let nextId = 1;
function freshId(): string {
  nextId += 1;
  return `field-${nextId}`;
}

function newField(name: string): EditableField {
  return {
    id: freshId(),
    name,
    type: "string",
    description: "",
    labels: "",
    items: null,
    properties: [],
  };
}

function fromApi(node: ApiSchemaField, name: string): EditableField {
  return {
    id: freshId(),
    name,
    type: node.type,
    description: node.description,
    labels: (node.labels ?? []).join(", "),
    items: node.items ? fromApi(node.items, "") : node.type === "array" ? newField("") : null,
    properties: node.properties
      ? Object.entries(node.properties).map(([key, child]) => fromApi(child, key))
      : node.type === "object"
        ? []
        : [],
  };
}

function fromApiRoot(tree: Record<string, ApiSchemaField>): EditableField[] {
  return Object.entries(tree).map(([key, child]) => fromApi(child, key));
}

function toApi(field: EditableField): ApiSchemaField {
  const base: ApiSchemaField = { type: field.type, description: field.description || " " };
  if (field.type === "enum") {
    base.labels = field.labels
      .split(",")
      .map((label) => label.trim())
      .filter(Boolean);
  }
  if (field.type === "array") {
    base.items = toApi(field.items ?? newField(""));
  }
  if (field.type === "object") {
    base.properties = Object.fromEntries(
      field.properties.map((child) => [child.name, toApi(child)]),
    );
  }
  return base;
}

function toApiRoot(fields: EditableField[]): Record<string, ApiSchemaField> {
  return Object.fromEntries(fields.map((field) => [field.name, toApi(field)]));
}

function countLeaves(fields: EditableField[]): number {
  return fields.reduce((total, field) => {
    if (field.type === "object") return total + countLeaves(field.properties);
    if (field.type === "array") return total + countLeaves(field.items ? [field.items] : []);
    return total + 1;
  }, 0);
}

function maxDepth(fields: EditableField[], depth = 1): number {
  return fields.reduce((deepest, field) => {
    if (field.type === "object") return Math.max(deepest, maxDepth(field.properties, depth + 1));
    if (field.type === "array") {
      return Math.max(deepest, maxDepth(field.items ? [field.items] : [], depth + 1));
    }
    return Math.max(deepest, depth);
  }, depth);
}

const MAX_DEPTH = 12;
const MAX_LEAVES = 256;

const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  string: "Text",
  integer: "Whole number",
  number: "Decimal",
  boolean: "Yes/No",
  enum: "Choice list",
  object: "Group",
  array: "Repeating group",
};

// -- Root component ---------------------------------------------------------------------------

export function SchemaEditor() {
  const [schemas, setSchemas] = useState<SchemaSummary[]>([]);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; message: string } | null>(
    null,
  );
  const [reloadToken, setReloadToken] = useState(0);

  function reload() {
    setReloadToken((token) => token + 1);
  }

  useEffect(() => {
    const controller = new AbortController();
    setListState("loading");
    fetch("/api/schemas?status=ALL", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Schema list request failed");
        return response.json() as Promise<unknown>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        const list = Array.isArray(payload) ? (payload as SchemaSummary[]) : [];
        setSchemas(list);
        setListState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setListState("error");
      });
    return () => controller.abort();
  }, [reloadToken]);

  const grouped = useMemo(() => {
    const map = new Map<string, SchemaSummary[]>();
    for (const schema of schemas) {
      const versions = map.get(schema.schema_id) ?? [];
      versions.push(schema);
      map.set(schema.schema_id, versions);
    }
    for (const versions of map.values()) versions.sort((a, b) => b.schema_version - a.schema_version);
    return map;
  }, [schemas]);

  const selected = useMemo(() => {
    if (!selectedKey) return null;
    const [schemaId, versionText] = selectedKey.split(":");
    const version = Number(versionText);
    return schemas.find((s) => s.schema_id === schemaId && s.schema_version === version) ?? null;
  }, [schemas, selectedKey]);

  return (
    <section className="schema-editor" aria-labelledby="schema-editor-title">
      <div className="schema-heading">
        <div>
          <p className="eyebrow">Extraction schemas</p>
          <h2 id="schema-editor-title">Schema library</h2>
        </div>
        <button
          type="button"
          className="primary-action"
          onClick={() => {
            setCreating(true);
            setSelectedKey(null);
          }}
        >
          <Plus size={16} aria-hidden="true" /> New schema
        </button>
      </div>

      {notice ? <p className={`notice notice-${notice.kind}`} role="status">{notice.message}</p> : null}

      <div className="schema-editor-layout">
        <nav className="schema-list" aria-label="Registered schemas">
          {listState === "loading" ? <p>Loading schemas…</p> : null}
          {listState === "error" ? <p role="alert">Schemas could not be loaded.</p> : null}
          {listState === "ready" && grouped.size === 0 ? <p>No schemas yet.</p> : null}
          {[...grouped.entries()].map(([schemaId, versions]) => (
            <div className="schema-list-group" key={schemaId}>
              <strong>{versions[0].display_name}</strong>
              <ul>
                {versions.map((version) => {
                  const key = `${version.schema_id}:${version.schema_version}`;
                  return (
                    <li key={key}>
                      <button
                        type="button"
                        className={key === selectedKey ? "schema-list-item active" : "schema-list-item"}
                        onClick={() => {
                          setCreating(false);
                          setSelectedKey(key);
                        }}
                      >
                        <span>v{version.schema_version}</span>
                        <span className={`schema-status schema-status-${version.status.toLowerCase()}`}>
                          {version.status}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="schema-editor-main">
          {creating ? (
            <CreateSchemaForm
              onCreated={(detail) => {
                setCreating(false);
                setNotice({ kind: "success", message: `${detail.display_name} created as a draft.` });
                reload();
                setSelectedKey(`${detail.schema_id}:${detail.schema_version}`);
              }}
              onCancel={() => setCreating(false)}
            />
          ) : null}
          {!creating && selected ? (
            <SchemaDetailPanel
              summary={selected}
              onChanged={(message) => {
                setNotice(message);
                reload();
              }}
              onSelect={(schemaId, version) => setSelectedKey(`${schemaId}:${version}`)}
            />
          ) : null}
          {!creating && !selected ? (
            <p className="schema-message">Select a schema version, or create a new one.</p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function CreateSchemaForm({
  onCreated,
  onCancel,
}: {
  onCreated: (detail: SchemaDetail) => void;
  onCancel: () => void;
}) {
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [rootMode, setRootMode] = useState<"SINGLE_RECORD" | "REPEATED_RECORDS">("SINGLE_RECORD");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/schemas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: displayName,
          description: description || null,
          root_mode: rootMode,
        }),
      });
      const payload = (await response.json()) as SchemaDetail | { error?: { message?: string } };
      if (!response.ok) {
        throw new Error((payload as { error?: { message?: string } }).error?.message ?? "Could not create schema.");
      }
      onCreated(payload as SchemaDetail);
    } catch (submitError: unknown) {
      setError(submitError instanceof Error ? submitError.message : "Could not create schema.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="schema-create-form">
      <h3>Create a schema</h3>
      <p>Can one document contain multiple records of this type?</p>
      <div className="root-mode-choice">
        <label>
          <input
            type="radio"
            name="root-mode"
            checked={rootMode === "SINGLE_RECORD"}
            onChange={() => setRootMode("SINGLE_RECORD")}
          />
          No — one record per document (e.g. a tax form)
        </label>
        <label>
          <input
            type="radio"
            name="root-mode"
            checked={rootMode === "REPEATED_RECORDS"}
            onChange={() => setRootMode("REPEATED_RECORDS")}
          />
          Yes — one or many records per document (e.g. invoices)
        </label>
      </div>
      <label className="field-label" htmlFor="new-schema-name">Name</label>
      <input
        id="new-schema-name"
        value={displayName}
        onChange={(event) => setDisplayName(event.target.value)}
        placeholder="e.g. Tax form W-2"
      />
      <label className="field-label" htmlFor="new-schema-description">Description (optional)</label>
      <input
        id="new-schema-description"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
      />
      {error ? <p className="notice notice-error" role="alert">{error}</p> : null}
      <div className="schema-form-actions">
        <button type="button" className="primary-action" disabled={!displayName.trim() || submitting} onClick={() => void submit()}>
          {submitting ? <LoaderCircle className="spin" size={16} aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}
          Create draft
        </button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function SchemaDetailPanel({
  summary,
  onChanged,
  onSelect,
}: {
  summary: SchemaSummary;
  onChanged: (notice: { kind: "success" | "error"; message: string }) => void;
  onSelect: (schemaId: string, version: number) => void;
}) {
  const [detail, setDetail] = useState<SchemaDetail | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [fields, setFields] = useState<EditableField[]>([]);
  const [instructions, setInstructions] = useState("");
  const [jsonDraft, setJsonDraft] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationReport | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    setValidation(null);
    fetch(`/api/schemas/${encodeURIComponent(summary.schema_id)}/versions/${summary.schema_version}`, {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("Schema detail request failed");
        return response.json() as Promise<SchemaDetail>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setDetail(payload);
        const editable = fromApiRoot(payload.schema_tree);
        setFields(editable);
        setInstructions(payload.instructions);
        setJsonDraft(JSON.stringify(payload.schema_tree, null, 2));
        setState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setState("error");
      });
    return () => controller.abort();
  }, [summary.schema_id, summary.schema_version]);

  const depth = useMemo(() => maxDepth(fields), [fields]);
  const leaves = useMemo(() => countLeaves(fields), [fields]);

  function applyJson() {
    try {
      const parsed = JSON.parse(jsonDraft) as Record<string, ApiSchemaField>;
      setFields(fromApiRoot(parsed));
      setJsonError(null);
    } catch {
      setJsonError("That is not valid JSON.");
    }
  }

  function syncJsonFromTree(next: EditableField[]) {
    setFields(next);
    setJsonDraft(JSON.stringify(toApiRoot(next), null, 2));
  }

  async function validateOnServer() {
    setBusy(true);
    try {
      const response = await fetch(`/api/schemas/${encodeURIComponent(summary.schema_id)}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ai_extract_schema: toApiRoot(fields) }),
      });
      const payload = (await response.json()) as ValidationReport;
      setValidation(payload);
    } catch {
      onChanged({ kind: "error", message: "Validation request failed." });
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/schemas/${encodeURIComponent(summary.schema_id)}/draft?schema_version=${summary.schema_version}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ instructions, ai_extract_schema: toApiRoot(fields) }),
        },
      );
      const payload = (await response.json()) as SchemaDetail | { error?: { message?: string } };
      if (!response.ok) {
        throw new Error((payload as { error?: { message?: string } }).error?.message ?? "Could not save draft.");
      }
      onChanged({ kind: "success", message: "Draft saved." });
    } catch (error: unknown) {
      onChanged({ kind: "error", message: error instanceof Error ? error.message : "Could not save draft." });
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/schemas/${encodeURIComponent(summary.schema_id)}/publish?schema_version=${summary.schema_version}`,
        { method: "POST" },
      );
      const payload = (await response.json()) as SchemaDetail | { error?: { message?: string } };
      if (!response.ok) {
        throw new Error((payload as { error?: { message?: string } }).error?.message ?? "Could not publish.");
      }
      onChanged({ kind: "success", message: "Schema published. It is now immutable and extractable." });
    } catch (error: unknown) {
      onChanged({ kind: "error", message: error instanceof Error ? error.message : "Could not publish." });
    } finally {
      setBusy(false);
    }
  }

  async function clone() {
    setBusy(true);
    try {
      const response = await fetch(
        `/api/schemas/${encodeURIComponent(summary.schema_id)}/clone?schema_version=${summary.schema_version}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_display_name: `${summary.display_name} (copy)` }),
        },
      );
      const payload = (await response.json()) as SchemaDetail | { error?: { message?: string } };
      if (!response.ok) {
        throw new Error((payload as { error?: { message?: string } }).error?.message ?? "Could not clone.");
      }
      const cloned = payload as SchemaDetail;
      onChanged({ kind: "success", message: `Draft version ${cloned.schema_version} created for editing.` });
      onSelect(cloned.schema_id, cloned.schema_version);
    } catch (error: unknown) {
      onChanged({ kind: "error", message: error instanceof Error ? error.message : "Could not clone." });
    } finally {
      setBusy(false);
    }
  }

  if (state === "loading") return <p>Loading schema…</p>;
  if (state === "error" || !detail) return <p role="alert">Schema could not be loaded.</p>;

  const editable = detail.is_editable;

  return (
    <div className="schema-detail-panel">
      <div className="schema-provenance" aria-label="Schema provenance">
        <span className={`schema-status schema-status-${detail.status.toLowerCase()}`}>{detail.status}</span>
        <span>Version {detail.schema_version}</span>
        <span>{detail.root_mode === "REPEATED_RECORDS" ? "Repeated records" : "Single record"}</span>
        <span className={depth > MAX_DEPTH ? "limit-exceeded" : ""}>Depth {depth}/{MAX_DEPTH}</span>
        <span className={leaves > MAX_LEAVES ? "limit-exceeded" : ""}>Fields {leaves}/{MAX_LEAVES}</span>
      </div>

      {editable ? (
        <>
          <label className="field-label" htmlFor="schema-instructions">Extraction instructions</label>
          <textarea
            id="schema-instructions"
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            rows={2}
          />

          <div className="schema-tree-editor">
            <h4>Fields</h4>
            <SchemaTree
              fields={fields}
              onChange={syncJsonFromTree}
            />
          </div>

          <details className="schema-json-editor">
            <summary>Advanced: edit as JSON</summary>
            <textarea
              value={jsonDraft}
              onChange={(event) => setJsonDraft(event.target.value)}
              rows={12}
              spellCheck={false}
            />
            <button type="button" onClick={applyJson}>Apply JSON to tree</button>
            {jsonError ? <p className="notice notice-error">{jsonError}</p> : null}
          </details>

          {validation ? (
            <div className={validation.valid ? "notice notice-success" : "notice notice-error"} role="status">
              {validation.valid ? (
                <span><CheckCircle2 size={15} aria-hidden="true" /> Schema is valid.</span>
              ) : (
                <ul>{validation.errors.map((error) => <li key={error}>{error}</li>)}</ul>
              )}
            </div>
          ) : null}

          <div className="schema-form-actions">
            <button type="button" disabled={busy} onClick={() => void validateOnServer()}>Test schema</button>
            <button type="button" disabled={busy} onClick={() => void saveDraft()}>Save draft</button>
            <button type="button" className="primary-action" disabled={busy} onClick={() => void publish()}>Publish</button>
          </div>
        </>
      ) : (
        <>
          <p className="schema-instructions">{detail.instructions}</p>
          <ReadOnlyTree tree={detail.schema_tree} />
          <div className="schema-form-actions">
            <button type="button" disabled={busy} onClick={() => void clone()}>
              <Copy size={15} aria-hidden="true" /> Clone to a new draft
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function SchemaTree({
  fields,
  onChange,
}: {
  fields: EditableField[];
  onChange: (next: EditableField[]) => void;
}) {
  function updateAt(index: number, updated: EditableField) {
    const next = [...fields];
    next[index] = updated;
    onChange(next);
  }
  function removeAt(index: number) {
    onChange(fields.filter((_, i) => i !== index));
  }
  function addField() {
    onChange([...fields, newField(`field_${fields.length + 1}`)]);
  }
  return (
    <div className="schema-node-list">
      {fields.map((field, index) => (
        <SchemaNode
          key={field.id}
          field={field}
          onChange={(updated) => updateAt(index, updated)}
          onRemove={() => removeAt(index)}
        />
      ))}
      <button type="button" className="schema-add-field" onClick={addField}>
        <Plus size={14} aria-hidden="true" /> Add field
      </button>
    </div>
  );
}

function SchemaNode({
  field,
  onChange,
  onRemove,
}: {
  field: EditableField;
  onChange: (next: EditableField) => void;
  onRemove: () => void;
}) {
  return (
    <div className="schema-node" role="group" aria-label={field.name || "field"}>
      <div className="schema-node-row">
        <input
          className="schema-node-name"
          value={field.name}
          placeholder="field_name"
          onChange={(event) => onChange({ ...field, name: event.target.value })}
          aria-label="Field name"
        />
        <select
          value={field.type}
          aria-label="Data type"
          onChange={(event) => {
            const type = event.target.value as FieldType;
            onChange({
              ...field,
              type,
              items: type === "array" ? field.items ?? newField("") : field.items,
              properties: type === "object" ? field.properties : field.properties,
            });
          }}
        >
          {Object.entries(FIELD_TYPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <input
          className="schema-node-description"
          value={field.description}
          placeholder="Description"
          aria-label="Field description"
          onChange={(event) => onChange({ ...field, description: event.target.value })}
        />
        <button type="button" className="schema-node-remove" onClick={onRemove} aria-label={`Remove ${field.name}`}>
          <Trash2 size={14} aria-hidden="true" />
        </button>
      </div>
      {field.type === "enum" ? (
        <input
          className="schema-node-labels"
          value={field.labels}
          placeholder="Comma-separated choices"
          aria-label="Choices"
          onChange={(event) => onChange({ ...field, labels: event.target.value })}
        />
      ) : null}
      {field.type === "object" ? (
        <div className="schema-node-children">
          <SchemaTree
            fields={field.properties}
            onChange={(next) => onChange({ ...field, properties: next })}
          />
        </div>
      ) : null}
      {field.type === "array" ? (
        <div className="schema-node-children">
          <p className="schema-node-hint">Each item of this repeating group:</p>
          <SchemaNode
            field={field.items ?? newField("")}
            onChange={(next) => onChange({ ...field, items: next })}
            onRemove={() => onChange({ ...field, items: newField("") })}
          />
        </div>
      ) : null}
    </div>
  );
}

function ReadOnlyTree({ tree }: { tree: Record<string, ApiSchemaField> }) {
  return (
    <ul className="schema-readonly-tree">
      {Object.entries(tree).map(([name, node]) => (
        <ReadOnlyNode key={name} name={name} node={node} />
      ))}
    </ul>
  );
}

function ReadOnlyNode({ name, node }: { name: string; node: ApiSchemaField }) {
  return (
    <li>
      <strong>{name}</strong> <code>{FIELD_TYPE_LABELS[node.type]}</code>
      <span> — {node.description}</span>
      {node.properties ? <ReadOnlyTree tree={node.properties} /> : null}
      {node.items?.properties ? <ReadOnlyTree tree={node.items.properties} /> : null}
    </li>
  );
}
