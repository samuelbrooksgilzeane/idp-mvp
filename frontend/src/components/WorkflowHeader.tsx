const workflow = ["Foundation", "Ingest", "Parse", "Extract", "Validate"];

type WorkflowHeaderProps = {
  appName: string;
  activeStep: number;
};

export function WorkflowHeader({ appName, activeStep }: WorkflowHeaderProps) {
  return (
    <header className="workflow-header">
      <div className="brand-lockup">
        <span className="brand-mark" aria-hidden="true">IDP</span>
        <div><strong>{appName}</strong><span>Document workflow</span></div>
      </div>
      <nav aria-label="MVP workflow">
        <ol className="workflow-steps">
          {workflow.map((step, index) => (
            <li
              className={index === activeStep ? "active" : index < activeStep ? "complete" : undefined}
              key={step}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>{step}
            </li>
          ))}
        </ol>
      </nav>
    </header>
  );
}
