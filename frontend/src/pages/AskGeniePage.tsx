import { useEffect, useState } from "react";
import type { AppConfig } from "../types";

export function AskGeniePage() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/app-config", { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error("Configuration unavailable");
      const value = await response.json() as AppConfig;
      if (!controller.signal.aborted) setConfig(value);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, []);
  if (error) return <p role="alert">Genie configuration could not be loaded. Refresh to retry.</p>;
  if (!config) return <p role="status">Loading Genie…</p>;
  const genie = config.genie;
  return <section className="genie-workspace" aria-label="Ask Genie">
    <h2>Ask about {config.project_name}</h2>
    <p>{genie.coverage}</p>
    {!genie.enabled ? <p>Genie is not configured for this project yet.</p> : <>
      {genie.open_url ? <p><a href={genie.open_url} target="_blank" rel="noopener noreferrer">Open Genie in Databricks</a></p> : null}
      <p>Sign in to Databricks with access to this agent and its data. If the embedded view is blocked or asks you to sign in repeatedly, open Genie in Databricks.</p>
      {genie.embed_url && !unavailable ? <iframe className="genie-frame" src={genie.embed_url}
        title={`${config.project_name} Genie`} allow="clipboard-write" onError={() => setUnavailable(true)} />
        : <p>{unavailable ? "The embedded view is hidden or unavailable." : "Embedding is not configured. Use the link above."}</p>}
      {genie.embed_url && !unavailable ? <button type="button" onClick={() => setUnavailable(true)}>Hide embedded view</button> : null}
      <p>Leaving this page may discard an unsent prompt. Saved conversations are managed by Genie.</p>
    </>}
  </section>;
}
