"""Two bounded metadata reads; never starts compute or submits SQL, jobs or prompts."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any
from urllib.parse import urlsplit


def check(client: Any, host: str, warehouse: str) -> list[dict[str, Any]]:
    if client.config.host.rstrip("/") != host.rstrip("/"):
        raise ValueError(
            "Profile host does not match the explicitly selected workspace"
        )
    if not re.fullmatch(r"[a-zA-Z0-9-]+", warehouse):
        raise ValueError("Invalid warehouse ID")
    checks = [
        ("warehouse", f"/api/2.0/sql/warehouses/{warehouse}", {}),
        ("apps", "/api/2.0/apps", {"page_size": 5}),
    ]
    results = []
    for name, path, query in checks:
        try:
            value = client.api_client.do("GET", path, query=query)
            summary = (
                {"state": value.get("state")}
                if name == "warehouse"
                else {"first_page_count": len(value.get("apps", []))}
            )
            results.append({"check": name, "ok": True, **summary})
        except Exception as error:
            # No raw exception text: SDK messages may include configuration details.
            results.append(
                {"check": name, "ok": False, "error_type": type(error).__name__}
            )
            break
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="idp-mvp")
    parser.add_argument("--host", required=True)
    parser.add_argument("--warehouse", required=True)
    args = parser.parse_args()
    origin = urlsplit(args.host)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        parser.error("--host must be the plain HTTPS workspace origin")
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        client = WorkspaceClient(
            config=Config(
                profile=args.profile, http_timeout_seconds=15, retry_timeout_seconds=1
            )
        )
        result = check(client, args.host, args.warehouse)
    except Exception as error:
        result = [
            {
                "check": "authentication_or_configuration",
                "ok": False,
                "error_type": type(error).__name__,
            }
        ]
    print(
        json.dumps(
            {
                "checks": result,
                "sql_queries": 0,
                "job_submissions": 0,
                "ai_requests": 0,
            },
            indent=2,
        )
    )
    if any(not item["ok"] for item in result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
