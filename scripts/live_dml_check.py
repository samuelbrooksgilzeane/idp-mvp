"""Live check L2: the row-count result shape of UPDATE and MERGE on a SQL warehouse.

Batch 2 reads ``num_affected_rows`` / ``num_inserted_rows`` from DML results. This runs four
statements against one throwaway Delta table (create, MERGE, UPDATE, drop) and prints each
result's manifest columns and first row. No inference, jobs or document data. The warehouse starts
if stopped (a few minutes of 2X-Small compute), so run it once, deliberately:

    uv run --project backend python scripts/live_dml_check.py --host https://... \
        --warehouse ID --catalog workspace --schema idp_mvp --yes
"""

from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any

TABLE = "idp_scratch_dml_shape_check"


def run(client: Any, warehouse: str, statement: str) -> dict[str, Any]:
    from databricks.sdk.service import sql

    response = client.statement_execution.execute_statement(
        statement=statement, warehouse_id=warehouse, wait_timeout="50s",
        on_wait_timeout=sql.ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    while response.status and response.status.state in {
        sql.StatementState.PENDING, sql.StatementState.RUNNING
    }:
        time.sleep(1)
        response = client.statement_execution.get_statement(response.statement_id)
    state = response.status.state.value if response.status and response.status.state else None
    columns = (
        [column.name for column in response.manifest.schema.columns or []]
        if response.manifest and response.manifest.schema
        else None
    )
    rows = response.result.data_array if response.result else None
    error = response.status.error.message if response.status and response.status.error else None
    return {"state": state, "columns": columns, "first_row": rows[0] if rows else None,
            "error": error}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="idp-mvp")
    parser.add_argument("--host", required=True)
    parser.add_argument("--warehouse", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--yes", action="store_true", help="actually run the four statements")
    args = parser.parse_args()
    for value in (args.warehouse, args.catalog, args.schema):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            parser.error("Warehouse, catalog and schema must be simple identifiers")
    table = f"{args.catalog}.{args.schema}.{TABLE}"
    statements = [
        ("create", f"CREATE OR REPLACE TABLE {table} (id INT, v INT) USING DELTA"),
        ("merge_insert", f"MERGE INTO {table} t USING (SELECT 1 AS id, 1 AS v) s "
                         "ON t.id = s.id WHEN NOT MATCHED THEN INSERT *"),
        ("update", f"UPDATE {table} SET v = 2 WHERE id = 1 AND v = 1"),
        ("drop", f"DROP TABLE IF EXISTS {table}"),
    ]
    if not args.yes:
        print(json.dumps({"would_run": [s for _, s in statements]}, indent=2))
        return

    from workspace_auth import workspace_client  # sibling script module

    client = workspace_client(args.profile, args.host)
    results: dict[str, Any] = {}
    try:
        for name, statement in statements[:-1]:
            results[name] = run(client, args.warehouse, statement)
    finally:
        results["drop"] = run(client, args.warehouse, statements[-1][1])
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
