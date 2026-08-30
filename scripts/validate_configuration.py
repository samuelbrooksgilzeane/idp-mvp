from pathlib import Path
from typing import Any

import yaml

from idp_app.core.config import IdpMode, Settings

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_VARIABLES = {
    "catalog",
    "project_schema",
    "table_prefix",
    "source_volume_name",
    "artifacts_volume_name",
    "warehouse_id",
    "validation_endpoint",
    "evaluation_experiment",
    "app_name",
    "batch_concurrency",
}
EXPECTED_BOOTSTRAP_PARAMETERS = {
    "catalog": "${var.catalog}",
    "project_schema": "${var.project_schema}",
    "table_prefix": "${var.table_prefix}",
    "source_volume_name": "${var.source_volume_name}",
    "artifacts_volume_name": "${var.artifacts_volume_name}",
}
EXPECTED_PARSING_MIGRATION_PARAMETERS = {
    "catalog": "${var.catalog}",
    "project_schema": "${var.project_schema}",
    "table_prefix": "${var.table_prefix}",
}


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if not isinstance(data, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain a YAML mapping")
    return data


def validate_app_config() -> None:
    config = load_yaml(ROOT / "app.yaml")
    command = config.get("command")
    if not isinstance(command, list) or "idp_app.main:create_app" not in command:
        raise ValueError("app.yaml must start the IDP FastAPI application factory")

    serialized = yaml.safe_dump(config).lower()
    for forbidden in ("token", "password", "client_secret"):
        if forbidden in serialized:
            raise ValueError(f"app.yaml must not contain {forbidden}")


def validate_bundle_config() -> None:
    config = load_yaml(ROOT / "databricks_etl" / "databricks.yml")
    variables = config.get("variables")
    if not isinstance(variables, dict) or set(variables) != TRUSTED_VARIABLES:
        raise ValueError("databricks.yml trusted variables do not match the technical contract")

    targets = config.get("targets")
    if not isinstance(targets, dict) or set(targets) != {"dev", "prod"}:
        raise ValueError("databricks.yml must define exactly dev and prod targets")
    if targets["dev"]["variables"]["table_prefix"] != "idp_dev":
        raise ValueError("dev must use the idp_dev table prefix")
    if targets["prod"]["variables"]["table_prefix"] != "idp":
        raise ValueError("prod must use the idp table prefix")

    serialized = yaml.safe_dump(config).lower()
    if "workspace:" in serialized or "host:" in serialized:
        raise ValueError("databricks.yml must not hardcode workspace configuration")

    includes = config.get("include")
    if includes != ["resources/*.yml"]:
        raise ValueError("databricks.yml must include the reviewed bundle resources")

    sync = config.get("sync")
    if not isinstance(sync, dict) or sync.get("paths") != [".."]:
        raise ValueError("Bundle sync must include the repository application source")
    if sync.get("include") != ["../frontend/dist/**"]:
        raise ValueError("Bundle sync must include the built production frontend")


def validate_data_bootstrap() -> None:
    resource = load_yaml(ROOT / "databricks_etl" / "resources" / "bootstrap.job.yml")
    jobs = resource.get("resources", {}).get("jobs", {})
    bootstrap = jobs.get("governed_data_bootstrap")
    if not isinstance(bootstrap, dict):
        raise ValueError("Bundle must define the governed_data_bootstrap job")

    tasks = bootstrap.get("tasks")
    expected_tasks = [
        "create_governed_objects",
        "migrate_parsing_columns",
        "migrate_extraction_columns",
        "migrate_repeated_invoices",
        "register_production_schemas",
        "register_production_schemas_v2",
        "register_production_schemas_v3",
        "register_production_schemas_v4",
        "create_governed_views",
        "migrate_generic_schema_registry",
    ]
    if not isinstance(tasks, list) or [t.get("task_key") for t in tasks] != expected_tasks:
        raise ValueError(
            "Governed data bootstrap must contain the reviewed creation, migration, "
            "and schema-registration tasks in order"
        )
    sql_task = tasks[0].get("sql_task", {})
    if sql_task.get("warehouse_id") != "${var.warehouse_id}":
        raise ValueError("Governed data bootstrap must use the trusted warehouse variable")
    if sql_task.get("parameters") != EXPECTED_BOOTSTRAP_PARAMETERS:
        raise ValueError("Governed data bootstrap parameters must match the trusted contract")

    migration_task = tasks[1]
    migration_sql = migration_task.get("sql_task", {})
    if migration_task.get("depends_on") != [{"task_key": "create_governed_objects"}]:
        raise ValueError("Parsing migration must run after governed object creation")
    if migration_sql.get("file", {}).get("path") != "../sql/migrate_parsing.sql":
        raise ValueError("Bootstrap must use the reviewed parsing migration")
    if migration_sql.get("parameters") != EXPECTED_PARSING_MIGRATION_PARAMETERS:
        raise ValueError("Parsing migration parameters must match the trusted contract")

    extraction_migration_task = tasks[2]
    extraction_migration_sql = extraction_migration_task.get("sql_task", {})
    if extraction_migration_task.get("depends_on") != [
        {"task_key": "migrate_parsing_columns"}
    ]:
        raise ValueError("Extraction migration must run after parsing migration")
    if extraction_migration_sql.get("file", {}).get("path") != (
        "../sql/migrate_extraction.sql"
    ):
        raise ValueError("Bootstrap must use the reviewed extraction migration")
    if extraction_migration_sql.get("parameters") != EXPECTED_PARSING_MIGRATION_PARAMETERS:
        raise ValueError("Extraction migration parameters must match the trusted contract")

    repeated_invoice_task = tasks[3]
    repeated_invoice_sql = repeated_invoice_task.get("sql_task", {})
    if repeated_invoice_task.get("depends_on") != [
        {"task_key": "migrate_extraction_columns"}
    ]:
        raise ValueError("Repeated-invoice migration must run after extraction migration")
    if repeated_invoice_sql.get("file", {}).get("path") != (
        "../sql/migrate_repeated_invoices.sql"
    ):
        raise ValueError("Bootstrap must use the reviewed repeated-invoice migration")
    if repeated_invoice_sql.get("parameters") != EXPECTED_PARSING_MIGRATION_PARAMETERS:
        raise ValueError(
            "Repeated-invoice migration parameters must match the trusted contract"
        )

    registration_task = tasks[4]
    registration_python = registration_task.get("spark_python_task", {})
    if registration_task.get("depends_on") != [
        {"task_key": "migrate_repeated_invoices"}
    ]:
        raise ValueError("Schema registration must run after governed migrations")
    if registration_python.get("python_file") != "../src/register_schemas.py":
        raise ValueError("Bootstrap must use the reviewed schema registration task")
    expected_registration_parameters = [
        "--catalog",
        "${var.catalog}",
        "--project-schema",
        "${var.project_schema}",
        "--table-prefix",
        "${var.table_prefix}",
        "--manifest-path",
        "${workspace.file_path}/schemas/invoice_v1.json",
    ]
    if registration_python.get("parameters") != expected_registration_parameters:
        raise ValueError("Schema registration parameters must match the trusted contract")

    view_task = tasks[8]
    view_sql = view_task.get("sql_task", {})
    if view_task.get("depends_on") != [{"task_key": "register_production_schemas_v4"}]:
        raise ValueError("Governed views must be created after the schema registrations")
    if view_sql.get("file", {}).get("path") != "../sql/create_views.sql":
        raise ValueError("Bootstrap must use the reviewed view definitions")
    if view_sql.get("parameters") != EXPECTED_PARSING_MIGRATION_PARAMETERS:
        raise ValueError("View creation parameters must match the trusted contract")

    sql = "\n".join(
        (ROOT / "databricks_etl" / "sql" / name).read_text(encoding="utf-8")
        for name in ("create_objects.sql", "create_views.sql")
    )
    normalized = " ".join(sql.upper().split())
    for forbidden in ("CREATE CATALOG", " DROP ", " TRUNCATE "):
        if forbidden in f" {normalized} ":
            raise ValueError(f"Data bootstrap contains destructive or forbidden SQL: {forbidden}")
    if ".DEFAULT." in normalized:
        raise ValueError("Data bootstrap must not create objects in the default schema")

    migration = (ROOT / "databricks_etl" / "sql" / "migrate_parsing.sql").read_text(
        encoding="utf-8"
    )
    migration_normalized = " ".join(migration.upper().split())
    if "IF NOT EXISTS" not in migration_normalized:
        raise ValueError("Parsing migration must guard existing columns")
    for forbidden in (" DROP ", " TRUNCATE ", " DELETE "):
        if forbidden in f" {migration_normalized} ":
            raise ValueError(f"Parsing migration contains forbidden SQL: {forbidden}")

    repeated_invoice_migration = (
        ROOT / "databricks_etl" / "sql" / "migrate_repeated_invoices.sql"
    ).read_text(encoding="utf-8")
    repeated_normalized = " ".join(repeated_invoice_migration.upper().split())
    if "IF NOT EXISTS" not in repeated_normalized:
        raise ValueError("Repeated-invoice migration must guard existing columns")
    for forbidden in (" DROP ", " TRUNCATE ", " DELETE "):
        if forbidden in f" {repeated_normalized} ":
            raise ValueError(
                f"Repeated-invoice migration contains forbidden SQL: {forbidden}"
            )

    extraction_migration = (
        ROOT / "databricks_etl" / "sql" / "migrate_extraction.sql"
    ).read_text(encoding="utf-8")
    extraction_migration_normalized = " ".join(extraction_migration.upper().split())
    if "IF NOT EXISTS" not in extraction_migration_normalized:
        raise ValueError("Extraction migration must guard existing columns")
    for forbidden in (" DROP ", " TRUNCATE ", " DELETE "):
        if forbidden in f" {extraction_migration_normalized} ":
            raise ValueError(
                f"Extraction migration contains forbidden SQL: {forbidden}"
            )


def reviewed_batch_task(tasks: object, python_file: str, label: str) -> None:
    """A batch job runs exactly one reviewed task per document, in parallel.

    for_each defaults to a concurrency of 1, which would process a batch sequentially, so the
    trusted deployment variable must always be supplied.
    """
    if not isinstance(tasks, list) or len(tasks) != 1:
        raise ValueError(f"{label} must contain exactly one reviewed task")
    for_each = tasks[0].get("for_each_task")
    if not isinstance(for_each, dict):
        raise ValueError(f"{label} must submit its documents through a for_each task")
    if for_each.get("inputs") != "{{job.parameters.inputs}}":
        raise ValueError(f"{label} must take its documents from the trusted inputs parameter")
    if for_each.get("concurrency") != "${var.batch_concurrency}":
        raise ValueError(f"{label} must set concurrency from the trusted deployment variable")
    nested = for_each.get("task", {})
    if nested.get("spark_python_task", {}).get("python_file") != python_file:
        raise ValueError(f"{label} must use the reviewed task source")
    if nested.get("environment_key") != "default":
        raise ValueError(f"{label} must run its task in the reviewed environment")


def validate_parsing_job() -> None:
    resource = load_yaml(ROOT / "databricks_etl" / "resources" / "parsing.job.yml")
    jobs = resource.get("resources", {}).get("jobs", {})
    parsing = jobs.get("document_parser")
    if not isinstance(parsing, dict):
        raise ValueError("Bundle must define the document_parser job")
    reviewed_batch_task(
        parsing.get("tasks"), "../src/parse_document.py", "Document parser"
    )

    source = (ROOT / "databricks_etl" / "src" / "parse_document.py").read_text(
        encoding="utf-8"
    )
    required = (
        "ai_parse_document",
        "'version', '2.0'",
        "'descriptionElementTypes', ''",
        "imageOutputPath",
        "READ_FILES",
    )
    if any(value not in source for value in required):
        raise ValueError("Parsing task does not retain the reviewed parser contract")
    deletion_calls = (".unlink(", "os.remove(", "dbutils.fs.rm(", "shutil.move(")
    if any(call in source for call in deletion_calls):
        raise ValueError("Parsing task must not delete or move source documents")


def validate_extraction_job() -> None:
    resource = load_yaml(ROOT / "databricks_etl" / "resources" / "extraction.job.yml")
    jobs = resource.get("resources", {}).get("jobs", {})
    extraction = jobs.get("document_extractor")
    if not isinstance(extraction, dict):
        raise ValueError("Bundle must define the document_extractor job")
    reviewed_batch_task(
        extraction.get("tasks"), "../src/extract_document.py", "Document extractor"
    )
    source = (ROOT / "databricks_etl" / "src" / "extract_document.py").read_text(
        encoding="utf-8"
    )
    required = (
        "ai_extract(",
        "'version', '2.1'",
        "'mode', 'precision'",
        "'enableCitations', 'true'",
        "'enableConfidenceScores', 'true'",
        "SET ai_result = PARSE_JSON(:result_json)",
        "ORDER BY completed_at DESC, parse_run_id DESC",
    )
    if any(value not in source for value in required):
        raise ValueError("Extraction task does not retain the reviewed extraction contract")
    normalized = " ".join(source.upper().split())
    for forbidden in (" DROP ", " TRUNCATE ", " DELETE "):
        if forbidden in f" {normalized} ":
            raise ValueError(f"Extraction task contains forbidden SQL: {forbidden}")


def validate_application_resource() -> None:
    resource = load_yaml(
        ROOT / "databricks_etl" / "resources" / "application.app.yml"
    )
    apps = resource.get("resources", {}).get("apps", {})
    app = apps.get("idp_app")
    if not isinstance(app, dict):
        raise ValueError("Bundle must define the idp_app Databricks App resource")
    if app.get("source_code_path") != "../..":
        raise ValueError("Databricks App must deploy the reviewed repository source")

    config = app.get("config", {})
    command = config.get("command")
    if not isinstance(command, list) or "idp_app.main:create_app" not in command:
        raise ValueError("Databricks App must start the IDP FastAPI application factory")
    env = {
        item.get("name"): item
        for item in config.get("env", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if env.get("IDP_MODE", {}).get("value") != "databricks":
        raise ValueError("Deployed Databricks App must use databricks mode")
    if env.get("IDP_WAREHOUSE_ID", {}).get("value_from") != "sql-warehouse":
        raise ValueError("Databricks App must use its bound SQL warehouse")
    if env.get("IDP_PARSE_JOB_ID", {}).get("value_from") != "document-parser":
        raise ValueError("Databricks App must use its bound parsing Job")
    if env.get("IDP_EXTRACTION_JOB_ID", {}).get("value_from") != "document-extractor":
        raise ValueError("Databricks App must use its bound extraction Job")

    bindings = app.get("resources")
    if not isinstance(bindings, list):
        raise ValueError("Databricks App must declare least-privilege resources")
    binding_names = {
        item.get("name") for item in bindings if isinstance(item, dict)
    }
    required_bindings = {
        "sql-warehouse",
        "document-parser",
        "document-extractor",
        "source-volume-read",
        "source-volume-write",
        "documents-table-select",
        "documents-table-modify",
        "parsed-documents-table-select",
        "parsed-documents-table-modify",
        "schema-registry-table-select",
        "extraction-runs-table-select",
        "extraction-runs-table-modify",
        "extracted-fields-table-select",
        "invoice-candidates-select",
    }
    if not required_bindings.issubset(binding_names):
        raise ValueError("Databricks App resource bindings are incomplete")


def main() -> None:
    validate_app_config()
    validate_bundle_config()
    validate_data_bootstrap()
    validate_parsing_job()
    validate_extraction_job()
    validate_application_resource()
    if Settings.model_fields["mode"].default is not IdpMode.MOCK:
        raise ValueError("Default local application mode must be mock")
    print("Configuration and YAML validation passed.")


if __name__ == "__main__":
    main()
