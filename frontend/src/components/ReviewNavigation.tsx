import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { type ListContext, previousCursor, rememberCursor } from "../lib/listNavigation";

export function ReviewNavigation({ id, kind }: { id: string; kind: "documents" | "results" }) {
  const location = useLocation(); const navigate = useNavigate();
  const context = (location.state as { list?: ListContext } | null)?.list;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!context || context.kind !== kind || !context.ids.includes(id)) return null;
  const index = context.ids.indexOf(id);
  async function move(direction: number) {
    if (!context) return;
    let updated = context;
    let target = context.ids[index + direction];
    setError(null);
    if (!target) {
      const cursor = direction > 0 ? context.next : context.previous;
      if (cursor == null) return;
      setBusy(true);
      try {
        const query = new URLSearchParams(context.query);
        if (cursor) query.set("cursor", cursor); else query.delete("cursor");
        const result = await fetch(`${context.endpoint}?${query}`);
        if (!result.ok) throw new Error("The adjacent page is unavailable. Return to the list and reset it.");
        const data = await result.json();
        const ids = data.items.map((row: { document_id: string; extraction_run_id: string }) => kind === "documents" ? row.document_id : row.extraction_run_id);
        const base = new URLSearchParams(query); base.delete("cursor");
        const key = `${context.endpoint}?${base}`;
        rememberCursor(key, data.next_cursor, cursor);
        const url = new URL(context.url, window.location.origin);
        if (cursor) url.searchParams.set("cursor", cursor); else url.searchParams.delete("cursor");
        updated = { ...context, ids, query: query.toString(), url: url.pathname + url.search,
          next: data.next_cursor, previous: previousCursor(key, cursor) };
        target = direction > 0 ? ids[0] : ids.at(-1);
      } catch (cause) { setError(String(cause)); return; }
      finally { setBusy(false); }
    }
    if (target) navigate(`/${kind}/${target}`, { state: { list: updated } });
  }
  return <nav aria-label="Review navigation">
    <Link to={context.url}>Back to {kind}</Link>{" "}
    <button type="button" disabled={busy || (index === 0 && context.previous === undefined)} onClick={() => void move(-1)}>Previous {kind === "results" ? "result" : "document"}</button>{" "}
    <button type="button" disabled={busy || (index === context.ids.length - 1 && !context.next)} onClick={() => void move(1)}>Next {kind === "results" ? "result" : "document"}</button>
    {error ? <p role="alert">{error}</p> : null}
  </nav>;
}
