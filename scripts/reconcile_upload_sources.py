"""Read-only operator audit; never deletes or automatically registers source PDFs."""

import argparse
import json

from idp_app.api.dependencies import build_document_service
from idp_app.core.config import IdpMode, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-workspace-reads",
        action="store_true",
        help="Explicitly allow volume listing and bounded registry SQL reads",
    )
    parser.add_argument("--max-objects", type=int, default=10000)
    args = parser.parse_args()
    settings = Settings()
    if settings.mode is IdpMode.DATABRICKS and not args.allow_workspace_reads:
        parser.error(
            "Databricks mode requires --allow-workspace-reads; no workspace calls made"
        )
    if args.max_objects < 1:
        parser.error("--max-objects must be positive")
    findings = build_document_service(settings).reconcile_sources(args.max_objects)
    print(json.dumps({"read_only": True, "findings": findings}, indent=2))


if __name__ == "__main__":
    main()
