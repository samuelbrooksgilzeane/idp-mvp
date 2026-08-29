import { NavLink } from "react-router-dom";

const NAVIGATION = [
  { label: "Documents", to: "/", end: true },
  { label: "Results", to: "/results", end: false },
  { label: "Schema", to: "/schema", end: false },
];

type WorkflowHeaderProps = { appName: string };

export function WorkflowHeader({ appName }: WorkflowHeaderProps) {
  return (
    <header className="workflow-header">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">IDP</span>
        <div><strong>{appName}</strong><span>Document workflow</span></div>
      </div>
      <nav aria-label="Sections">
        <ul className="workflow-steps">
          {NAVIGATION.map((item, index) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? "active" : undefined)}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>{item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </header>
  );
}
