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
    {!genie.enabled ? <p>Genie is not configured for this project yet.</p> : <>
      {genie.embed_url && !unavailable ? <iframe className="genie-frame" src={genie.embed_url}
        title={`${config.project_name} Genie`} allow="clipboard-write" onError={() => setUnavailable(true)} />
        : <p role="alert">{unavailable ? "The embedded view is unavailable." : "Embedding is not configured."}</p>}
    </>}
  </section>;
}
