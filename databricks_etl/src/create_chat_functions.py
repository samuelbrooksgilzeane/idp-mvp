"""Create the document chat's read-only UC functions from a source-controlled SQL template.

SQL function bodies are stored with fixed object names, so they cannot use the parameter markers
the bootstrap's SQL tasks use. This task substitutes validated, backquoted names instead.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
OBJECT_PLACEHOLDER = re.compile(r"\{object:([a-z][a-z0-9_]*)\}")
TEMPLATE_NAME = "create_chat_functions.sql"


@dataclass(frozen=True)
class Parameters:
    catalog: str
    project_schema: str
    table_prefix: str
    sql_path: Path


def parse_arguments() -> Parameters:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--project-schema", required=True)
    parser.add_argument("--table-prefix", required=True)
    parser.add_argument("--sql-path", required=True)
    arguments = parser.parse_args()
    parameters = Parameters(
        catalog=arguments.catalog,
        project_schema=arguments.project_schema,
        table_prefix=arguments.table_prefix,
        sql_path=Path(arguments.sql_path),
    )
    validate_parameters(parameters)
    return parameters


def validate_parameters(parameters: Parameters) -> None:
    identifiers = (
        parameters.catalog,
        parameters.project_schema,
        parameters.table_prefix,
    )
    if any(SIMPLE_IDENTIFIER.fullmatch(value) is None for value in identifiers):
        raise ValueError(
            "Databricks object configuration contains an invalid identifier"
        )
    if parameters.sql_path.name != TEMPLATE_NAME:
        raise ValueError("Only the reviewed chat function template may be applied")


def render_statements(
    template: str, catalog: str, project_schema: str, table_prefix: str
) -> list[str]:
    """Return the template's statements with every {object:name} fully qualified and quoted."""

    def qualify(match: re.Match[str]) -> str:
        return f"`{catalog}`.`{project_schema}`.`{table_prefix}_{match.group(1)}`"

    lines = [
        line for line in template.splitlines() if not line.lstrip().startswith("--")
    ]
    rendered = OBJECT_PLACEHOLDER.sub(qualify, "\n".join(lines))
    if "{object:" in rendered:
        raise ValueError(
            "Chat function template contains a malformed object placeholder"
        )
    # Statements end with ";" at the end of a line; comment strings may contain ";" mid-line.
    statements = re.split(r";[ \t]*$", rendered, flags=re.MULTILINE)
    return [statement.strip() for statement in statements if statement.strip()]


def main() -> None:
    parameters = parse_arguments()
    statements = render_statements(
        parameters.sql_path.read_text(encoding="utf-8"),
        parameters.catalog,
        parameters.project_schema,
        parameters.table_prefix,
    )
    for statement in statements:
        spark.sql(statement)  # type: ignore[name-defined]  # noqa: F821 - Databricks injects Spark.
    print(f"Created {len(statements)} chat functions")


if __name__ == "__main__":
    main()
