import { Braces, ExternalLink, FileText, MessageSquare, Table2 } from "lucide-react";
import { NavLink } from "react-router-dom";

const NAVIGATION = [
  { label: "Documents", to: "/", end: true, Icon: FileText },
  { label: "Results", to: "/results", end: false, Icon: Table2 },
  { label: "Schemas", to: "/schema", end: false, Icon: Braces },
];

type WorkflowHeaderProps = {
  appName: string; runtimeMode?: string; apiStatus?: string; chatAppUrl?: string | null;
};

/** Sidebar navigation; the runtime footer only reports live connection state. */
export function WorkflowHeader({ appName, runtimeMode, apiStatus, chatAppUrl }: WorkflowHeaderProps) {
  return (
    <aside className="app-sidebar">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">IDP</span>
        <div><strong>{appName}</strong><span>Document workflow</span></div>
      </div>
      <nav aria-label="Sections">
        <ul className="workflow-steps">
          {NAVIGATION.map(({ label, to, end, Icon }) => (
            <li key={to}>
              <NavLink to={to} end={end} className={({ isActive }) => (isActive ? "active" : undefined)}>
                <Icon size={16} aria-hidden="true" />{label}
              </NavLink>
            </li>
          ))}
          {chatAppUrl ? (
            <li>
              <a href={chatAppUrl} target="_blank" rel="noopener noreferrer"
                aria-label="Ask documents (opens in a new tab)">
                <MessageSquare size={16} aria-hidden="true" />Ask documents
                <ExternalLink size={12} aria-hidden="true" className="external-mark" />
              </a>
            </li>
          ) : null}
        </ul>
      </nav>
      {apiStatus ? (
        <dl className="sidebar-footer" aria-label="Runtime status">
          <div>
            <dt>API</dt>
            <dd className={`status-${apiStatus.toLowerCase()}`}>
              <span className="status-dot" aria-hidden="true" />{apiStatus}
            </dd>
          </div>
          {runtimeMode === "mock" ? <div><dt>Mode</dt><dd>Local mock</dd></div> : null}
        </dl>
      ) : null}
    </aside>
  );
}
