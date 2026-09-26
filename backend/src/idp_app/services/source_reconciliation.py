from collections.abc import Iterable

from idp_app.services.document_registry import DocumentRegistry, document_page_cursor


def reconcile_sources(
    registry: DocumentRegistry, paths: Iterable[str], max_objects: int = 10000
) -> list[dict[str, str]]:
    """Report source/registry disagreements without changing either side."""
    available: set[str] = set()
    for path in paths:
        available.add(path)
        if len(available) > max_objects:
            raise ValueError("Source inventory exceeds the configured audit bound")
    known: set[str] = set()
    findings = []
    cursor = None
    while True:
        rows = registry.list_document_page(None, None, "", cursor, 100)
        for document in rows[:100]:
            known.add(document.source_path)
            if len(known) > max_objects:
                raise ValueError("Registry exceeds the configured audit bound")
            if document.source_path not in available:
                findings.append(
                    {
                        "kind": "MISSING_SOURCE",
                        "document_id": document.document_id,
                        "source_path": document.source_path,
                    }
                )
        if len(rows) <= 100:
            break
        cursor = document_page_cursor(rows[99], None, None, "")
    findings.extend(
        {"kind": "UNREGISTERED_SOURCE", "source_path": path} for path in sorted(available - known)
    )
    return findings
