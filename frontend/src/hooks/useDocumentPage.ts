import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { DocumentRecord } from "../types";

export function useDocumentPage(enabled: boolean) {
  const [params, setParams] = useSearchParams();
  const caseId = params.get("case") || null;
  const status = params.get("status") || "";
  const search = params.get("search") || "";
  const cursor = params.get("cursor") || "";
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0);
  const previousCursors = useRef(new Map<string, string>());
  const queryKey = JSON.stringify([caseId, status, search]);

  useEffect(() => {
    const current = ++generation.current;
    if (!enabled) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: "50" });
    if (caseId) query.set("case_id", caseId);
    if (status) query.set("status", status);
    if (search) query.set("search", search);
    if (cursor) query.set("cursor", cursor);
    void fetch(`/api/documents/page?${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(response.status === 422
          ? "This page is no longer available. Reset the list to its first page."
          : "Documents could not be loaded. Try refreshing the list.");
        return response.json() as Promise<{ items: DocumentRecord[]; next_cursor: string | null }>;
      })
      .then((payload) => {
        if (controller.signal.aborted || current !== generation.current) return;
        setDocuments(payload.items);
        setNextCursor(payload.next_cursor);
        if (payload.next_cursor) {
          const cache = previousCursors.current;
          cache.set(`${queryKey}:${payload.next_cursor}`, cursor);
          if (cache.size > 100) cache.delete(cache.keys().next().value!);
        }
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted || current !== generation.current) return;
        setDocuments([]);
        setNextCursor(null);
        setError(failure instanceof Error ? failure.message : "Documents could not be loaded.");
      })
      .finally(() => {
        if (!controller.signal.aborted && current === generation.current) setLoading(false);
      });
    return () => controller.abort();
  }, [enabled, caseId, status, search, cursor, revision, queryKey]);

  const changeFilter = (name: string, value: string | null) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set(name, value); else next.delete(name);
      next.delete("cursor");
      return next;
    });
  };
  const changeCursor = (value: string) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set("cursor", value); else next.delete("cursor");
      return next;
    });
  };
  const refresh = useCallback(() => { setRevision((value) => value + 1); }, []);
  return {
    documents, loading, error, caseId, status, search, cursor, nextCursor, refresh,
    changeFilter, changeCursor,
    previousCursor: previousCursors.current.get(`${queryKey}:${cursor}`),
  };
}
