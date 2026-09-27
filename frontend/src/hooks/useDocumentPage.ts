import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DocumentRecord } from "../types";
import { useCursorPage, invalidateListPages } from "./useCursorPage";
import { rememberCursor, previousCursor } from "../lib/listNavigation";

export function useDocumentPage(enabled: boolean) {
  const [params, setParams] = useSearchParams();
  const caseId = params.get("case") || null;
  const status = params.get("status") || "";
  const search = params.get("search") || "";
  const cursor = params.get("cursor") || "";
  const [revision, setRevision] = useState(0);
  const query = new URLSearchParams({ limit: "50" });
  if (caseId) query.set("case_id", caseId);
  if (status) query.set("status", status);
  if (search) query.set("search", search);
  const key = `/api/documents/page?${query}`;
  if (cursor) query.set("cursor", cursor);
  const page = useCursorPage<DocumentRecord>(`/api/documents/page?${query}`, enabled, revision);
  rememberCursor(key, page.next_cursor, cursor);
  useEffect(() => {
    if (!enabled || !page.items.some(item => ["PARSE_QUEUED", "PARSING"].includes(item.status))) return;
    const timer = setInterval(() => { if (!document.hidden) setRevision(v => v + 1); }, 5000);
    return () => clearInterval(timer);
  }, [enabled, page.items]);
  const changeFilter = (name: string, value: string | null) => {
    setParams(current => { const next = new URLSearchParams(current);
      if (value) next.set(name, value); else next.delete(name);
      next.delete("cursor"); return next; });
  };
  const changeCursor = (value: string) => {
    setParams(current => { const next = new URLSearchParams(current);
      if (value) next.set("cursor", value); else next.delete("cursor"); return next; });
  };
  const refresh = useCallback(() => { invalidateListPages(); setRevision(v => v + 1); }, []);
  return { documents: page.items, loading: page.loading, error: page.error, caseId, status,
    search, cursor, nextCursor: page.next_cursor, refresh, changeFilter, changeCursor,
    previousCursor: previousCursor(key, cursor), query: query.toString() };
}
