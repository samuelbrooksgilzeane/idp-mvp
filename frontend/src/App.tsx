import { useEffect, useState } from "react";

type HealthResponse = {
  status: "ok";
  mode: "mock" | "databricks";
  application_name: string;
  configuration: Record<string, boolean>;
};

type RuntimeState =
  | { kind: "loading" }
  | { kind: "ready"; health: HealthResponse }
  | { kind: "unavailable" };

const workflow = ["Foundation", "Ingest", "Parse", "Extract", "Validate"];

export function App() {
  const [runtime, setRuntime] = useState<RuntimeState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    fetch("/api/health", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health request failed");
        return response.json() as Promise<HealthResponse>;
      })
      .then((health) => setRuntime({ kind: "ready", health }))
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setRuntime({ kind: "unavailable" });
        }
      });

    return () => controller.abort();
  }, []);

  const apiStatus = runtime.kind === "ready" ? "Reachable" : runtime.kind === "loading" ? "Checking" : "Unavailable";
  const runtimeMode = runtime.kind === "ready" ? runtime.health.mode : "unknown";
  const appName = runtime.kind === "ready" ? runtime.health.application_name : "IDP MVP";

  return (
    <div className="app-shell">
      <header className="workflow-header">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">IDP</span>
          <div>
            <strong>{appName}</strong>
            <span>Document workflow</span>
          </div>
        </div>
        <nav aria-label="MVP workflow">
          <ol className="workflow-steps">
            {workflow.map((step, index) => (
              <li className={index === 0 ? "active" : undefined} key={step}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                {step}
              </li>
            ))}
          </ol>
        </nav>
      </header>

      <main>
        <section className="workspace" aria-labelledby="workspace-title">
          <div className="workspace-copy">
            <p className="eyebrow">Workspace state</p>
            <h1 id="workspace-title">MVP not configured</h1>
            <p className="summary">
              The application foundation is available. Document intake and processing begin in later increments.
            </p>
          </div>

          <dl className="runtime-status" aria-label="Runtime status">
            <div>
              <dt>Runtime</dt>
              <dd>{runtimeMode}</dd>
            </div>
            <div>
              <dt>API</dt>
              <dd className={`status-${apiStatus.toLowerCase()}`}>
                <span className="status-dot" aria-hidden="true" />
                {apiStatus}
              </dd>
            </div>
            <div>
              <dt>Capability</dt>
              <dd>Foundation only</dd>
            </div>
          </dl>
        </section>
      </main>

      <footer>
        <span>Project foundation</span>
        <span>No external connections</span>
      </footer>
    </div>
  );
}
