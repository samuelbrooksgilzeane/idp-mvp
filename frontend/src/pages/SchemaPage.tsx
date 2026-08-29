import { SchemaViewer } from "../components/SchemaViewer";

/** The extraction contract is per use case, not per document, so it has its own page. */
export function SchemaPage() {
  return <SchemaViewer useCase="invoice" />;
}
