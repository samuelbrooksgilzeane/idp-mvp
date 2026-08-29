from dataclasses import dataclass

TABLE_NAMES = (
    "documents",
    "parsed_documents",
    "schema_registry",
    "extraction_runs",
    "extracted_fields",
    "invoice_candidates",
    "invoice_line_candidates",
    "validation_results",
    "validation_runs",
)

VIEW_NAMES = (
    "latest_successful_parses",
    "latest_successful_extractions",
    "validation_summary",
)


@dataclass(frozen=True)
class DataObjectNamespace:
    catalog: str
    project_schema: str
    table_prefix: str

    @property
    def schema_name(self) -> str:
        return f"{self.catalog}.{self.project_schema}"

    def object_name(self, name: str) -> str:
        if name not in TABLE_NAMES + VIEW_NAMES:
            raise ValueError(f"Unknown governed data object: {name}")
        return f"{self.schema_name}.{self.table_prefix}_{name}"

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(self.object_name(name) for name in TABLE_NAMES)

    @property
    def views(self) -> tuple[str, ...]:
        return tuple(self.object_name(name) for name in VIEW_NAMES)
