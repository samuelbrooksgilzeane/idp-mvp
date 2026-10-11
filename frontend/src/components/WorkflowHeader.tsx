import { Braces, FileText, MessageSquare, Table2 } from "lucide-react";
import { NavLink } from "react-router-dom";

const NAVIGATION = [
  { label: "Documents", to: "/", end: true, Icon: FileText },
  { label: "Results", to: "/results", end: false, Icon: Table2 },
  { label: "Schemas", to: "/schema", end: false, Icon: Braces },
];

type WorkflowHeaderProps = { appName: string; chatEnabled?: boolean };

/** Sidebar navigation. */
export function WorkflowHeader({ appName, chatEnabled }: WorkflowHeaderProps) {
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
          {chatEnabled ? (
            <li>
              <NavLink to="/chat" className={({ isActive }) => (isActive ? "active" : undefined)}>
                <MessageSquare size={16} aria-hidden="true" />Ask documents
              </NavLink>
            </li>
          ) : null}
        </ul>
      </nav>
    </aside>
  );
}
