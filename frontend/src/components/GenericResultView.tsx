import { AlertTriangle, ChevronLeft, ChevronRight, MapPin } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";

import type { GenericField } from "../types";
import type { CitationTarget } from "./DocumentViewer";
import type { CitationCoordinate } from "./viewerGeometry";

export type FieldPolicy = { confidenceThreshold: number; citationRequired: boolean };

type GenericResultViewProps = {
  rootMode: "SINGLE_RECORD" | "REPEATED_RECORDS";
  hierarchy: Record<string, unknown>;
  fields: GenericField[];
  fieldPolicies: Map<string, FieldPolicy>;
  onViewEvidence: (target: CitationTarget) => void;
};

/** A leaf as `ai_extract` returns it: `{ value, confidence_score, citation_ids, ... }`. Any
 * other object is a nested group, distinguished from a leaf by the absence of `value`. */
function isLeafNode(node: unknown): node is { value: unknown } {
  return typeof node === "object" && node !== null && !Array.isArray(node) && "value" in node;
}

function isFlatRecordArray(items: unknown[]): items is Record<string, unknown>[] {
  return (
    items.length > 0 &&
    items.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        !Array.isArray(item) &&
        !isLeafNode(item) &&
        Object.values(item as Record<string, unknown>).every(isLeafNode),
    )
  );
}

export function GenericResultView({
  rootMode,
  hierarchy,
  fields,
  fieldPolicies,
  onViewEvidence,
}: GenericResultViewProps) {
  const fieldsByInstancePath = useMemo(
    () => new Map(fields.map((field) => [field.instance_path, field])),
    [fields],
  );
  const evidenceNonce = useMemo(() => ({ current: 0 }), []);
  const [recordIndex, setRecordIndex] = useState(0);

  const rootEntries = Object.entries(hierarchy);

  function viewEvidence(instancePath: string) {
    const field = fieldsByInstancePath.get(instancePath);
    if (!field) return;
    const boxes: CitationCoordinate[] = field.citations.flatMap((citation) =>
      citation.bbox.map((box) => ({ page_id: box.page_id, coord: box.coord })),
    );
    if (boxes.length === 0) return;
    evidenceNonce.current += 1;
    onViewEvidence({
      pageId: boxes[0].page_id,
      fieldLabel: field.field_name,
      boxes,
      nonce: evidenceNonce.current,
    });
  }

  function renderLeaf(node: { value: unknown }, instancePath: string, label: string) {
    const field = fieldsByInstancePath.get(instancePath);
    const policy = field ? fieldPolicies.get(field.schema_path.replace("[]", "[*]")) : undefined;
    const lowConfidence =
      field?.confidence_score != null &&
      policy != null &&
      field.confidence_score < policy.confidenceThreshold;
    const missingCitation = policy?.citationRequired && field && field.citations.length === 0;
    const hasEvidence = Boolean(field && field.citations.length > 0);
    const displayValue =
      node.value === null || node.value === undefined || node.value === ""
        ? "—"
        : String(node.value);
    return (
      <div className="result-field" key={instancePath}>
        <span className="result-field-label">{label}</span>
        <button
          type="button"
          className={`result-field-value${lowConfidence || missingCitation ? " result-field-issue" : ""}`}
          disabled={!hasEvidence}
          onClick={() => viewEvidence(instancePath)}
        >
          {displayValue}
          {hasEvidence ? <MapPin size={12} aria-hidden="true" /> : null}
        </button>
        {lowConfidence || missingCitation ? (
          <span className="result-field-flag">
            <AlertTriangle size={11} aria-hidden="true" />
            {lowConfidence ? "Low confidence" : "Missing citation"}
          </span>
        ) : null}
      </div>
    );
  }

  function renderGroup(node: Record<string, unknown>, prefix: string) {
    return (
      <div className="result-group">
        {Object.entries(node).map(([key, value]) => renderNode(value, prefix ? `${prefix}.${key}` : key, key))}
      </div>
    );
  }

  function renderNode(node: unknown, instancePath: string, label: string): ReactNode {
    if (isLeafNode(node)) return renderLeaf(node, instancePath, label);
    if (Array.isArray(node)) return renderArray(node, instancePath, label);
    if (typeof node === "object" && node !== null) {
      return (
        <details className="result-nested-group" open key={instancePath}>
          <summary>{label}</summary>
          {renderGroup(node as Record<string, unknown>, instancePath)}
        </details>
      );
    }
    return null;
  }

  function renderArray(items: unknown[], instancePath: string, label: string) {
    if (items.length === 0) {
      return (
        <p className="result-empty-array" key={instancePath}>
          {label}: none stated
        </p>
      );
    }
    if (isFlatRecordArray(items)) {
      const columns = Object.keys(items[0]);
      return (
        <div className="result-nested-table" key={instancePath}>
          <p className="result-nested-table-label">{label} ({items.length})</p>
          <table>
            <thead>
              <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
            </thead>
            <tbody>
              {items.map((item, index) => (
                <tr key={index}>
                  {columns.map((column) => {
                    const leaf = (item as Record<string, unknown>)[column] as
                      | { value: unknown }
                      | undefined;
                    const itemPath = `${instancePath}[${index}].${column}`;
                    const field = fieldsByInstancePath.get(itemPath);
                    const hasEvidence = Boolean(field && field.citations.length > 0);
                    return (
                      <td key={column}>
                        <button
                          type="button"
                          className="result-field-value result-field-value-inline"
                          disabled={!hasEvidence}
                          onClick={() => viewEvidence(itemPath)}
                        >
                          {leaf?.value === null || leaf?.value === undefined || leaf.value === ""
                            ? "—"
                            : String(leaf.value)}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return (
      <details className="result-nested-group" open key={instancePath}>
        <summary>{label} ({items.length})</summary>
        {items.map((item, index) => (
          <div className="result-array-item" key={index}>
            <p className="result-array-item-label">Item {index + 1}</p>
            {renderNode(item, `${instancePath}[${index}]`, `${label} ${index + 1}`)}
          </div>
        ))}
      </details>
    );
  }

  if (rootMode === "REPEATED_RECORDS" && rootEntries.length === 1) {
    const [rootKey, rootValue] = rootEntries[0];
    const records = Array.isArray(rootValue) ? rootValue : [];
    if (records.length === 0) {
      return <p className="result-empty-array">No {rootKey} stated in this document.</p>;
    }
    const current = records[Math.min(recordIndex, records.length - 1)];
    return (
      <div className="result-record-pager">
        <div className="result-record-nav">
          <button
            type="button"
            disabled={recordIndex === 0}
            onClick={() => setRecordIndex((value) => Math.max(0, value - 1))}
            aria-label="Previous record"
          >
            <ChevronLeft size={14} aria-hidden="true" />
          </button>
          <span>Record {recordIndex + 1} of {records.length}</span>
          <button
            type="button"
            disabled={recordIndex >= records.length - 1}
            onClick={() => setRecordIndex((value) => Math.min(records.length - 1, value + 1))}
            aria-label="Next record"
          >
            <ChevronRight size={14} aria-hidden="true" />
          </button>
        </div>
        {renderNode(current, `${rootKey}[${recordIndex}]`, rootKey)}
      </div>
    );
  }

  return <>{renderGroup(hierarchy, "")}</>;
}
